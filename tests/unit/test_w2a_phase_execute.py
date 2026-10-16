"""W2-A: the execute-phase driver (R9/R8) -- phases over the fake
benchmark.

Fake plugin through the real import machinery (the stub-finder pattern
of tests/unit/stages), fake model handles from llm.fake, phase specs
built directly (core.config's parsed shapes). The independent probes
are the plugin's call records, the FakeClient call counts (a rerun or a
reused unit makes zero model calls) and the phase directory's persisted
units / report rows.
"""

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import json
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
from expedistill.benchmarks.registry import ConfigError, _reset_registry, register_plugin
from expedistill.composites import phase_bank, shortfall, verdicts
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
)
from expedistill.core.contracts import AcceptanceReportRow
from expedistill.core.ids import (
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Subtask, Trajectory
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import json_script, make_fake_handle
from expedistill.methods.delivery import units as delivery_units
from expedistill.stages import phase_execute
from expedistill.stages.context import RunSettings, StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


class PhasePlugin(BenchmarkPlugin):
    """The fixed task set; ``behavior(record)`` decides each run's
    outcome (default success). The call records are the independent
    probe for order and resume assertions."""

    def __init__(self, behavior=None):
        self.behavior = behavior
        self.calls = []

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        if tasks_spec is None:
            ids = list(TASK_IDS)
        else:
            unknown = [i for i in tasks_spec if i not in TASK_IDS]
            if unknown:
                raise ConfigError(f"unknown task ids: {unknown}")
            ids = list(tasks_spec)
        return [Task(task_id=i, instruction=f"do {i}") for i in ids]

    def run(self, task, handle, skills_block, workdir):
        record = {"task_id": task.task_id, "handle": handle,
                  "skills_block": skills_block}
        self.calls.append(record)
        handle.client.complete(handle.name, "system", task.instruction)
        if self.behavior is None:
            return _outcome(task.task_id, True)
        return self.behavior(record)

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _outcome(task_id, correctness):
    return RunOutcome(
        steps=[{"action": f"exec {task_id}", "observation": "ok"}],
        correctness=correctness,
    )


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery."""
    fullname = f"expedistill.benchmarks.{plugin.name}"

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


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry()
    # The fake benchmark scores through the plain scorer rule (a real
    # benchmark's default binding is the config's frozen table).
    verdicts.BENCHMARK_VERDICT_RULES.setdefault(_FAKE, "scorer")
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.pop(_FAKE, None)


def make_config(**overrides):
    # A legal v4 method (the parse_phases checklist runs at construction,
    # so these ExperimentConfigs freeze the way a loaded one does). The
    # phases the tests RUN are constructed directly through the spec
    # helpers below; this sequence only has to be a legal method.
    defaults = dict(
        benchmark=_FAKE,
        mode="full",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1,
        n_attempts_weak=2,
        rules={},
        sequence=["D"],
        phases={"D": {"kind": "execute", "role": "baseline",
                      "tasks": "train", "delivery": {"name": "none"},
                      "execution": {"name": "real",
                                    "arms": ["executor_strong",
                                             "executor_weak"]}}},
    )
    defaults.update(overrides)
    return _config(**defaults)


def _config(**kwargs):
    from expedistill.core.config import ExperimentConfig
    return ExperimentConfig(**kwargs)


def make_handles(**overrides):
    handles = {
        "executor_strong": make_fake_handle("strong"),
        "executor_weak": make_fake_handle("weak"),
        "examiner": make_fake_handle("examiner"),
        "answerer": make_fake_handle("answerer"),
        "judge": make_fake_handle("judge"),
        "auditor": make_fake_handle("auditor"),
    }
    handles.update(overrides)
    return handles


def make_ctx(tmp_path, config, handles=None, run_name="run", **run_settings):
    run_dir = tmp_path / run_name
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles=handles if handles is not None else make_handles(),
        workdir_root=run_dir / "workdirs",
        run_settings=RunSettings(**run_settings),
    )


def _prefixed(task_id):
    return make_task_id(_FAKE, task_id)


def _pin_k1(monkeypatch):
    """K1 at one infra retry and zero backoff (the pre-resilience pin of
    the stage-1 tests) so failing-task tests stay fast and countable."""
    def injecting(real):
        def wrapper(**kwargs):
            kwargs.setdefault("infra_retries", 1)
            kwargs.setdefault("infra_backoff", (0.0, 0.0))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(phase_execute, "batch_execute",
                        injecting(phase_execute.batch_execute))


def baseline_spec(**overrides):
    spec = dict(role="baseline", tasks="train",
                delivery={"name": "none"},
                execution={"name": "real",
                           "arms": ["executor_strong", "executor_weak"]})
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def live_spec(**overrides):
    spec = dict(role="live", tasks="train",
                delivery={"name": "group_by_task"},
                execution={"name": "real", "n_attempts": 1},
                compare=CompareSpec(against="D"))
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def exam_spec(**overrides):
    spec = dict(role="exam", tasks="train",
                delivery={"name": "per_skill"},
                execution={"name": "exam"})
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def c_spec(**overrides):
    spec = dict(role="test", tasks="test",
                delivery={"name": "whole_bank"},
                execution={"name": "real"},
                compare=CompareSpec(against="D"))
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def phase_of(index, name, spec, task_ids=None):
    label = f"p{index + 1:02d}:{name}"
    return PhaseInstance(index=index, name=name, label=label, spec=spec,
                         task_ids=tuple(task_ids if task_ids is not None
                                        else TASK_IDS))


def make_group_skill(task_id, content=MARKED, version=1):
    subtask_id = make_subtask_id(
        make_trajectory_id(task_id, "strong", 0), 0)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id],
                 version=version)


def write_bank_artifact(ctx, label, skills):
    """A learn phase's bank artifact (W2-B's writer is not here yet; the
    loading side is phase_bank's frozen contract)."""
    directory = phase_bank.phase_bank_dir(ctx.run_dir, label)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / phase_bank.BANK_NAME, "w",
              encoding="utf-8") as handle:
        for skill in skills:
            handle.write(json.dumps(skill.to_dict(), ensure_ascii=False)
                         + "\n")


def report_rows(ctx, phase):
    path = (phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
            / phase_execute.REPORT_NAME)
    return [AcceptanceReportRow(**row) for row in read_jsonl(path)]


def snapshot_bank(ctx):
    """Every byte of the run's skill bank: the store's skill objects and
    the run-level bank ledger (R4's invariant is byte equality)."""
    skills_dir = ctx.store.dir_for("skills")
    digest = hashlib.sha256()
    for path in sorted(skills_dir.glob("*.json")):
        digest.update(path.read_bytes())
    bank_path = ctx.run_dir / "skill_bank.jsonl"
    if bank_path.exists():
        digest.update(bank_path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# The baseline phase (D)
# ---------------------------------------------------------------------------

def test_baseline_runs_both_arms_and_writes_the_report(monkeypatch,
                                                       tmp_path):
    plugin = _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    phase = phase_of(0, "D", baseline_spec())

    result = phase_execute.run(ctx, phase)

    assert result.label == "p01:D"
    assert result.summary["role"] == "baseline"
    # Strong once per task, weak twice per task (the frozen depths).
    strong_calls = [call for call in plugin.calls
                    if call["handle"].role == "strong"]
    weak_calls = [call for call in plugin.calls
                  if call["handle"].role == "weak"]
    assert len(strong_calls) == 3
    assert len(weak_calls) == 6
    rows = report_rows(ctx, phase)
    assert len(rows) == 6  # 3 tasks x 2 roles (D phases split by role)
    assert all(row.status == verdicts.STATUS_NORMAL for row in rows)
    assert all(row.valid_count == row.success_count for row in rows)
    # The summary and the report are both on disk under the phase dir.
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    assert (directory / "phase_summary.json").exists()
    units_rows = read_jsonl(directory / phase_execute.UNITS_NAME)
    assert len(units_rows) == 6
    # A rerun is a pure re-derivation: no new executions.
    calls_before = len(plugin.calls)
    phase_execute.run(ctx, phase)
    assert len(plugin.calls) == calls_before


def test_baseline_leaves_the_bank_unchanged_r4(monkeypatch, tmp_path):
    # R4's byte invariant holds for every phase that makes no bank calls.
    # (The live role's exception is the frozen verdicts-ledger protocol's
    # own commit -- judge_round/finalize_group, the coordinator's
    # 2026-10-16 ruling -- pinned by the live tests instead.)
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    before = snapshot_bank(ctx)

    phase_execute.run(ctx, phase_of(0, "D", baseline_spec()))
    skill = _exam_universe(ctx, tmp_path)
    write_bank_artifact(ctx, "p02:L", [skill])
    before = snapshot_bank(ctx)
    examiner, answerer, judge, auditor = _exam_script(bare_correct=True)
    ctx = make_ctx(tmp_path, make_config(),
                   handles=make_handles(examiner=examiner, answerer=answerer,
                                        judge=judge, auditor=auditor))
    phase_execute.run(ctx, phase_of(2, "A", exam_spec()))
    write_bank_artifact(ctx, "p04:L", [make_group_skill(_prefixed("t1"))])
    before = snapshot_bank(ctx)
    phase_execute.run(ctx, phase_of(4, "C", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS))

    assert snapshot_bank(ctx) == before


def test_r4_call_face():
    """R4's call face: the driver package contains no bank mutation
    calls (every module of it, not just the entry -- the split moved the
    roles into sibling files and the pin must follow them)."""
    package_dir = Path(phase_execute.__file__).parent
    sources = [path.read_text(encoding="utf-8")
               for path in sorted(package_dir.glob("*.py"))]
    assert sources
    for source in sources:
        assert "discard_skill(" not in source
        assert "accept_skill(" not in source


def test_redispatch_budget_then_exhausted_hole(monkeypatch, tmp_path):
    _pin_k1(monkeypatch)

    def behavior(record):
        if record["task_id"] == "t2":
            raise InfrastructureError("sandbox exploded")
        return _outcome(record["task_id"], True)

    plugin = _install_plugin(monkeypatch, PhasePlugin(behavior))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase = phase_of(0, "D", baseline_spec())

    with pytest.raises(InfrastructureError, match="infra_missing"):
        phase_execute.run(ctx, phase)

    # The fully-missing task has no report row yet (the frozen reader's
    # universe is the store plus the accepted record); the summary names
    # the hole and the raise stops the sequence.
    summary_path = (phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
                    / "phase_summary.json")
    assert read_json(summary_path)["infra_missing"] == [
        "traj/fakebench:t2/strong/00", "traj/fakebench:t2/weak/00"]
    state = read_json(phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
                      / "weak" / phase_execute.REDISPATCH_NAME)
    # The budget (default 1) was spent: one re-dispatch offer of the hole
    # (the state is keyed by unit id; the healthy units stay at zero).
    t2_passes = [entry["passes"] for unit_id, entry in state.items()
                 if "t2" in unit_id]
    assert t2_passes == [1]
    assert all(entry["passes"] == 0
               for unit_id, entry in state.items() if "t2" not in unit_id)
    # A resume without the flag re-raises without re-dispatching past the
    # budget; with the flag the holes are accepted durably and the phase
    # completes.
    calls_before = len(plugin.calls)
    with pytest.raises(InfrastructureError):
        phase_execute.run(ctx, phase)
    assert len(plugin.calls) == calls_before  # budget spent: no new calls

    accepting = StageContext(
        run_dir=ctx.run_dir, store=ctx.store, config=ctx.config,
        usage=ctx.usage, handles=ctx.handles,
        workdir_root=ctx.workdir_root,
        run_settings=RunSettings(allow_infra_shortfall=True))
    summary = phase_execute.run(accepting, phase).summary
    assert summary["infra_shortfall_accepted"] is True
    assert summary["complete"] is False  # the hole stays a hole
    accepted = read_json(shortfall.stage1_shortfall_path(
        ctx.run_dir))["unit_ids"]
    assert accepted == ["traj/fakebench:t2/strong/00",
                        "traj/fakebench:t2/weak/00"]
    # With the holes durably accepted the report row appears: the
    # accepted record widens the reader's universe, and every unit of
    # the task being a hole reads as all_holes.
    rows = {row.unit_id: row for row in report_rows(ctx, phase)}
    hole_row = next(row for row in rows.values()
                    if "t2" in row.unit_id and row.role == "weak")
    assert hole_row.status == verdicts.STATUS_NOT_TESTABLE
    assert hole_row.cause == verdicts.CAUSE_ALL_HOLES


def test_unscored_runs_report_all_holes(monkeypatch, tmp_path):
    # Every run comes back stored without a scorer verdict (R2b's normal
    # reachable state): the settled zero-measurement cause, all_holes --
    # never invalid_redispatch_exhausted, because nothing is missing and
    # no dispatch died. (A fresh sampling attempt would extend the depth
    # beyond the frozen n_attempts, a shape no frozen reader represents;
    # that channel is Wave 3's, disclosed in the wave report.)
    plugin = _install_plugin(
        monkeypatch, PhasePlugin(lambda record: _outcome(
            record["task_id"], None)))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase = phase_of(0, "D", baseline_spec(acceptance="scorer"))

    summary = phase_execute.run(ctx, phase).summary

    # Exactly the frozen depth ran -- one stored unscored sample, no
    # depth extension (the wave's disclosed boundary).
    weak_calls = [call for call in plugin.calls
                  if call["handle"].role == "weak"
                  and call["task_id"] == "t1"]
    assert len(weak_calls) == 1
    rows = {row.unit_id: row for row in report_rows(ctx, phase)}
    weak_row = next(row for row in rows.values()
                    if "t1" in row.unit_id and row.role == "weak")
    assert weak_row.status == verdicts.STATUS_NOT_TESTABLE
    assert weak_row.cause == verdicts.CAUSE_ALL_HOLES
    assert len(weak_row.runs) == 1
    assert weak_row.valid_count == 0
    assert summary["complete"] is True  # nothing is missing


# ---------------------------------------------------------------------------
# The live phase (B)
# ---------------------------------------------------------------------------

def _weak_baseline_behavior(record):
    """Strong succeeds; weak fails t1 (the grouped task) and succeeds the
    rest -- so t1 has headroom and the others are control-perfect."""
    if record["handle"].role == "strong":
        return _outcome(record["task_id"], True)
    return _outcome(record["task_id"], record["task_id"] != "t1")


def test_live_control_perfect_skips_the_group(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch,
                             PhasePlugin(_weak_baseline_behavior))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase_execute.run(ctx, phase_of(0, "D", baseline_spec()))
    # The group universe is the store's pending members (the same
    # derivation the frozen audit line carries).
    ctx.store.save(make_group_skill(_prefixed("t2")), "skills")
    calls_before = len(plugin.calls)

    phase = phase_of(2, "B", live_spec())
    result = phase_execute.run(ctx, phase)

    # D's weak arm was all-success on t2: the group is short-circuited
    # with zero executions -- through the ledger's weak_already_knows
    # terminal row, and the report row carries the cause.
    assert len(plugin.calls) == calls_before
    assert result.summary["skipped_control_perfect"] == [
        row.unit_id for row in report_rows(ctx, phase)]
    row = report_rows(ctx, phase)[0]
    assert row.status == verdicts.STATUS_NOT_RUN_CONTROL_PERFECT
    assert row.cause == verdicts.CAUSE_WEAK_ALREADY_KNOWS


def test_live_measures_then_reuses_across_phases(monkeypatch, tmp_path):
    # Weak fails everywhere (baseline AND live), so round 0 is a miss:
    # the group stays pending and the ledger keeps its round-0 row.
    plugin = _install_plugin(monkeypatch,
                             PhasePlugin(lambda record: _outcome(
                                 record["task_id"],
                                 record["handle"].role == "strong")))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase_execute.run(ctx, phase_of(0, "D", baseline_spec()))
    ctx.store.save(make_group_skill(_prefixed("t1")), "skills")

    first = phase_of(2, "B", live_spec())
    phase_execute.run(ctx, first)
    measured = report_rows(ctx, first)
    assert len(measured) == 1
    assert measured[0].status == verdicts.STATUS_NORMAL
    assert measured[0].success_count == 0
    calls_after_first = len(plugin.calls)
    # The ledger's round-0 row exists (the resume protocol's truth).
    ledger = read_jsonl(ctx.run_dir / "stage4_acceptance" / "live_run"
                        / "verdicts.jsonl")
    assert [row["round"] for row in ledger] == [0]
    assert ledger[0]["better"] is False

    # The second live phase measures round 1, but the group's block is
    # byte-identical (the revision produced no new version): rule 1
    # reuses the round-0 verdict -- zero executions, no round-1 row
    # (the no-op guard's phase-world shape).
    second = phase_of(4, "B", live_spec())
    result = phase_execute.run(ctx, second)

    assert len(plugin.calls) == calls_after_first
    assert result.summary["skipped_reused"] == [measured[0].unit_id.replace(
        first.label, second.label)]
    assert result.summary["round"] == 1
    ledger = read_jsonl(ctx.run_dir / "stage4_acceptance" / "live_run"
                        / "verdicts.jsonl")
    assert [row["round"] for row in ledger] == [0]
    reused = report_rows(ctx, second)
    assert len(reused) == 1
    assert reused[0].status == measured[0].status
    assert reused[0].valid_count == measured[0].valid_count
    assert reused[0].success_count == measured[0].success_count
    assert reused[0].phase_label == second.label


def test_live_injection_overflow_skips_the_group(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch,
                             PhasePlugin(_weak_baseline_behavior))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase_execute.run(ctx, phase_of(0, "D", baseline_spec()))
    ctx.store.save(make_group_skill(_prefixed("t1")), "skills")
    calls_before = len(plugin.calls)

    phase = phase_of(2, "B", live_spec(
        execution={"name": "real", "n_attempts": 1,
                   "max_injection_tokens": 1}))
    phase_execute.run(ctx, phase)

    assert len(plugin.calls) == calls_before
    row = report_rows(ctx, phase)[0]
    assert row.status == verdicts.STATUS_NOT_TESTABLE
    assert row.cause == verdicts.CAUSE_INJECTION_OVERFLOW


# ---------------------------------------------------------------------------
# The exam phase (A)
# ---------------------------------------------------------------------------

def _exam_universe(ctx, tmp_path):
    """One pending skill on t1 with its subtask and trajectory stored."""
    task_id = _prefixed("t1")
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(task_id, "strong", 0),
        task_id=task_id, benchmark=_FAKE, model_role="strong",
        model_name="big-model", attempt_index=0,
        steps=[Step(action="check", observation="down"),
               Step(action="restart", observation="ok")],
        correctness=True)
    ctx.store.save(trajectory, "trajectories")
    subtask = Subtask(
        subtask_id=make_subtask_id(trajectory.trajectory_id, 0),
        task_id=task_id, trajectory_id=trajectory.trajectory_id,
        source="strong", trajectory_success=True, segment_index=0,
        step_range=(0, 2), label="restart", description="restart steps",
        success=True)
    ctx.store.save(subtask, "subtasks")
    return make_group_skill(task_id)


def _exam_script(bare_correct, leaked=False, with_skill_correct=None):
    """(answerer, judge, auditor) handles for one exam unit."""
    examiner = make_fake_handle(
        "examiner", [json_script({
            "question_text": "The service rejects connections. What next?",
            "reference_answer": "Restart the service."})])
    answerer = make_fake_handle("weak", ["bare answer", "skilled answer"])
    verdicts_script = [
        json_script({"verdict": "correct" if bare_correct else "incorrect",
                     "error_description": "" if bare_correct else "vague"}),
    ]
    if not bare_correct and not leaked:
        verdicts_script.append(json_script({
            "verdict": ("correct" if (with_skill_correct is None or
                                      with_skill_correct)
                        else "incorrect"),
            "error_description": ""}))
    judge = make_fake_handle("judge", verdicts_script)
    auditor = make_fake_handle(
        "auditor", [] if bare_correct else
        [json_script({"leaked": leaked, "reason": "because"})])
    return examiner, answerer, judge, auditor


def test_exam_bare_correct_skips_the_with_skill_answer(monkeypatch,
                                                       tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    skill = _exam_universe(ctx, tmp_path)
    write_bank_artifact(ctx, "p02:L", [skill])
    examiner, answerer, judge, auditor = _exam_script(bare_correct=True)
    ctx = make_ctx(tmp_path, make_config(),
                   handles=make_handles(examiner=examiner, answerer=answerer,
                                        judge=judge, auditor=auditor))

    phase = phase_of(2, "A", exam_spec())
    result = phase_execute.run(ctx, phase).summary

    # The exam was generated at execution time and answered bare once;
    # the control was perfect, so the with-skill answer never ran.
    assert len(examiner.client.calls) == 1
    assert len(answerer.client.calls) == 1
    assert len(judge.client.calls) == 1
    assert result["control_perfect"] == [make_exam_id(skill.subtask_id)]
    rows = {row.unit_id: row for row in report_rows(ctx, phase)}
    bare_row = next(row for row in rows.values()
                    if row.injected_skills is None)
    skill_row = next(row for row in rows.values()
                     if row.injected_skills is not None)
    assert bare_row.status == verdicts.STATUS_NORMAL
    assert bare_row.success_count == 1
    assert skill_row.status == verdicts.STATUS_NOT_RUN_CONTROL_PERFECT
    assert skill_row.cause == verdicts.CAUSE_WEAK_ALREADY_KNOWS


def test_exam_measures_the_with_skill_answer(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    skill = _exam_universe(ctx, tmp_path)
    write_bank_artifact(ctx, "p02:L", [skill])
    examiner, answerer, judge, auditor = _exam_script(bare_correct=False)
    ctx = make_ctx(tmp_path, make_config(),
                   handles=make_handles(examiner=examiner, answerer=answerer,
                                        judge=judge, auditor=auditor))

    phase = phase_of(2, "A", exam_spec())
    result = phase_execute.run(ctx, phase).summary

    # Bare wrong -> leak audit (clean) -> with-skill answer -> judged.
    assert len(answerer.client.calls) == 2
    assert len(auditor.client.calls) == 1
    assert result["measured"] == [make_exam_id(skill.subtask_id)]
    rows = {row.unit_id: row for row in report_rows(ctx, phase)}
    skill_row = next(row for row in rows.values()
                     if row.injected_skills is not None)
    assert skill_row.status == verdicts.STATUS_NORMAL
    assert skill_row.success_count == 1


def test_exam_leaked_version_voids_the_measurement(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    skill = _exam_universe(ctx, tmp_path)
    write_bank_artifact(ctx, "p02:L", [skill])
    examiner, answerer, judge, auditor = _exam_script(bare_correct=False,
                                                      leaked=True)
    ctx = make_ctx(tmp_path, make_config(),
                   handles=make_handles(examiner=examiner, answerer=answerer,
                                        judge=judge, auditor=auditor))

    phase = phase_of(2, "A", exam_spec())
    result = phase_execute.run(ctx, phase).summary

    assert result["leaked"] == [make_exam_id(skill.subtask_id)]
    rows = {row.unit_id: row for row in report_rows(ctx, phase)}
    skill_row = next(row for row in rows.values()
                     if row.injected_skills is not None)
    assert skill_row.status == verdicts.STATUS_NOT_TESTABLE
    assert skill_row.cause == verdicts.CAUSE_LEAK
    # The leaked version never spent a with-skill answer (C1).
    assert len(answerer.client.calls) == 1


# ---------------------------------------------------------------------------
# The final-test phase (C)
# ---------------------------------------------------------------------------

def test_c_phase_reuses_the_d_control_and_computes_the_delta(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch,
                             PhasePlugin(_weak_baseline_behavior))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase_execute.run(ctx, phase_of(0, "D", baseline_spec()))
    skill = make_group_skill(_prefixed("t1"))
    write_bank_artifact(ctx, "p02:L", [skill])
    calls_before = len(plugin.calls)

    phase = phase_of(2, "C", c_spec(), task_ids=TASK_IDS)
    result = phase_execute.run(ctx, phase).summary

    # The with-bank arm executed; the control was reused from D's store
    # (zero new weak-bare calls beyond the with-bank arm's own). The
    # plugin's behavior keys on the role alone, so the weak with-bank
    # arm fails t1 exactly like D's weak bare arm did: the delta is an
    # honest zero over the shared denominators.
    with_bank_calls = [call for call in plugin.calls[calls_before:]]
    assert len(with_bank_calls) == 3
    assert result["control_source"] == "D_reuse"
    assert result["with_bank"]["tasks"] == result["control"]["tasks"] == 3
    assert result["delta"]["pass_at_n"] == pytest.approx(0.0)
    # C writes no acceptance report (section 3 rule 9).
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    assert not (directory / phase_execute.REPORT_NAME).exists()


def test_c_phase_fresh_bare_control_in_split_mode(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    config = make_config(mode="split", n_attempts_weak=1)
    ctx = make_ctx(tmp_path, config)
    skill = make_group_skill(_prefixed("t1"))
    write_bank_artifact(ctx, "p02:L", [skill])

    phase = phase_of(2, "C", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS)
    result = phase_execute.run(ctx, phase).summary

    assert result["control_source"] == "fresh_bare_run"
    # Both arms ran on the same task set: the delta is over identical
    # denominators, and both summaries cover every task.
    assert result["with_bank"]["tasks"] == result["control"]["tasks"] == 3
    assert result["delta"]["pass_at_n"] == pytest.approx(0.0)


def test_c_phase_reruns_every_time(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config(mode="split", n_attempts_weak=1))
    write_bank_artifact(ctx, "p02:L", [make_group_skill(_prefixed("t1"))])

    phase_execute.run(ctx, phase_of(2, "C", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS))
    calls_after_first = len(plugin.calls)
    phase_execute.run(ctx, phase_of(4, "C", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS))

    # No reuse across C phases: every unit re-ran (the frozen rule).
    assert len(plugin.calls) == 2 * calls_after_first


# ---------------------------------------------------------------------------
# Unit ids, params, the driver's guards
# ---------------------------------------------------------------------------

def test_unit_id_rebuild_matches_the_delivery_sha8():
    row = delivery_units.make_row(_prefixed("t1"),
                                  [{"skill_id": "s1", "version": 2}],
                                  "block bytes")
    full = verdicts.make_unit_id(phase_label="p03:B", benchmark=_FAKE,
                                 task_ref=row.task_ref, role="weak",
                                 skills_injected=row.skills_injected)
    sha8 = hashlib.sha256(verdicts.canonical_skills(
        row.skills_injected).encode("utf-8")).hexdigest()[:8]
    assert full == f"p03:B:{_FAKE}:{_prefixed('t1')}:weak:{sha8}"
    # The delivery-side id is exactly the join the rebuild consumes.
    assert row.unit_id == f"{_prefixed('t1')}:{sha8}"
    # The three raw shapes stay distinct keys (the frozen rule).
    none_row = delivery_units.make_row(_prefixed("t1"), None, "")
    empty_row = delivery_units.make_row(_prefixed("t1"), [], "")
    assert row.unit_id != none_row.unit_id != empty_row.unit_id


def test_run_rejects_a_learn_phase(tmp_path):
    ctx = make_ctx(tmp_path, make_config())
    phase = PhaseInstance(index=1, name="L", label="p02:L",
                          spec=LearnPhaseSpec(), task_ids=())
    with pytest.raises(ConfigError, match="learn-phase driver"):
        phase_execute.run(ctx, phase)


def test_execution_params_refuse_unknown_keys(tmp_path):
    ctx = make_ctx(tmp_path, make_config())
    bad = phase_of(2, "B", live_spec(
        execution={"name": "real", "n_attempts": 2, "surprise": 1}))
    with pytest.raises(ConfigError, match="surprise"):
        phase_execute.run(ctx, bad)
    arms_off_baseline = phase_of(
        2, "B", live_spec(execution={"name": "real", "n_attempts": 2,
                                     "arms": ["executor_weak"]}))
    with pytest.raises(ConfigError, match="arms"):
        phase_execute.run(ctx, arms_off_baseline)


def test_unknown_named_rule_fails_before_any_work(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    phase = phase_of(0, "D", baseline_spec(acceptance="no_such_rule"))
    with pytest.raises(ConfigError, match="unknown verdict rule"):
        phase_execute.run(ctx, phase)


# ---------------------------------------------------------------------------
# The TB named verdict rule (wiring list rule 3)
# ---------------------------------------------------------------------------

def test_tb_scorer_rule_consumes_the_verifier_facts():
    rule = verdicts.get_verdict_rule("tb_scorer")
    # A pseudo zero: a reward exists but the verifier never ran.
    verdict, basis = rule({"correctness": None,
                           "metrics": {"verifier_ran": False}})
    assert verdict == verdicts.RUN_INVALID
    assert "pseudo zero" in basis
    # The verifier's evidence never came back (the log was missing).
    verdict, basis = rule({"correctness": None,
                           "metrics": {"verifier_ran": None}})
    assert verdict == verdicts.RUN_INVALID
    assert "never came back" in basis
    # A run that died on a model output error.
    verdict, basis = rule({"correctness": None,
                           "metrics": {"exception_type":
                                       "ModelOutputError"}})
    assert verdict == verdicts.RUN_INVALID
    assert "ModelOutputError" in basis
    # A plain missing verdict (no facts at all).
    verdict, _ = rule({"correctness": None, "metrics": {}})
    assert verdict == verdicts.RUN_INVALID
    # The scorer's own answer decides when it exists.
    assert rule({"correctness": True, "metrics": {}})[0] == \
        verdicts.RUN_SUCCESS
    assert rule({"correctness": False, "metrics": {}})[0] == \
        verdicts.RUN_FAILURE
    # TB's default binding is the named rule.
    assert verdicts.default_verdict_rule("tb") == "tb_scorer"
