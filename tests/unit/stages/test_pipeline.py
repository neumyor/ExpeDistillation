"""Pipeline (layer 6) tests: end-to-end over a fake benchmark, no network.

The pipeline is exercised for real -- every stage module runs -- against:

* a stub benchmark plugin served through the real import machinery (the
  pattern of tests/unit/blocks/test_execute.py), whose runner returns canned
  RunOutcomes without touching the model;
* FakeClients injected through run_pipeline's ``make_client`` hook, scripted
  by prompt content: one (segment, skill, exam, answer, judge, similarity)
  response shape per B2 prompt, so the whole stage 2-5 model conversation is
  realistic enough to produce accepted skills and a non-empty bank.

Resume is asserted behaviorally: a second call on the same run directory must
issue ZERO new executions (no plugin.run calls, no model calls) and append a
fresh ledger line per stage.
"""

import importlib
import importlib.abc
import importlib.machinery
import json
import os
import socket
import sys
import types
from pathlib import Path

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import _reset_registry
from expedistill.blocks.author import render_skill_content, skill_markers
from expedistill.core.config import ROLE_DEFAULTS, ExperimentConfig, save_config
from expedistill.core.models import FAILURE_REFERENCE_MARKERS, PROCEDURE_MARKERS
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLockedError
from expedistill.infra.store import read_jsonl
from expedistill.llm.fake import FakeClient, json_script
from expedistill.stages.pipeline import STAGE_NAMES, ledger_path, run_pipeline

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


# ---------------------------------------------------------------------------
# The stub benchmark plugin
# ---------------------------------------------------------------------------

class FakePipelinePlugin(BenchmarkPlugin):
    """Two tasks; deterministic outcomes, no model, every call logged.

    Strong runs always succeed. Weak runs fail bare and succeed with a
    non-empty skills block, so with_bank strictly beats the control arm.
    """

    def __init__(self):
        self.calls = []

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        all_tasks = [Task(task_id="t1", instruction="do task one"),
                     Task(task_id="t2", instruction="do task two")]
        if tasks_spec is None:
            return all_tasks
        wanted = set(tasks_spec)
        return [task for task in all_tasks if task.task_id in wanted]

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({
            "task_id": task.task_id,
            "role": handle.role,
            "with_skills": bool(skills_block),
        })
        # The pipeline wires executor handles under their full role names
        # (ROLE_DEFAULTS anchors: "executor_strong" / "executor_weak" -- the same
        # convention BareT2InfraPlugin below already keys on). The strong
        # arm really must succeed: protocol v2's SEGMENT fake keys the
        # strong-arm segments on the trajectory's "final status: success"
        # view header, and D6/G2 distill material has to be successes.
        if handle.role == "executor_strong":
            status = "success"
        else:
            status = "success" if skills_block else "failure"
        return RunOutcome(
            steps=[{"action": "run", "observation": "the environment state"}],
            status=status,
        )

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"  # never rendered: run() makes no model call


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery."""
    fullname = _MODULE

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            from expedistill.benchmarks.registry import register_plugin
            register_plugin(plugin)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    sys.modules.pop(fullname, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    return plugin


@pytest.fixture
def plugin(monkeypatch):
    instance = _install_plugin(monkeypatch, FakePipelinePlugin())
    yield instance
    sys.modules.pop(_MODULE, None)
    _reset_registry()


# ---------------------------------------------------------------------------
# The scripted model side
# ---------------------------------------------------------------------------

#: One valid JSON-contract payload per G2 mode (protocol v2, decision
#: section 3.2): the fake answers must satisfy the same contract the real
#: model is held to, and the stored skill is the deterministic marker-text
#: rendering of these sections (asserted below).
_DISTILL_SECTIONS = {
    "when_to_use": "when a service reports an unexpected state",
    "steps": ["inspect the reported state",
              "run the command that matches the state"],
    "verify": "the service reports the expected state",
    "not_applicable_when": "no state was reported",
}
_DIAGNOSE_SECTIONS = {
    "trigger": "acting on a report without inspecting it first",
    "why_it_fails": "the action does not match the actual state.",
    "do_instead": ["inspect the reported state",
                   "run the command that matches the state"],
    "not_applicable_when": "the state was already inspected",
}


def _script(system: str, user: str) -> str:
    """One correct response per B2 prompt, dispatched by the prompt's text.

    The answerer is wrong without skills and right with them; the judge keys
    on the answer's own words, so bare answers fail acceptance and skilled
    answers pass (the K4 contract), and the leak audit stays trivially green
    because the exam carries no protected specifics.
    """
    if user.startswith("You are given the view"):
        # SEGMENT: one subtask covering the whole single-step trajectory.
        # Protocol v2 D6 material: the strong arm's segment succeeded
        # (procedure material), the weak arm's failed (diagnosis material).
        # In this fixture the trajectory's final status names the arm:
        # strong runs always succeed, weak bare runs always fail.
        success = "(final status: success)" in user
        return json_script([{
            "start": 0, "end": 1, "label": "whole",
            "description": "do the whole task", "success": success,
        }])
    if user.startswith("Write one exam question"):
        return json_script({
            "question_text": "A service reports an unexpected state and "
                             "stops. What should be done before anything "
                             "else, and why?",
            "reference_answer": "Inspect the reported state first, then "
                                "take the matching corrective action.",
        })
    if "The subtask SUCCEEDED" in user:
        # SKILL_DISTILL (protocol v2), dispatched by a phrase unique to the
        # distill template ("SUBTASK LABEL" is shared by both G2 prompts
        # and cannot discriminate). The response is the prompt's JSON
        # contract; blocks.author renders it into the stored marker text.
        return json_script(_DISTILL_SECTIONS)
    if "WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING" in user:
        # SKILL_DIAGNOSE (protocol v2), same anchor discipline.
        return json_script(_DIAGNOSE_SECTIONS)
    if user.startswith("Answer the question below."):
        # The D9 block header is the with/without-skill discriminator (an
        # empty block renders to nothing at all).
        if "## Skill rules" in user:
            return ("The skill applies. Inspect the state first, then act. "
                    "Final answer: inspect the state first.")
        return "I have no idea what should be done here."
    if user.startswith("Judge whether the skill below leaks"):
        # D8 leak audit: the skills this harness scripts are generic method
        # advice, so the judge never finds protected answer material in them
        # and every version passes the audit (the 4-skill bank).
        return json_script({"leaked": False, "reason": "generic advice"})
    if user.startswith("Grade an exam answer."):
        graded = user.split("ANSWER TO GRADE:", 1)[1]
        if "no idea" in graded:
            return json_script({"verdict": "incorrect",
                                "error_description": "No action is stated."})
        return json_script({"verdict": "correct", "error_description": ""})
    if user.startswith("A skill written for a weak model failed"):
        # G6 must keep the original's marker set (protocol v2): echo the
        # original content back, so the rewrite is marker-identical for
        # either skill kind. The happy path never reaches this branch
        # (every skilled answer passes on round 0); it stays for any flow
        # that does.
        original = user.split("SKILL (current version):\n", 1)[1].split(
            "\n\nEXAM QUESTION:", 1)[0]
        return json_script({
            "failure_category": "misread",
            "skill_text": original,
            "rewrite_reason": "Shorter sentences.",
        })
    if user.startswith("You are curating a bank of skills written for a "
                       "weak model. Find"):
        return json_script({"similar_groups": [], "conflicts": []})
    if user.startswith("You are curating a bank of skills written for a "
                       "weak model. Merge"):
        return "Inspect the state first. Then take the matching action."
    if user.startswith("You are curating a bank of skills written for a "
                       "weak model. Assign"):
        # G9 requires every listed skill id to appear in the mapping. The
        # listing lines are "- <skill_id>: <content>" and the ids themselves
        # contain colons, so the id is everything before the first ": ".
        # All scripted skills teach the same ability: one shared group name.
        ids = [line[2:].split(": ", 1)[0]
               for line in user.splitlines()
               if line.startswith("- ")]
        return json_script({"groups": {skill_id: "state inspection"
                                       for skill_id in ids}})
    raise AssertionError(f"unexpected prompt text:\n{user[:200]}")


class ClientLog:
    """Builds make_client hooks and remembers every client it handed out."""

    def __init__(self):
        self.clients = []

    def make_client(self, model_name: str) -> FakeClient:
        client = FakeClient(_script)
        self.clients.append(client)
        return client

    @property
    def call_count(self) -> int:
        return sum(len(client.calls) for client in self.clients)


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

def _write_config(path: Path, **overrides) -> Path:
    config = ExperimentConfig(
        benchmark=_FAKE,
        mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile="The weak model misses intermediate steps.",
        n_attempts_strong=1,
        n_attempts_weak=1,
        methods={"segmentation": {"K": 1}},
        # Protocol v2 rule names (any_weak_failed /
        # strong_success_weak_failed; strong2_weak2 keeps the v2 material
        # order for trajectory selection).
        rules={"task_select": "any_weak_failed",
               "segment_select": "strong_success_weak_failed",
               "trajectory_select": "strong2_weak2"},
        # The pipeline takes every handle's output budget from
        # cfg.max_output_tokens_for(role); every fixture config therefore
        # carries token_budgets (here: one uniform default).
        token_budgets={"default_max_output_tokens": 65536},
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    save_config(config, str(path))
    return path


@pytest.fixture
def config_path(tmp_path):
    return _write_config(tmp_path / "experiment.yaml")


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------

def test_pipeline_full_mode_end_to_end(plugin, config_path, tmp_path):
    run_dir = tmp_path / "run"
    log = ClientLog()

    result = run_pipeline(
        config_path=config_path, run_dir=run_dir, make_client=log.make_client)

    # -- stage 6 summary shape --------------------------------------------
    assert result["mode"] == "full"
    assert result["control_source"] == "stage1_reuse"
    assert result["bank_reference"].startswith("bank:")
    # Weak bare failed everything; weak with the bank succeeded everywhere.
    assert result["control"]["avg_at_n"] == 0.0
    assert result["with_bank"]["avg_at_n"] == 1.0
    assert result["delta"]["avg_at_n"] == 1.0

    # -- the ledger: one line per stage, in execution order ----------------
    lines = read_jsonl(ledger_path(run_dir))
    assert [row["stage"] for row in lines] == list(STAGE_NAMES)
    assert all(isinstance(row["summary"], dict) for row in lines)
    # Stage 0 always freezes all three split keys (ruling 7): a full-mode
    # run reserves nothing.
    assert lines[0]["summary"] == {"tasks": 2, "train": ["t1", "t2"],
                                  "test": ["t1", "t2"], "reserved": []}

    # -- the run layout the stages promised to fill -------------------------
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "skill_bank.jsonl").exists()
    # The bank holds the rendered marker text (protocol v2): two procedure
    # skills from the strong arms, two failure references from the weak
    # arms, each exactly the deterministic rendering of its fake payload.
    bank = read_jsonl(run_dir / "skill_bank.jsonl")
    assert len(bank) == 4
    marker_sets = [skill_markers(row["content"]) for row in bank]
    assert marker_sets.count(PROCEDURE_MARKERS) == 2
    assert marker_sets.count(FAILURE_REFERENCE_MARKERS) == 2
    by_subtask = {row["subtask_id"]: row["content"] for row in bank}
    assert by_subtask["traj/fakebench:t1/strong/00/seg00"] == \
        render_skill_content("distill", _DISTILL_SECTIONS)
    assert by_subtask["traj/fakebench:t1/weak/00/seg00"] == \
        render_skill_content("diagnose", _DIAGNOSE_SECTIONS)
    # 4 stage-1 executions (strong + weak bare) + 2 stage-6 with-bank runs.
    assert len(plugin.calls) == 6
    # Model calls: 4 segments + 4 skills + 4 exams + 4 leak audits
    # + 8 answers + 8 judgments + 1 similarity + 1 grouping (curation found
    # nothing to merge, but G9 still groups the bank).
    assert log.call_count == 34


def test_pipeline_multiline_steps_and_embedded_fences_end_to_end(
        plugin, config_path, tmp_path):
    # The 2026-10-13 incident classes in one end-to-end pass (docs/decisions/
    # g2_multiline_and_json_extraction_fix_20261014.md): a raw UNFENCED JSON
    # reply whose steps carry real newlines and whose values embed ``` code
    # fences. The fence-priority bug used to hijack it to a top-level array
    # and the newline rejection killed the multi-line steps; now the skill
    # must land in the bank with the correct marker set, heredoc
    # continuation lines at column 0.
    multiline_steps = [
        "inspect the reported state",
        "run this exact block:\n"
        "```\n"
        "python3 - <<'PY'\n"
        "print('ok')\n"
        "PY\n"
        "```",
    ]
    distill_sections = {**_DISTILL_SECTIONS, "steps": multiline_steps}

    def script(system: str, user: str) -> str:
        if "The subtask SUCCEEDED" in user:
            # Raw reply, exactly the incident shape: an unfenced JSON object
            # (no ```json wrapper) with code fences inside string values.
            return json.dumps(distill_sections, ensure_ascii=False)
        return _script(system, user)

    run_dir = tmp_path / "run"
    result = run_pipeline(
        config_path=config_path, run_dir=run_dir,
        make_client=lambda model_name: FakeClient(script))

    assert result["delta"]["avg_at_n"] == 1.0
    bank = read_jsonl(run_dir / "skill_bank.jsonl")
    assert len(bank) == 4
    marker_sets = [skill_markers(row["content"]) for row in bank]
    assert marker_sets.count(PROCEDURE_MARKERS) == 2
    assert marker_sets.count(FAILURE_REFERENCE_MARKERS) == 2
    by_subtask = {row["subtask_id"]: row["content"] for row in bank}
    strong = by_subtask["traj/fakebench:t1/strong/00/seg00"]
    assert strong == render_skill_content("distill", distill_sections)
    lines = strong.split("\n")
    # The heredoc continuation lines sit at column 0, verbatim.
    assert lines[3] == "2. run this exact block:"
    assert lines[4] == "```"
    assert lines[5] == "python3 - <<'PY'"
    assert lines[8] == "```"


def test_pipeline_rerun_makes_no_new_calls(plugin, config_path, tmp_path):
    run_dir = tmp_path / "run"
    first_log = ClientLog()
    first = run_pipeline(
        config_path=config_path, run_dir=run_dir, make_client=first_log.make_client)
    first_calls = list(plugin.calls)
    first_plugin_calls = len(plugin.calls)

    # A second call on the same run directory is a full resume: every stage
    # finds its units on disk and issues nothing.
    second_log = ClientLog()
    second = run_pipeline(
        config_path=config_path, run_dir=run_dir, make_client=second_log.make_client)

    assert plugin.calls[:first_plugin_calls] == first_calls
    assert len(plugin.calls) == first_plugin_calls
    assert second_log.call_count == 0
    assert second == first
    # The resume still appends one fresh ledger line per completed stage.
    lines = read_jsonl(ledger_path(run_dir))
    assert [row["stage"] for row in lines] == list(STAGE_NAMES) * 2


# ---------------------------------------------------------------------------
# Failure contracts
# ---------------------------------------------------------------------------

def test_locked_run_dir_raises_run_locked_error(plugin, config_path, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.lock").write_text(json.dumps({
        "pid": os.getpid(),  # alive by definition: this test process
        "hostname": socket.gethostname(),
        "acquired_at": "2026-10-10T00:00:00+00:00",
    }), encoding="utf-8")
    with pytest.raises(RunLockedError):
        run_pipeline(config_path=config_path, run_dir=run_dir)


def test_missing_role_is_config_error_naming_role_and_config(
        plugin, tmp_path):
    # An explicitly empty role survives config validation (the role table is
    # only default-filled, never pruned) and must be caught by the pipeline.
    config_path = tmp_path / "broken.yaml"
    config_path.write_text(
        "benchmark: fakebench\n"
        "schema_version: 3\n"
        "mode: full\n"
        "models:\n"
        "  executor_strong: strong-model\n"
        "  executor_weak: weak-model\n"
        "  judge:\n",
        encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        run_pipeline(
            config_path=config_path, run_dir=tmp_path / "run",
            make_client=lambda model_name: FakeClient(_script))
    assert "judge" in str(excinfo.value)
    assert str(config_path) in str(excinfo.value)


def test_invalid_config_is_config_error(plugin, tmp_path):
    # load_config's own ValueError (here: no executor_weak) is surfaced as a
    # ConfigError naming the config file, so the CLI prints it cleanly.
    config_path = tmp_path / "no_weak.yaml"
    config_path.write_text(
        "benchmark: fakebench\n"
        "mode: full\n"
        "models:\n"
        "  executor_strong: strong-model\n",
        encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        run_pipeline(
            config_path=config_path, run_dir=tmp_path / "run",
            make_client=lambda model_name: FakeClient(_script))
    assert str(config_path) in str(excinfo.value)


def test_one_client_per_unique_model(plugin, config_path, tmp_path):
    log = ClientLog()
    run_pipeline(
        config_path=config_path, run_dir=tmp_path / "run",
        make_client=log.make_client)
    # Nine roles, two distinct model names: two clients, both used.
    assert len(log.clients) == 2
    models_used = {client.calls[0]["model"] for client in log.clients
                   if client.calls}
    assert models_used == {"strong-model", "weak-model"}


# ---------------------------------------------------------------------------
# Per-model extra chat-call options (model_options, frozen into the manifest)
# ---------------------------------------------------------------------------

def test_build_handles_wires_extra_body_per_model(plugin, tmp_path):
    from expedistill.stages import pipeline

    config = ExperimentConfig(
        benchmark=_FAKE, mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model",
                "judge": "judge-model"},
        model_options={"strong-model": {"reasoning_effort": "max"},
                       "judge-model": {"top_p": 0.9}},
        token_budgets={"default_max_output_tokens": 65536},
    )
    log = ClientLog()
    handles = pipeline._build_handles(
        config, tmp_path / "experiment.yaml", log.make_client,
        infra_retries=1, infra_backoff_seconds=0.0)
    assert set(handles) == set(ROLE_DEFAULTS)
    # Options follow the MODEL NAME: every role on the strong model carries
    # the strong model's extras, an explicitly overridden role its own.
    for role in ROLE_DEFAULTS:
        expected = {"strong-model": {"reasoning_effort": "max"},
                   "judge-model": {"top_p": 0.9},
                   "weak-model": {}}[config.models[role]]
        assert handles[role].extra_body == expected, role
    # Unlisted models get a fresh private dict, not a shared default.
    assert handles["executor_weak"].extra_body is not handles["answerer"].extra_body


def test_model_options_reach_every_client_call(plugin, tmp_path):
    config_path = _write_config(
        tmp_path / "experiment.yaml",
        model_options={"strong-model": {"reasoning_effort": "max"}})
    log = ClientLog()
    run_pipeline(config_path=config_path, run_dir=tmp_path / "run",
                 make_client=log.make_client)
    calls = [call for client in log.clients for call in client.calls]
    assert calls
    for call in calls:
        if call["model"] == "strong-model":
            assert call["extra"] == {"reasoning_effort": "max"}
        else:
            assert call["extra"] is None
    # The options are part of the frozen identity: they are in the manifest
    # under the frozen config, like every other method parameter.
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(
        encoding="utf-8"))
    assert manifest["config"]["model_options"] == {
        "strong-model": {"reasoning_effort": "max"}}


# ---------------------------------------------------------------------------
# Output token budgets: cfg.max_output_tokens_for(role) -> handle.max_tokens
# ---------------------------------------------------------------------------

def test_build_handles_wires_output_budget_per_role(plugin, tmp_path):
    from expedistill.stages import pipeline

    # Per-role overrides fall back to default_max_output_tokens; the
    # handle's max_tokens is the only place the budget becomes live.
    config = ExperimentConfig(
        benchmark=_FAKE, mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        token_budgets={
            "default_max_output_tokens": 65536,
            "roles": {"judge": {"max_output_tokens": 1024},
                      "segmenter": {"max_output_tokens": 2048}},
        },
    )
    handles = pipeline._build_handles(
        config, tmp_path / "experiment.yaml", ClientLog().make_client,
        infra_retries=0, infra_backoff_seconds=0.0)
    assert set(handles) == set(ROLE_DEFAULTS)
    for role in ROLE_DEFAULTS:
        expected = {"judge": 1024, "segmenter": 2048}.get(role, 65536)
        assert handles[role].max_tokens == expected, role


def test_pipeline_requests_carry_the_budget_not_the_dead_default(
        plugin, config_path, tmp_path):
    # The handle field is the single live mechanism: the ModelHandle's
    # built-in 5120 never applies on the pipeline path; every request
    # carries the configured cap (the fixture config's default).
    log = ClientLog()
    run_pipeline(config_path=config_path, run_dir=tmp_path / "run",
                 make_client=log.make_client)
    calls = [call for client in log.clients for call in client.calls]
    assert calls
    assert all(call["max_tokens"] == 65536 for call in calls)


def test_config_without_token_budgets_stops_before_any_stage(
        plugin, tmp_path):
    # The token_budgets field is optional at load time, but the helper
    # fails closed; the pipeline surfaces that at handle-build time as an
    # infra ConfigError naming the config -- stage-0 era: no client built,
    # nothing frozen, no ledger line.
    config_path = tmp_path / "no_budgets.yaml"
    config_path.write_text(
        "benchmark: fakebench\n"
        "schema_version: 3\n"
        "mode: full\n"
        "models:\n"
        "  executor_strong: strong-model\n"
        "  executor_weak: weak-model\n",
        encoding="utf-8")
    log = ClientLog()
    run_dir = tmp_path / "run"
    with pytest.raises(ConfigError) as excinfo:
        run_pipeline(config_path=config_path, run_dir=run_dir,
                     make_client=log.make_client)
    assert "token_budgets" in str(excinfo.value)
    assert str(config_path) in str(excinfo.value)
    # The budget is resolved before any client is built, so a config
    # without budgets fails without touching the endpoint.
    assert log.clients == []
    assert plugin.calls == []
    assert not ledger_path(run_dir).exists()


# ---------------------------------------------------------------------------
# The default (real-endpoint) client path
# ---------------------------------------------------------------------------

class _StubSDK:
    """Stands in for openai.OpenAI (the tests/unit/llm/test_client.py
    pattern): records each construction and answers chat calls through the
    same prompt-dispatched ``_script`` the FakeClients use. No network."""

    instances = []

    def __init__(self, *, base_url, api_key, timeout, max_retries):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.models = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))
        _StubSDK.instances.append(self)

    def _create(self, *, model, messages, **kwargs):
        self.models.append(model)
        system = next((m["content"] for m in messages
                       if m["role"] == "system"), "")
        user = next(m["content"] for m in messages if m["role"] == "user")
        # The real client now opens every call as a stream (the 300s gateway
        # cutoff on non-streaming chat), so the stub answers with the chunk
        # stream an endpoint would emit for this content.
        delta = types.SimpleNamespace(content=_script(system, user),
                                      refusal=None)
        choice = types.SimpleNamespace(delta=delta, finish_reason="stop")
        return [types.SimpleNamespace(choices=[choice], usage=None)]


def test_default_client_builder_runs_end_to_end(
        plugin, config_path, tmp_path, monkeypatch):
    # Regression: the default builder used to call
    # OpenAICompatClient.from_env(model_name), a TypeError on the very first
    # real-endpoint run (every other test injects make_client).
    import openai

    from expedistill.llm.client import API_KEY_ENV, ENDPOINT_ENV

    monkeypatch.setenv(ENDPOINT_ENV, "http://dummy-endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "dummy-key")
    _StubSDK.instances = []
    monkeypatch.setattr(openai, "OpenAI", _StubSDK)

    result = run_pipeline(config_path=config_path, run_dir=tmp_path / "run")

    assert result["delta"]["avg_at_n"] == 1.0
    # One endpoint client per unique model name, all built from the env.
    assert len(_StubSDK.instances) == 2
    assert {sdk.base_url for sdk in _StubSDK.instances} == {
        "http://dummy-endpoint/v1"}
    assert all(sdk.max_retries == 0 for sdk in _StubSDK.instances)
    # The default client timeout stays 300 unless the run setting overrides.
    assert all(sdk.timeout == 300 for sdk in _StubSDK.instances)
    # Each client is only ever asked for its own model.
    assert sorted(set(sdk.models) for sdk in _StubSDK.instances
                  if sdk.models) == [{"strong-model"}, {"weak-model"}]


def test_default_client_builder_missing_env_is_config_error(
        plugin, config_path, tmp_path, monkeypatch):
    from expedistill.llm.client import API_KEY_ENV, ENDPOINT_ENV

    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ConfigError, match=ENDPOINT_ENV):
        run_pipeline(config_path=config_path, run_dir=tmp_path / "run")


def test_llm_timeout_sec_reaches_every_default_client(
        plugin, config_path, tmp_path, monkeypatch):
    import openai

    from expedistill.llm.client import API_KEY_ENV, ENDPOINT_ENV

    monkeypatch.setenv(ENDPOINT_ENV, "http://dummy-endpoint/v1")
    monkeypatch.setenv(API_KEY_ENV, "dummy-key")
    _StubSDK.instances = []
    monkeypatch.setattr(openai, "OpenAI", _StubSDK)

    run_pipeline(config_path=config_path, run_dir=tmp_path / "run",
                 llm_timeout_sec=120.5)

    # One client per unique model, each built with the run's timeout.
    assert len(_StubSDK.instances) == 2
    assert all(sdk.timeout == 120.5 for sdk in _StubSDK.instances)


# ---------------------------------------------------------------------------
# Per-call infra retry settings (operational, not frozen method parameters)
# ---------------------------------------------------------------------------

@pytest.fixture
def captured_handles(monkeypatch):
    """Record the role -> handle table every run_pipeline call builds."""
    from expedistill.stages import pipeline

    built = []
    real = pipeline._build_handles

    def recording(*args, **kwargs):
        handles = real(*args, **kwargs)
        built.append(handles)
        return handles

    monkeypatch.setattr(pipeline, "_build_handles", recording)
    return built


def test_default_handles_carry_infra_retry_defaults(
        plugin, config_path, tmp_path, captured_handles):
    run_pipeline(config_path=config_path, run_dir=tmp_path / "run",
                 make_client=ClientLog().make_client)
    (handles,) = captured_handles
    assert set(handles) == set(ROLE_DEFAULTS)
    for role, handle in handles.items():
        assert handle.infra_retries == 3, role
        assert handle.infra_backoff_seconds == 5.0, role


def test_cli_infra_flags_reach_every_handle(
        plugin, config_path, tmp_path, monkeypatch, captured_handles):
    import functools

    from expedistill import cli
    from expedistill.stages import pipeline

    log = ClientLog()
    monkeypatch.setattr(cli, "run_pipeline", functools.partial(
        pipeline.run_pipeline, make_client=log.make_client))
    code = cli.main([
        "run", "--config", str(config_path),
        "--run-dir", str(tmp_path / "run"),
        "--llm-infra-retries", "7", "--llm-infra-backoff", "0.25",
    ])
    assert code == 0
    (handles,) = captured_handles
    for role, handle in handles.items():
        assert handle.infra_retries == 7, role
        assert handle.infra_backoff_seconds == 0.25, role


def test_transient_infra_error_is_retried_and_run_completes(
        plugin, config_path, tmp_path, monkeypatch):
    from expedistill.infra.reliability import InfrastructureError
    from expedistill.llm import call as call_module

    waits = []
    monkeypatch.setattr(call_module, "_sleep", waits.append)
    failures = {"left": 1}

    def flaky(system, user):
        if failures["left"]:
            failures["left"] -= 1
            raise InfrastructureError("endpoint hiccup")
        return _script(system, user)

    clients = []

    def make_client(model_name):
        client = FakeClient(flaky)
        clients.append(client)
        return client

    result = run_pipeline(config_path=config_path, run_dir=tmp_path / "run",
                          make_client=make_client)

    assert result["delta"]["avg_at_n"] == 1.0
    assert failures["left"] == 0
    # The failed request was resent once after the first backoff step; the
    # rest of the conversation is the same 34 calls as a clean run.
    assert waits == [5.0]
    assert sum(len(client.calls) for client in clients) == 35


# ---------------------------------------------------------------------------
# Infra holes, the shortfall escape hatch, and the K1 dispatch stagger
# ---------------------------------------------------------------------------

class BareT2InfraPlugin(FakePipelinePlugin):
    """FakePipelinePlugin, except a bare weak run of t2 is always an infra
    error: K1 exhausts its retries and stage 1 is left with a hole."""

    def run(self, task, handle, skills_block, workdir):
        if (handle.role == "executor_weak" and task.task_id == "t2"
                and not skills_block):
            self.calls.append({"task_id": task.task_id, "role": handle.role,
                               "with_skills": False})
            return RunOutcome(status="infra_error", meta={"error": "sandbox"})
        return super().run(task, handle, skills_block, workdir)


@pytest.fixture
def infra_plugin(monkeypatch):
    instance = _install_plugin(monkeypatch, BareT2InfraPlugin())
    yield instance
    sys.modules.pop(_MODULE, None)
    _reset_registry()


@pytest.fixture
def k1_zero_backoff(monkeypatch):
    """Pin K1's infra-retry knobs at the pre-resilience values for the
    tests whose plugin burns real retries (t2 always infra-errors).

    The stages have no run setting for K1's retry knobs, so the pin goes
    through the call: a transparent wrapper around each stage's
    ``batch_execute`` injects ``infra_retries=1`` and a zero backoff.
    Without it the resilience defaults (2 retries, 15-45s random backoff)
    would sleep for real between t2's attempts and slow these tests by
    minutes; the pipeline's assertions are timing-agnostic either way.
    """
    from expedistill.stages import stage1_baseline, stage6_test

    def injecting(real):
        def wrapper(**kwargs):
            kwargs.setdefault("infra_retries", 1)
            kwargs.setdefault("infra_backoff", (0.0, 0.0))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(stage1_baseline, "batch_execute",
                        injecting(stage1_baseline.batch_execute))
    monkeypatch.setattr(stage6_test, "batch_execute",
                        injecting(stage6_test.batch_execute))


def test_stage1_infra_hole_stops_the_pipeline(
        infra_plugin, k1_zero_backoff, config_path, tmp_path):
    from expedistill.infra.reliability import InfrastructureError

    run_dir = tmp_path / "run"
    with pytest.raises(InfrastructureError, match="resume"):
        run_pipeline(config_path=config_path, run_dir=run_dir,
                     make_client=ClientLog().make_client)
    # Stage 1 never completed, so the pipeline did not move on.
    assert [row["stage"] for row in read_jsonl(ledger_path(run_dir))] == [
        "stage0_freeze"]
    summary = json.loads((run_dir / "stage1_summary.json").read_text())
    assert summary["complete"] is False


def test_allowed_infra_shortfall_completes_and_ledger_says_so(
        infra_plugin, k1_zero_backoff, config_path, tmp_path, capsys):
    run_dir = tmp_path / "run"
    result = run_pipeline(config_path=config_path, run_dir=run_dir,
                          make_client=ClientLog().make_client,
                          allow_infra_shortfall=True)
    # The stage-1 hole is the full-mode control's hole too; both arms still
    # cover both tasks.
    assert result["infra_shortfall_accepted"] is True
    assert result["control"]["tasks"] == result["with_bank"]["tasks"] == 2
    rows = {row["stage"]: row for row in read_jsonl(ledger_path(run_dir))}
    for stage in ("stage1_baseline", "stage6_test"):
        assert rows[stage]["infra_shortfall_accepted"] is True, stage
    assert "infra_shortfall_accepted" not in rows["stage2_segmentation"]
    err = capsys.readouterr().err
    assert "stage1_baseline" in err and "stage6_test" in err
    assert "infra shortfall" in err


def test_k1_stagger_reaches_every_k1_call(
        plugin, config_path, tmp_path, monkeypatch):
    from expedistill.stages import stage1_baseline, stage6_test

    seen = []

    def spy(real):
        def wrapper(**kwargs):
            seen.append(kwargs.get("dispatch_stagger", "missing"))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(stage1_baseline, "batch_execute",
                        spy(stage1_baseline.batch_execute))
    monkeypatch.setattr(stage6_test, "batch_execute",
                        spy(stage6_test.batch_execute))
    run_pipeline(config_path=config_path, run_dir=tmp_path / "a",
                 make_client=ClientLog().make_client)
    # Full mode: stage 1 strong + weak, stage 6 with-bank only.
    assert seen == [0.0, 0.0, 0.0]
    seen.clear()
    run_pipeline(config_path=config_path, run_dir=tmp_path / "b",
                 make_client=ClientLog().make_client, k1_stagger=0.05)
    assert seen == [0.05, 0.05, 0.05]


# ---------------------------------------------------------------------------
# Run settings are validated up front
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("setting, value", [
    ("workers", 0),
    ("workers", True),
    ("llm_infra_retries", -1),
    ("llm_infra_retries", 1.5),
    ("llm_infra_backoff_seconds", -0.5),
    ("llm_infra_backoff_seconds", float("nan")),
    ("llm_infra_backoff_seconds", float("inf")),
    ("llm_timeout_sec", 0),
    ("llm_timeout_sec", -30),
    ("llm_timeout_sec", float("nan")),
    ("llm_timeout_sec", float("inf")),
    ("llm_timeout_sec", True),
    ("llm_timeout_sec", "300"),
    ("k1_stagger", -1),
    ("k1_stagger", float("nan")),
    ("allow_infra_shortfall", "yes"),
    ("until_stage", -1),
    ("until_stage", 7),
    ("until_stage", 1.5),
    ("until_stage", True),
    ("until_stage", "x"),
])
def test_bad_run_setting_is_config_error_before_anything_runs(
        plugin, config_path, tmp_path, setting, value):
    run_dir = tmp_path / "run"
    log = ClientLog()
    with pytest.raises(ConfigError, match=setting):
        run_pipeline(config_path=config_path, run_dir=run_dir,
                     make_client=log.make_client, **{setting: value})
    # Nothing ran: no lock taken, no client built, no stage executed.
    assert not run_dir.exists()
    assert log.clients == []
    assert plugin.calls == []


def test_accepted_shortfall_survives_resume_without_flag(
        infra_plugin, k1_zero_backoff, config_path, tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    first = run_pipeline(config_path=config_path, run_dir=run_dir,
                         make_client=ClientLog().make_client,
                         allow_infra_shortfall=True)
    first_f = json.loads((run_dir / "stage1_summary.json").read_text())["F"]
    n_calls = len(infra_plugin.calls)

    # The endpoint recovers; the resume does not pass the flag.
    monkeypatch.setattr(BareT2InfraPlugin, "run", FakePipelinePlugin.run)
    second_log = ClientLog()
    second = run_pipeline(config_path=config_path, run_dir=run_dir,
                          make_client=second_log.make_client)

    assert len(infra_plugin.calls) == n_calls  # accepted holes not re-run
    assert second_log.call_count == 0
    assert second == first
    assert json.loads(
        (run_dir / "stage1_summary.json").read_text())["F"] == first_f


# ---------------------------------------------------------------------------
# until_stage: run the pipeline only through stage N
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("until", range(7))
def test_until_stage_stops_after_named_stage(
        plugin, config_path, tmp_path, until):
    run_dir = tmp_path / "run"
    result = run_pipeline(
        config_path=config_path, run_dir=run_dir,
        make_client=ClientLog().make_client, until_stage=until)

    # The ledger holds exactly stages 0..until, in execution order.
    lines = read_jsonl(ledger_path(run_dir))
    assert [row["stage"] for row in lines] == list(STAGE_NAMES[:until + 1])
    # The return value is the LAST EXECUTED stage's summary, not always
    # stage 6's.
    assert result == lines[-1]["summary"]
    assert lines[-1]["stage"] == STAGE_NAMES[until]


def test_until_stage_none_runs_all_stages(plugin, config_path, tmp_path):
    run_dir = tmp_path / "run"
    result = run_pipeline(
        config_path=config_path, run_dir=run_dir,
        make_client=ClientLog().make_client, until_stage=None)
    lines = read_jsonl(ledger_path(run_dir))
    assert [row["stage"] for row in lines] == list(STAGE_NAMES)
    # None keeps today's contract: the return value is stage 6's summary.
    assert result == lines[-1]["summary"]
    assert result["delta"]["avg_at_n"] == 1.0


def test_stopped_run_resumes_with_no_extra_calls_for_finished_stages(
        plugin, config_path, tmp_path):
    run_dir = tmp_path / "run"

    # Stop after stage 1: only the 4 stage-1 executions ran, and stages 0-1
    # made no model calls at all (every model call in this harness belongs
    # to stages 2-5).
    first_log = ClientLog()
    run_pipeline(
        config_path=config_path, run_dir=run_dir,
        make_client=first_log.make_client, until_stage=1)
    assert first_log.call_count == 0
    assert len(plugin.calls) == 4
    first_calls = list(plugin.calls)

    # Continue to the end: the finished stages 0-1 add nothing. The only new
    # executions are stage 6's two with-bank runs; the fresh model calls are
    # exactly the stages 2-5 conversation (the same 34 a clean run spends).
    second_log = ClientLog()
    result = run_pipeline(
        config_path=config_path, run_dir=run_dir,
        make_client=second_log.make_client, until_stage=6)
    assert result["delta"]["avg_at_n"] == 1.0
    assert plugin.calls[:4] == first_calls
    new_calls = plugin.calls[4:]
    assert len(new_calls) == 2
    assert all(call["with_skills"] for call in new_calls)
    assert second_log.call_count == 34
    # The resume re-ran stages 0-1 (finding everything finished) and then
    # 2-6: the ledger keeps the full history, one fresh line per stage.
    lines = read_jsonl(ledger_path(run_dir))
    assert [row["stage"] for row in lines] == \
        list(STAGE_NAMES[:2]) + list(STAGE_NAMES)

    # A third, complete run is again a full no-op resume.
    third_log = ClientLog()
    again = run_pipeline(
        config_path=config_path, run_dir=run_dir,
        make_client=third_log.make_client)
    assert third_log.call_count == 0
    assert len(plugin.calls) == 6
    assert again == result


def test_malformed_whole_bank_output_is_a_clean_cli_stop(
        plugin, config_path, tmp_path, monkeypatch, capsys):
    # G7 (find similar) sees the whole bank, so K5 lets its ModelOutputError
    # propagate once the repair budget is spent. The CLI must report it as a
    # one-line stop (exit 1, a human should look), not a traceback.
    import functools

    from expedistill import cli
    from expedistill.stages import pipeline

    def script(system, user):
        if user.startswith("You are curating a bank of skills written for "
                           "a weak model. Find"):
            return "this is not JSON at all"
        return _script(system, user)

    monkeypatch.setattr(cli, "run_pipeline", functools.partial(
        pipeline.run_pipeline,
        make_client=lambda model_name: FakeClient(script)))
    run_dir = tmp_path / "run"
    code = cli.main(["run", "--config", str(config_path),
                     "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert "malformed" in captured.err
    assert "repair budget" in captured.err
    assert "re-run the same command" in captured.err
    assert f"--run-dir {run_dir}" in captured.err
    assert "Traceback" not in captured.err
    # Stages 0-4 completed and stay on disk; stage 5 did not.
    stages = [row["stage"] for row in read_jsonl(ledger_path(run_dir))]
    assert stages == list(STAGE_NAMES[:5])


# ---------------------------------------------------------------------------
# live_run acceptance mode (decision doc 2026-10-14): the whole pipeline
# over a plugin whose weak arm only succeeds on the REVISED skill block, so
# every group walks round 0 (fail) -> G10/G11 -> round 1 (pass).
# ---------------------------------------------------------------------------

class _LivePipelinePlugin(FakePipelinePlugin):
    """The stub benchmark with live-round semantics: strong always
    succeeds; weak fails bare AND fails on the un-revised skill block, and
    only succeeds once the block carries the G11 revision marker -- which
    is what forces both rounds of every group."""

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({
            "task_id": task.task_id,
            "role": handle.role,
            "block": skills_block,
        })
        if handle.role == "executor_strong":
            status = "success"
        else:
            status = ("success" if "REVISED-BY-G11" in skills_block
                      else "failure")
        return RunOutcome(
            steps=[{"action": "run", "observation": "the environment state"}],
            status=status,
        )


def _install_live_plugin(monkeypatch, plugin):
    """Serve the live-semantics plugin under the same package name (the
    config keeps naming benchmark 'fakebench')."""
    from expedistill.benchmarks.registry import register_plugin
    fullname = _MODULE

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(plugin)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    sys.modules.pop(fullname, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    return plugin


def _live_script(system: str, user: str) -> str:
    """The exam pipeline's script plus the two live prompts: G10 revises
    every listed skill, G11 rewrites each one as its own text plus a
    marker line (the marker set survives; the bytes change, which is what
    makes round 1 a different injection)."""
    if user.startswith("You are the attribution step"):
        ids = [line.split("skill_id: ", 1)[1]
               for line in user.splitlines()
               if line.startswith("skill_id: ")]
        assert ids, "the G10 prompt listed no skills"
        return json_script({
            "diagnosis": "the skill's trigger is too vague to recognize",
            "skill_actions": [
                {"skill_id": skill_id, "action": "revise",
                 "problem": "the trigger condition is too vague to "
                            "recognize during real execution"}
                for skill_id in ids],
        })
    if user.startswith("A skill written for a weak model was injected"):
        original = user.split("SKILL (current version):\n", 1)[1].split(
            "\n\nATTRIBUTION REPORT", 1)[0]
        return json_script({
            "failure_category": "wrong_trigger",
            "skill_text": original + "\nREVISED-BY-G11: name the concrete "
                                     "state before acting.",
            "revision_reason": "made the trigger condition concrete",
        })
    return _script(system, user)


def _write_live_config(path: Path) -> Path:
    """full mode, weak depth 2, acceptance live_run at the same depth (the
    baseline units exist by id), strict_rate_gain as the criterion."""
    config = ExperimentConfig(
        benchmark=_FAKE,
        mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile="The weak model misses intermediate steps.",
        n_attempts_strong=1,
        n_attempts_weak=2,
        methods={
            "segmentation": {"K": 1},
            "acceptance": {"name": "live_run", "n_attempts": 2,
                           "improvement": "strict_rate_gain"},
        },
        rules={"task_select": "any_weak_failed",
               "segment_select": "strong_success_weak_failed",
               "trajectory_select": "strong2_weak2"},
        token_budgets={"default_max_output_tokens": 65536},
    )
    save_config(config, str(path))
    return path


def test_pipeline_live_run_mode_walks_both_rounds_end_to_end(
        monkeypatch, tmp_path):
    from expedistill.benchmarks.registry import _reset_registry
    from expedistill.core.models import Trajectory
    from expedistill.infra.store import Store
    from expedistill.prompts import PROMPTS, PromptSpec

    # The live stage renders the task prompt into the G10 input, so the
    # fake benchmark's task prompt must actually be registered.
    monkeypatch.setitem(PROMPTS, "FAKE_TASK", PromptSpec(
        name="FAKE_TASK",
        template="Solve the task: {instruction}\n{skills_block}\n{history}",
        description="live-run pipeline test task prompt"))
    plugin = _install_live_plugin(monkeypatch, _LivePipelinePlugin())
    config_path = _write_live_config(tmp_path / "live.yaml")
    run_dir = tmp_path / "run"
    try:
        result = run_pipeline(
            config_path=config_path, run_dir=run_dir,
            make_client=lambda model_name: FakeClient(_live_script))
    finally:
        sys.modules.pop(_MODULE, None)
        _reset_registry()

    # -- stage 6 still measures, against the LIVE-accepted (revised) bank:
    #    weak with the bank succeeds everywhere, the reused stage-1
    #    control failed everywhere.
    assert result["delta"]["avg_at_n"] == 1.0

    # -- the manifest froze the live protocol and its two prompts
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["config"]["methods"]["acceptance"] == {
        "name": "live_run", "n_attempts": 2,
        "improvement": "strict_rate_gain"}
    assert {"LIVE_RUN_ATTRIBUTION", "LIVE_SKILL_REVISION"} <= set(
        manifest["prompt_digests"])

    # -- the ledger: all seven stages, and stage 4's line is live
    lines = read_jsonl(ledger_path(run_dir))
    assert [row["stage"] for row in lines] == list(STAGE_NAMES)
    assert "infra_shortfall_accepted" not in lines[4]
    stage4 = lines[4]["summary"]
    assert stage4["mode"] == "live_run"
    assert stage4["improvement_rule"] == "strict_rate_gain"
    assert stage4["accepted"] == 6      # 3 candidates per task, 2 tasks
    assert stage4["discarded"] == {}
    assert stage4["infra_shortfall_accepted"] is False
    for task in ("t1", "t2"):
        detail = stage4["group_details"][f"{_FAKE}:{task}"]
        assert detail["round0"]["better"] is False
        assert detail["round1"]["better"] is True
        assert detail["outcome"] == "accepted"
        assert detail["discard_reasons"] == {}

    # -- both live rounds really executed: round 0 failed on the original
    #    blocks, round 1 succeeded on the revised ones, and the two
    #    rounds' frozen injections differ (the no-op guard had a say).
    live = run_dir / "stage4_acceptance" / "live_run"
    round00 = Store(live / "round00")
    round01 = Store(live / "round01")
    assert len(round00.ids("trajectories")) == 4
    assert len(round01.ids("trajectories")) == 4
    assert all(round00.load("trajectories", trajectory_id, Trajectory).status
               == "failure" for trajectory_id in round00.ids("trajectories"))
    assert all(round01.load("trajectories", trajectory_id, Trajectory).status
               == "success" for trajectory_id in round01.ids("trajectories"))
    audit0 = read_jsonl(live / "round00" / "injection_audit.jsonl")
    audit1 = read_jsonl(live / "round01" / "injection_audit.jsonl")
    assert {row["task_id"] for row in audit0} == {f"{_FAKE}:t1",
                                                 f"{_FAKE}:t2"}
    assert audit0[0]["injection_block_sha256"] != \
        audit1[0]["injection_block_sha256"]

    # -- the bank is the revised set: every member live-accepted at
    #    version 2 with the revision marker in its content.
    bank = read_jsonl(run_dir / "skill_bank.jsonl")
    assert len(bank) == 6
    assert all(row["version"] == 2 for row in bank)
    assert all("REVISED-BY-G11" in row["content"] for row in bank)

    # -- execution census: stage 1 (2 strong + 4 weak bare), live round 0
    #    (4, un-revised blocks), live round 1 (4, revised), stage 6
    #    with-bank (4, revised); the full-mode control reused stage 1.
    assert len(plugin.calls) == 18
    unrevised = [call for call in plugin.calls
                 if call["block"] and "REVISED-BY-G11" not in call["block"]]
    assert len(unrevised) == 4

    # -- resume: a second full pass over the same run directory is a pure
    #    replay -- zero executions, zero model calls (A10: the judged
    #    groups never re-enter a dispatch set), same stage-6 numbers.
    first_calls = len(plugin.calls)
    log2 = ClientLog()
    try:
        second = run_pipeline(
            config_path=config_path, run_dir=run_dir,
            make_client=log2.make_client)
    finally:
        sys.modules.pop(_MODULE, None)
        _reset_registry()
    assert len(plugin.calls) == first_calls
    assert log2.call_count == 0
    assert second == result
