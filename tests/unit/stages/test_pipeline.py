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
from expedistill.core.config import MODEL_ROLES, ExperimentConfig, save_config
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
        if handle.role == "strong":
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

def _script(system: str, user: str) -> str:
    """One correct response per B2 prompt, dispatched by the prompt's text.

    The answerer is wrong without skills and right with them; the judge keys
    on the answer's own words, so bare answers fail acceptance and skilled
    answers pass (the K4 contract), and the leak audit stays trivially green
    because the exam carries no protected specifics.
    """
    if user.startswith("You are given the view"):
        # SEGMENT: one subtask covering the whole single-step trajectory.
        return json_script([{
            "start": 0, "end": 1, "label": "whole",
            "description": "do the whole task", "success": False,
        }])
    if user.startswith("Write one exam question"):
        return json_script({
            "question_text": "A service reports an unexpected state and "
                             "stops. What should be done before anything "
                             "else, and why?",
            "reference_answer": "Inspect the reported state first, then "
                                "take the matching corrective action.",
        })
    if user.startswith("You are writing a skill for a weaker model, distilled"):
        return "Inspect the state first. Then take the matching action."
    if user.startswith("You are writing a skill for a weaker model, based on"):
        return "Inspect the state before acting. Then take the action that " \
               "matches the inspection."
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
        return json_script({
            "failure_category": "misread",
            "skill_text": "Inspect the state before acting. Then act.",
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
        K=1,
        rules={"task_select": "all_valid_failed",
               "segment_select": "strong_all_weak_failed",
               "trajectory_select": "all"},
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
    assert lines[0]["summary"]["train"] == ["t1", "t2"]

    # -- the run layout the stages promised to fill -------------------------
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "skill_bank.jsonl").exists()
    assert len(read_jsonl(run_dir / "skill_bank.jsonl")) == 4
    # 4 stage-1 executions (strong + weak bare) + 2 stage-6 with-bank runs.
    assert len(plugin.calls) == 6
    # Model calls: 4 segments + 4 skills + 4 exams + 4 leak audits
    # + 8 answers + 8 judgments + 1 similarity + 1 grouping (curation found
    # nothing to merge, but G9 still groups the bank).
    assert log.call_count == 34


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
    )
    log = ClientLog()
    handles = pipeline._build_handles(
        config, tmp_path / "experiment.yaml", log.make_client,
        infra_retries=1, infra_backoff_seconds=0.0)
    assert set(handles) == set(MODEL_ROLES)
    # Options follow the MODEL NAME: every role on the strong model carries
    # the strong model's extras, an explicitly overridden role its own.
    for role in MODEL_ROLES:
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
    assert set(handles) == set(MODEL_ROLES)
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
# Infra holes, the shortfall escape hatch, and the K1 circuit breaker
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


def test_stage1_infra_hole_stops_the_pipeline(
        infra_plugin, config_path, tmp_path):
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
        infra_plugin, config_path, tmp_path, capsys):
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


def test_infra_breaker_reaches_every_k1_call(
        plugin, config_path, tmp_path, monkeypatch):
    from expedistill.stages import stage1_baseline, stage6_test

    seen = []

    def spy(real):
        def wrapper(**kwargs):
            seen.append(kwargs.get("infra_breaker", "default"))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(stage1_baseline, "batch_execute",
                        spy(stage1_baseline.batch_execute))
    monkeypatch.setattr(stage6_test, "batch_execute",
                        spy(stage6_test.batch_execute))
    run_pipeline(config_path=config_path, run_dir=tmp_path / "a",
                 make_client=ClientLog().make_client)
    # Full mode: stage 1 strong + weak, stage 6 with-bank only.
    assert seen == [5, 5, 5]
    seen.clear()
    run_pipeline(config_path=config_path, run_dir=tmp_path / "b",
                 make_client=ClientLog().make_client, infra_breaker=2)
    assert seen == [2, 2, 2]


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
    ("infra_breaker", -1),
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
        infra_plugin, config_path, tmp_path, monkeypatch):
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
