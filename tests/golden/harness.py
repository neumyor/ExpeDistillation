"""Golden-matrix harness: the "behaviour unchanged" instrument of the
multi-method refactor (P-1 prime, docs/decisions/
multi_method_framework_20261015.md section 7).

What this pins. For every combination of the three mode axes -- acceptance
{exam, live_run} x delivery {whole_bank, retrieval} x experiment mode
{full, split} -- the whole pipeline runs over a stub benchmark with
FakeClients, and EVERY durable artifact of the run directory is snapshotted:
the manifest, stage summaries, object stores, ledgers, skill bank,
injection audit, llm request dumps. A refactor step passes P-0/P-1a only
if it reproduces these snapshots byte-for-byte (after the volatile-field
normalisation below).

Resume equivalence at artifact level. Each combo is also run with a
simulated crash (the client raises after a fraction of the straight run's
model calls -- a plain RuntimeError, so no stage retries it) and then
resumed with the same command; the final artifacts must equal the
straight run's snapshot. That is the crash-resume variant the decision
doc requires, checked where it matters: on disk.

Snapshot discipline (deliberate, not forbidden). The matrix is a
regression snapshot like any other: a step that deliberately changes run
artifacts regenerates it IN THE SAME COMMIT, and the diff is the review
artifact; a step that changes nothing must not touch it. Known limits,
by design: fake models only (no remote TB link, no real endpoints) --
those paths are validated by the live suite and the formal runs.

Normalisation is deliberately minimal and lives ONLY here (test code):
epoch ``time`` / ``duration_ms`` / uuid ``request_id`` values are blanked;
the run directory's absolute path is elided from any string;
``pipeline_log.jsonl`` is canonicalised to the last line per stage (its
documented reader semantics -- a resumed run legitimately appends fresh
lines). Nothing else may ever differ; a genuine post-crash difference is a
finding, not noise to hide.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.core.config import ExperimentConfig, save_config
from expedistill.llm.fake import FakeClient, json_script
from expedistill.stages.pipeline import run_pipeline

#: The stub benchmark name; served through the real import machinery so
#: every stage resolves it exactly like a checked-in plugin.
FAKE_BENCH = "fakebench"
_MODULE = f"expedistill.benchmarks.{FAKE_BENCH}"

#: Task instructions share vocabulary with the scripted skills so the
#: retrieval delivery has nonzero lexical overlap to rank (a bare "do task
#: one" scores zero against every skill and would degenerate the matrix's
#: retrieval combos into empty selections).
_INSTRUCTIONS = {
    "t1": "inspect the reported state of the service for task one",
    "t2": "inspect the reported state of the service for task two",
}

#: The combo axes of the matrix (decision doc section 7, P-1).
COMBOS: Tuple[Dict[str, str], ...] = tuple(
    {"acceptance": acceptance, "delivery": delivery, "mode": mode}
    for acceptance in ("exam", "live_run")
    for delivery in ("whole_bank", "retrieval")
    for mode in ("full", "split")
)


def combo_label(combo: Dict[str, str]) -> str:
    """The combo's file/id label, e.g. ``live_run-retrieval-split``."""
    return "-".join((combo["acceptance"], combo["delivery"], combo["mode"]))


# ---------------------------------------------------------------------------
# The stub benchmark plugins
# ---------------------------------------------------------------------------

class GoldenExamPlugin(BenchmarkPlugin):
    """Two tasks; strong always succeeds; weak fails bare and succeeds
    with any skills block (with_bank strictly beats the control arm)."""

    name = FAKE_BENCH

    def load_tasks(self, tasks_spec):
        tasks = [Task(task_id=task_id, instruction=_INSTRUCTIONS[task_id])
                 for task_id in ("t1", "t2")]
        if tasks_spec is None:
            return tasks
        wanted = set(tasks_spec)
        return [task for task in tasks if task.task_id in wanted]

    def run(self, task, handle, skills_block, workdir):
        status = ("success" if handle.role == "executor_strong"
                  else ("success" if skills_block else "failure"))
        return RunOutcome(
            steps=[{"action": "run",
                    "observation": "the environment state"}],
            status=status,
        )

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"  # never rendered: run() makes no model call


class GoldenLivePlugin(GoldenExamPlugin):
    """The exam plugin with live-round semantics: the weak arm also fails
    on the UN-revised skill block and only succeeds once the block carries
    the G11 revision marker -- which forces every group to walk round 0
    (fail) -> G10/G11 -> round 1 (pass)."""

    def run(self, task, handle, skills_block, workdir):
        if handle.role == "executor_strong":
            status = "success"
        else:
            status = ("success" if "REVISED-BY-G11" in skills_block
                      else "failure")
        return RunOutcome(
            steps=[{"action": "run",
                    "observation": "the environment state"}],
            status=status,
        )


def plugin_for(combo: Dict[str, str]) -> BenchmarkPlugin:
    return GoldenLivePlugin() if combo["acceptance"] == "live_run" \
        else GoldenExamPlugin()


@contextmanager
def installed_plugin(plugin: BenchmarkPlugin) -> Iterator[BenchmarkPlugin]:
    """Serve ``plugin`` under the stub package name through the real
    import machinery (registration happens at import, exactly like a
    checked-in plugin), and register the stub's task prompt -- the live
    stage renders it into the G10 input, so unlike the unit-test stubs the
    golden matrix's FAKE_TASK must be a real B2 entry (mirrors what every
    checked-in plugin does). Usable under pytest and standalone."""
    from expedistill.benchmarks.registry import _reset_registry, register_plugin
    from expedistill.prompts import PROMPTS, PromptSpec

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(plugin)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == _MODULE:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    sys.modules.pop(_MODULE, None)
    finder = Finder()
    sys.meta_path.insert(0, finder)
    previous_prompt = PROMPTS.get("FAKE_TASK")
    PROMPTS["FAKE_TASK"] = PromptSpec(
        name="FAKE_TASK",
        template="Solve the task: {instruction}\n{skills_block}\n{history}",
        description="golden-matrix stub task prompt")
    try:
        yield plugin
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(_MODULE, None)
        if previous_prompt is None:
            PROMPTS.pop("FAKE_TASK", None)
        else:
            PROMPTS["FAKE_TASK"] = previous_prompt
        _reset_registry()


# ---------------------------------------------------------------------------
# The scripted model side (self-contained on purpose: the golden must not
# drift when tests/unit/stages/test_pipeline.py's fixtures evolve)
# ---------------------------------------------------------------------------

#: One valid JSON-contract payload per G2 mode (protocol v2): the fake
#: answers satisfy the same contract the real model is held to.
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
    """One correct response per B2 prompt, dispatched by prompt text.

    Covers the exam conversation (segment, exam gen, distill/diagnose,
    answer, leak audit, judge, rewrite, curate) plus the two live prompts
    (G10 revises every listed skill; G11 appends a marker line so round 1
    injects different bytes -- the no-op guard must not skip it).
    """
    # -- live G10 / G11 come first: their anchors are live-only --------
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
    # -- the exam conversation ----------------------------------------
    if user.startswith("You are given the view"):
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
        return json_script(_DISTILL_SECTIONS)
    if "WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING" in user:
        return json_script(_DIAGNOSE_SECTIONS)
    if user.startswith("Answer the question below."):
        if "## Skill rules" in user:
            return ("The skill applies. Inspect the state first, then act. "
                    "Final answer: inspect the state first.")
        return "I have no idea what should be done here."
    if user.startswith("Judge whether the skill below leaks"):
        return json_script({"leaked": False, "reason": "generic advice"})
    if user.startswith("Grade an exam answer."):
        graded = user.split("ANSWER TO GRADE:", 1)[1]
        if "no idea" in graded:
            return json_script({"verdict": "incorrect",
                                "error_description": "No action is stated."})
        return json_script({"verdict": "correct", "error_description": ""})
    if user.startswith("A skill written for a weak model failed"):
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
        ids = [line[2:].split(": ", 1)[0]
               for line in user.splitlines()
               if line.startswith("- ")]
        return json_script({"groups": {skill_id: "state inspection"
                                       for skill_id in ids}})
    raise AssertionError(f"unexpected prompt text:\n{user[:200]}")


class _ClientLog:
    """make_client hook handing out FakeClients; counts every call."""

    def __init__(self):
        self.clients: List[FakeClient] = []

    def make_client(self, model_name: str) -> FakeClient:
        client = FakeClient(_script)
        self.clients.append(client)
        return client

    @property
    def call_count(self) -> int:
        return sum(len(client.calls) for client in self.clients)


class SimulatedCrash(RuntimeError):
    """Raised by the crash-injection client before the model call is sent.

    A plain RuntimeError on purpose: no stage retries it and no disposition
    swallows it (only InfrastructureError is retried), so it escapes
    run_pipeline exactly like a process death, and the harness resumes.
    """


class CrashAfter:
    """make_client hook crashing before the ``n``-th model call overall."""

    def __init__(self, n: int):
        self.n = n
        self.seen = 0

    def make_client(self, model_name: str) -> Any:
        budget = self

        class _CrashingClient:
            def __init__(self):
                self.inner = FakeClient(_script)

            @property
            def calls(self):
                return self.inner.calls

            def complete(self, *args, **kwargs):
                budget.seen += 1
                if budget.seen >= budget.n:
                    raise SimulatedCrash(
                        f"simulated crash before model call #{budget.seen}")
                return self.inner.complete(*args, **kwargs)

        return _CrashingClient()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def write_config(path: Path, combo: Dict[str, str]) -> Path:
    """Write the combo's experiment config (protocol v2 rule names,
    live_run at weak depth 2 / exam at depth 1, retrieval's top-1 gate)."""
    acceptance = combo["acceptance"]
    kwargs: Dict[str, Any] = dict(
        benchmark=FAKE_BENCH,
        mode=combo["mode"],
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile="The weak model misses intermediate steps.",
        n_attempts_strong=1,
        n_attempts_weak=2 if acceptance == "live_run" else 1,
        K=1,
        rules={"task_select": "any_weak_failed",
               "segment_select": "strong_success_weak_failed",
               "trajectory_select": "strong2_weak2"},
        token_budgets={"default_max_output_tokens": 65536},
    )
    if acceptance == "live_run":
        kwargs["acceptance"] = {"mode": "live_run", "n_attempts": 2,
                                "improvement": "strict_rate_gain"}
    if combo["delivery"] == "retrieval":
        kwargs["skill_delivery"] = {"mode": "retrieval", "top_k": 1,
                                    "max_categories": 1, "min_score": 0.02}
    if combo["mode"] == "split":
        kwargs["split"] = {"train": ["t1"], "test": ["t2"]}
    save_config(ExperimentConfig(**kwargs), str(path))
    return path


# ---------------------------------------------------------------------------
# Running and snapshotting
# ---------------------------------------------------------------------------

def run_straight(config_path: Path, run_dir: Path) -> int:
    """One uninterrupted pipeline run; returns the model call count."""
    log = _ClientLog()
    run_pipeline(config_path=Path(config_path), run_dir=Path(run_dir),
                 make_client=log.make_client)
    return log.call_count


def run_crash_and_resume(config_path: Path, run_dir: Path,
                         crash_at: int) -> None:
    """Crash before call ``crash_at``, then resume with the same command."""
    budget = CrashAfter(crash_at)
    try:
        run_pipeline(config_path=Path(config_path), run_dir=Path(run_dir),
                     make_client=budget.make_client)
        raise AssertionError(
            f"crash point {crash_at} never fired ({budget.seen} calls seen)")
    except SimulatedCrash:
        pass
    run_straight(config_path, run_dir)


#: Values that are blanked wherever these KEYS appear (module docstring:
#: epoch seconds, wall-clock durations, uuid request ids, and the one
#: per-invocation bookkeeping counter the stage summaries carry --
#: ``processed_this_call`` measures the work of ONE pipeline call, which
#: a crash+resume legitimately splits across calls).
_VOLATILE_KEYS = {"time": "<time>", "duration_ms": "<duration_ms>",
                  "request_id": "<request_id>",
                  "processed_this_call": "<processed_this_call>"}

#: The progress ledger, canonicalised to its documented reader view (the
#: last line per stage) because a resumed run legitimately appends lines.
_LEDGER = "pipeline_log.jsonl"

#: The request history (llm request dumps and the usage ledger) is
#: chronological CALL LOG, not result state: a crash+resume re-asks the
#: interrupted call, so sequence numbers and row order legitimately shift
#: by one while the multiset of calls stays identical. Both are snapshotted
#: as a sorted multiset (every dump's content is still pinned byte-for-byte
#: after volatile-field normalisation; a missing, extra or duplicated call
#: is a multiset difference and fails the matrix).
_USAGE = "llm_requests/usage.jsonl"
_REQUESTS_DIR = "llm_requests/"


def _normalize(value: Any, run_dir: Path) -> Any:
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if key in _VOLATILE_KEYS:
                normalized[key] = _VOLATILE_KEYS[key]
            else:
                normalized[key] = _normalize(item, run_dir)
        return normalized
    if isinstance(value, list):
        return [_normalize(item, run_dir) for item in value]
    if isinstance(value, str):
        prefix = str(run_dir)
        return value.replace(prefix, "<RUN_DIR>") if prefix in value else value
    return value


def snapshot(run_dir: Path) -> Dict[str, Any]:
    """The normalised artifact snapshot: every file under ``run_dir``
    except the run lock, keyed by POSIX-relative path. A missing or extra
    file is a snapshot difference (dict equality catches both)."""
    run_dir = Path(run_dir)
    out: Dict[str, Any] = {}
    request_dumps: List[Dict[str, Any]] = []
    usage_rows: List[Dict[str, Any]] = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(run_dir).as_posix()
        if rel == "run.lock":
            continue
        text = path.read_text(encoding="utf-8")
        if rel == _USAGE:
            usage_rows = _normalize(
                [json.loads(line) for line in text.splitlines()
                 if line.strip()], run_dir)
        elif rel.startswith(_REQUESTS_DIR) and path.suffix == ".json":
            # The dump's "seq" is its chronological position -- call
            # history, not content; the sorted multiset below carries it.
            dump = json.loads(text)
            dump.pop("seq", None)
            request_dumps.append(_normalize(dump, run_dir))
        elif path.suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines()
                    if line.strip()]
            if rel == _LEDGER:
                last = {}
                for row in rows:
                    last[row["stage"]] = row  # the documented reader view
                rows = [last[stage] for stage in sorted(last)]
            out[rel] = _normalize(rows, run_dir)
        elif path.suffix == ".json":
            out[rel] = _normalize(json.loads(text), run_dir)
        else:
            out[rel] = {"__sha256__": hashlib.sha256(
                text.encode("utf-8")).hexdigest()}
    if usage_rows or (run_dir / _USAGE).exists():
        out[_USAGE] = sorted(usage_rows, key=_canonical)
    if request_dumps:
        out[_REQUESTS_DIR + "dumps"] = sorted(request_dumps, key=_canonical)
    return out


def _canonical(value: Any) -> str:
    """A sort key turning a normalised record into its canonical JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _is_crash_row(record: Dict[str, Any]) -> bool:
    """True for the simulated crash's own bookkeeping rows (the harness's
    in-process crash gets recorded as a failed attempt; a real process
    death would record nothing). Usage rows carry the error class under
    ``meta.error``; request dumps carry it at the top level."""
    return (record.get("error") == SimulatedCrash.__name__
            or (record.get("meta") or {}).get("error")
            == SimulatedCrash.__name__)


def assert_resume_equivalent(golden: Dict[str, Any],
                             resumed: Dict[str, Any]) -> None:
    """The crash+resume comparator (module docstring).

    Result state -- every snapshot key except the request history -- must
    be identical. The request history may differ only by (a) the
    simulated crash's own error rows and (b) DUPLICATED re-asks of calls
    whose results were never persisted (README's documented resume
    semantics: a pair-written unit loses both halves). A missing call or
    a NOVEL call after resume is a failure.
    """
    history = (_USAGE, _REQUESTS_DIR + "dumps")
    for key in sorted(set(golden) | set(resumed)):
        if key in history:
            continue
        if golden.get(key) != resumed.get(key):
            raise AssertionError(f"resume inequivalence in {key}")
    for key in history:
        want = [row for row in golden.get(key, []) if not _is_crash_row(row)]
        got = [row for row in resumed.get(key, []) if not _is_crash_row(row)]
        missing = list(want)
        extras = []
        for row in got:
            try:
                missing.remove(row)
            except ValueError:
                extras.append(row)
        if missing:
            raise AssertionError(
                f"{key}: {len(missing)} call(s) missing after resume")
        known = {_canonical(row) for row in want}
        for row in extras:
            if _canonical(row) not in known:
                raise AssertionError(
                    f"{key}: novel call after resume: "
                    f"{_canonical(row)[:200]}")
