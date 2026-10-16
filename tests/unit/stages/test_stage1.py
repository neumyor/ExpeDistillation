"""Stage 1 unit tests: both arms, F selection, infra shortfalls, resume.

Fake plugin registered through the real registry/import machinery (the stub
finder pattern of tests/unit/blocks/test_execute.py) and fake model handles
from llm.fake: the FakeClient call count is the independent proof that a
rerun re-aggregates without re-executing anything."""

import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import (
    ConfigError,
    _reset_registry,
    register_plugin,
)
from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import make_task_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import (
    InfrastructureError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import make_fake_handle
from expedistill.stages import stage0_freeze, stage1_baseline
from dataclasses import replace
from expedistill.stages.context import RunSettings, StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")


class StagePlugin(BenchmarkPlugin):
    """Fake benchmark serving the fixed task set; ``behavior(record)`` decides
    each execution's outcome (default: success), keyed off the record the
    runner would see (task id and the working handle, whose ``role`` names
    the arm)."""

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
        record = {"task_id": task.task_id, "handle": handle}
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


def _pin_k1_infra(monkeypatch):
    """Run K1 at the pre-resilience retry settings (one infra retry, zero
    backoff) in the tests whose t2 always infra-errors.

    The stage has no run setting for K1's retry knobs, so the pin goes
    through the call: a transparent wrapper around the stage's
    ``batch_execute`` injects the values these tests were written against
    (initial + one retry). Without it K1's resilience defaults (2 retries,
    15-45s random backoff) would both change the plugin call counts and
    sleep for real between t2's attempts, slowing the suite by minutes.
    """
    def injecting(real):
        def wrapper(**kwargs):
            kwargs.setdefault("infra_retries", 1)
            kwargs.setdefault("infra_backoff", (0.0, 0.0))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(stage1_baseline, "batch_execute",
                        injecting(stage1_baseline.batch_execute))


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery
    (same contract as tests/unit/blocks/test_execute.py)."""
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
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()


def make_config(**overrides):
    defaults = dict(
        benchmark=_FAKE,
        mode="full",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1,
        n_attempts_weak=2,
        methods={"segmentation": {"K": 4}},
        rules={"task_select": "any_weak_failed", "trajectory_select": "all"},
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_handles():
    return {
        "executor_strong": make_fake_handle("strong"),
        "executor_weak": make_fake_handle("weak"),
    }


def make_ctx(tmp_path, config, handles=None, run_name="run"):
    run_dir = tmp_path / run_name
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles=handles if handles is not None else make_handles(),
        workdir_root=run_dir / "workdirs",
    )


def _prefixed(task_id):
    return make_task_id(_FAKE, task_id)


# ---------------------------------------------------------------------------
# Both arms, metrics, F
# ---------------------------------------------------------------------------

def test_both_arms_run_and_F_is_any_weak_failed(monkeypatch, tmp_path):
    def behavior(record):
        if record["handle"].role == "strong":
            return _outcome(record["task_id"], True)
        if record["task_id"] == "t3":
            # A mixed task: the first weak attempt fails, the second
            # succeeds. plugin.calls already holds this call, so 1 means
            # attempt 0 (the failure).
            t3_calls = sum(1 for call in plugin.calls
                           if call["task_id"] == "t3"
                           and call["handle"] is record["handle"])
            return _outcome("t3", t3_calls != 1)
        # Weak arm: t1 and t2 fail every attempt.
        return _outcome(record["task_id"], False)

    plugin = _install_plugin(monkeypatch, StagePlugin(behavior=behavior))
    ctx = make_ctx(tmp_path, make_config())
    tasks = list(TASK_IDS)

    result = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)

    # Strong arm: one success per task.
    assert result["strong"]["metrics"] == {
        _prefixed(t): {"attempts": 1, "valid": 1, "success": 1, "infra": 0}
        for t in TASK_IDS}
    assert result["strong"]["summary"]["tasks"] == 3
    assert result["strong"]["summary"]["pass_at_n"] == 1.0
    # Weak arm: two attempts per task, only t3 has a success.
    assert result["weak"]["metrics"][_prefixed("t1")] == {
        "attempts": 2, "valid": 2, "success": 0, "infra": 0}
    assert result["weak"]["summary"]["pass_at_n"] == pytest.approx(1 / 3)
    # F (v2 rule any_weak_failed): every task with at least one failed
    # valid weak attempt. t3 enters too, despite its success -- a mixed
    # task is exactly what the v2 rule admits and the v1 rule did not.
    assert result["F"] == [_prefixed(t) for t in TASK_IDS]
    assert result["infra_shortfalls"] == []
    # Executions: 3 strong + 6 weak, one model call each.
    assert len(plugin.calls) == 9
    assert {c["handle"].role for c in plugin.calls} == {"strong", "weak"}
    # Both roles' trajectories are stored, with the role-table model names.
    assert len(ctx.store.ids("trajectories")) == 9
    weak_first = ctx.store.load(
        "trajectories", f"traj/{_prefixed('t1')}/weak/00", Trajectory)
    assert weak_first.model_name == "weak-7b"
    # The summary file is the returned dict, and both are JSON-safe (the
    # pipeline ledgers the returned dict verbatim).
    assert read_json(ctx.run_dir / "stage1_summary.json") == result
    json.dumps(result)


# ---------------------------------------------------------------------------
# Infra shortfalls
# ---------------------------------------------------------------------------

def _t2_weak_infra(record):
    """Strong succeeds; weak t1 fails, weak t2 always infra-errors (so K1
    exhausts its retries and stores nothing), weak t3 succeeds."""
    if record["handle"].role == "strong":
        return _outcome(record["task_id"], True)
    if record["task_id"] == "t1":
        return _outcome("t1", False)
    if record["task_id"] == "t2":
        raise InfrastructureError("sandbox")
    return _outcome(record["task_id"], True)


_T2_WEAK_UNITS = [f"traj/{_prefixed('t2')}/weak/00",
                  f"traj/{_prefixed('t2')}/weak/01"]


def test_infra_holes_write_summary_then_raise(monkeypatch, tmp_path):
    _pin_k1_infra(monkeypatch)
    plugin = _install_plugin(monkeypatch, StagePlugin(behavior=_t2_weak_infra))
    ctx = make_ctx(tmp_path, make_config())
    tasks = list(TASK_IDS)

    with pytest.raises(InfrastructureError, match="resume"):
        stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)

    # Every execution really ran through K1 and the plugin (weak t2 twice
    # per attempt: initial + one infra retry).
    assert len(plugin.calls) == 3 + 2 + 4 + 2
    failure_ids = [f["unit_id"] for f in
                   UnitFailureRecorder(ctx.run_dir).list_failures()]
    assert sorted(failure_ids) == _T2_WEAK_UNITS
    # The summary was written before raising and names the holes.
    summary = read_json(ctx.run_dir / "stage1_summary.json")
    assert summary["complete"] is False
    assert summary["infra_missing"] == _T2_WEAK_UNITS
    assert summary["infra_shortfall_accepted"] is False


def test_accepted_infra_shortfall_counts_holes_as_infra(monkeypatch, tmp_path):
    _pin_k1_infra(monkeypatch)
    _install_plugin(monkeypatch, StagePlugin(behavior=_t2_weak_infra))
    ctx = make_ctx(tmp_path, make_config())
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    tasks = list(TASK_IDS)

    result = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)

    assert result["complete"] is False
    assert result["infra_shortfall_accepted"] is True
    assert result["infra_missing"] == _T2_WEAK_UNITS
    # t2 stored nothing, yet it stays in the metrics as two infra attempts:
    # the denominators that count every task keep it.
    assert result["weak"]["metrics"][_prefixed("t2")] == {
        "attempts": 2, "valid": 0, "success": 0, "infra": 2}
    weak = result["weak"]["summary"]
    assert weak["tasks"] == 3
    assert weak["infra_tasks"] == [_prefixed("t2")]
    assert weak["avg_at_n"] == pytest.approx(0.5)          # (0 + 1) / 2
    assert weak["avg_at_n_conservative"] == pytest.approx(1 / 3)
    assert weak["pass_at_n"] == pytest.approx(1 / 3)
    # The infra-exhausted task is a shortfall, never a failure.
    assert result["F"] == [_prefixed("t1")]
    assert result["infra_shortfalls"] == [_prefixed("t2")]
    assert read_json(ctx.run_dir / "stage1_summary.json") == result


def test_resume_after_infra_holes_completes(monkeypatch, tmp_path):
    _pin_k1_infra(monkeypatch)
    healthy = {"on": False}

    def behavior(record):
        if healthy["on"]:
            return _outcome(record["task_id"], True)
        return _t2_weak_infra(record)

    plugin = _install_plugin(monkeypatch, StagePlugin(behavior=behavior))
    config = make_config()
    tasks = list(TASK_IDS)
    with pytest.raises(InfrastructureError):
        stage1_baseline.run(make_ctx(tmp_path, config),
                            train_tasks=tasks, test_tasks=tasks)

    healthy["on"] = True
    plugin.calls.clear()
    result = stage1_baseline.run(make_ctx(tmp_path, config),
                                 train_tasks=tasks, test_tasks=tasks)
    # Only the two holes were re-executed.
    assert [c["task_id"] for c in plugin.calls] == ["t2", "t2"]
    assert result["complete"] is True
    assert result["infra_missing"] == []
    assert result["infra_shortfall_accepted"] is False
    assert result["infra_shortfalls"] == []


def test_partial_sample_with_real_failure_is_in_F_and_shortfalls(
        monkeypatch, tmp_path):
    # The v2 F rule (any_weak_failed) on the case the v1 rule deferred wholly
    # to the infra list: t1's weak arm fails for real on attempt 0 and
    # loses attempt 1 to infra for good (an accepted hole). The task has a
    # PARTIAL valid sample with a real failure, so it enters F -- and stays
    # listed in infra_shortfalls: under v2 the two lists are no longer
    # disjoint by construction (decision section 3.1).
    _pin_k1_infra(monkeypatch)

    def behavior(record):
        if record["handle"].role == "strong":
            return _outcome(record["task_id"], True)
        if record["task_id"] == "t1":
            # plugin.calls already holds this call, and the strong arm's
            # t1 run is in there too -- count this handle's calls only:
            # the first weak t1 attempt fails for real, every later one
            # (attempt 1 + its K1 retry) infra-errors, so the unit stays a
            # hole.
            t1_calls = sum(1 for call in plugin.calls
                           if call["task_id"] == "t1"
                           and call["handle"] is record["handle"])
            if t1_calls == 1:
                return _outcome("t1", False)
            raise InfrastructureError("sandbox")
        return _outcome(record["task_id"], True)

    plugin = _install_plugin(monkeypatch, StagePlugin(behavior=behavior))
    ctx = make_ctx(tmp_path, make_config())
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    tasks = list(TASK_IDS)

    result = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)

    assert result["infra_shortfall_accepted"] is True
    # One valid failed attempt is enough for F under any_weak_failed.
    assert result["F"] == [_prefixed("t1")]
    # The same task is still short of a full valid sample: it appears in
    # BOTH lists, which is the v2 overlap this test pins.
    assert result["infra_shortfalls"] == [_prefixed("t1")]


# ---------------------------------------------------------------------------
# Resume: rerun re-aggregates without re-executing
# ---------------------------------------------------------------------------

def test_rerun_reaggregates_without_model_calls(monkeypatch, tmp_path):
    def behavior(record):
        correctness = record["handle"].role == "strong"
        return _outcome(record["task_id"], correctness)

    plugin = _install_plugin(monkeypatch, StagePlugin(behavior=behavior))
    config = make_config()
    handles = make_handles()
    ctx = make_ctx(tmp_path, config, handles=handles)
    tasks = list(TASK_IDS)
    first = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)

    calls_before = sum(len(h.client.calls) for h in handles.values())
    plugin.calls.clear()
    second = stage1_baseline.run(make_ctx(tmp_path, config, handles=handles),
                                 train_tasks=tasks, test_tasks=tasks)

    assert plugin.calls == []
    assert sum(len(h.client.calls) for h in handles.values()) == calls_before
    assert second == first


# ---------------------------------------------------------------------------
# Full-mode wiring: stage 0 output feeds stage 1 with every task
# ---------------------------------------------------------------------------

def test_full_mode_stage0_output_passes_all_tasks_to_stage1(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    config = make_config()
    handles = make_handles()
    ctx = make_ctx(tmp_path, config, handles=handles)

    frozen = stage0_freeze.run(ctx)
    assert frozen["tasks"] == 3
    result = stage1_baseline.run(
        ctx, train_tasks=frozen["train"], test_tasks=frozen["test"])

    # Every task ran on both arms: 3 tasks x (1 strong + 2 weak) units.
    expected = sorted(
        f"traj/{_prefixed(t)}/{role}/{a:02d}"
        for t in TASK_IDS for role in ("strong", "weak")
        for a in range({"strong": 1, "weak": 2}[role]))
    assert ctx.store.ids("trajectories") == expected
    assert result["F"] == []


# ---------------------------------------------------------------------------
# Rule name comes from the (completed) config, not a stage-local default
# ---------------------------------------------------------------------------

def test_task_select_read_from_config_rules(monkeypatch, tmp_path):
    from expedistill.blocks.rules import TASK_SELECT
    from expedistill.core.config import DEFAULT_RULES

    # The config owns the default: no stage-local fallback to drift from it.
    assert not hasattr(stage1_baseline, "_DEFAULT_TASK_SELECT")
    assert make_config(rules={}).rules["task_select"] == \
        DEFAULT_RULES["task_select"]

    name = "stage1_test_only_select_all"
    if name not in TASK_SELECT:
        TASK_SELECT.register(name)(lambda metrics, n_attempts: True)
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config(rules={"task_select": name}))
    tasks = list(TASK_IDS)
    result = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)
    # Every task succeeded, yet the configured rule selects them all.
    assert result["F"] == [_prefixed(t) for t in TASK_IDS]


# ---------------------------------------------------------------------------
# An accepted shortfall is a durable per-run decision
# ---------------------------------------------------------------------------

def test_accepted_shortfall_is_durable_across_resume(monkeypatch, tmp_path):
    _pin_k1_infra(monkeypatch)
    healthy = {"on": False}

    def behavior(record):
        if healthy["on"]:
            # The endpoint recovered: t2 would now succeed, and fail-free
            # data would change F if the holes were ever re-executed.
            return _outcome(record["task_id"], True)
        return _t2_weak_infra(record)

    plugin = _install_plugin(monkeypatch, StagePlugin(behavior=behavior))
    config = make_config()
    tasks = list(TASK_IDS)
    ctx = make_ctx(tmp_path, config)
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    first = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)
    assert first["infra_accepted"] == _T2_WEAK_UNITS
    assert stage1_baseline.accepted_infra_shortfall(ctx.run_dir) == \
        _T2_WEAK_UNITS

    # Resume WITHOUT the flag after recovery: the accepted holes are never
    # re-executed, and F / the summary stay exactly as accepted.
    healthy["on"] = True
    plugin.calls.clear()
    second = stage1_baseline.run(make_ctx(tmp_path, config),
                                 train_tasks=tasks, test_tasks=tasks)
    assert plugin.calls == []
    assert second == first
    assert second["F"] == [_prefixed("t1")]
    assert second["infra_shortfall_accepted"] is True


def test_accepted_set_is_written_before_F_is_derived(monkeypatch, tmp_path):
    _pin_k1_infra(monkeypatch)
    _install_plugin(monkeypatch, StagePlugin(behavior=_t2_weak_infra))
    ctx = make_ctx(tmp_path, make_config())
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))

    def boom(*args, **kwargs):
        raise RuntimeError("crash while deriving F")

    monkeypatch.setattr(stage1_baseline.metrics_block, "failed_tasks", boom)
    tasks = list(TASK_IDS)
    with pytest.raises(RuntimeError):
        stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)
    assert stage1_baseline.accepted_infra_shortfall(ctx.run_dir) == \
        _T2_WEAK_UNITS


def test_accepted_unit_stored_later_is_still_excluded(monkeypatch, tmp_path):
    _pin_k1_infra(monkeypatch)
    # Belt and braces: even if some producer stores an accepted unit later,
    # the stage keeps scoring it as the accepted hole, so F cannot move.
    _install_plugin(monkeypatch, StagePlugin(behavior=_t2_weak_infra))
    config = make_config()
    tasks = list(TASK_IDS)
    ctx = make_ctx(tmp_path, config)
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    first = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)
    for attempt in range(2):
        ctx.store.save(Trajectory(
            trajectory_id=f"traj/{_prefixed('t2')}/weak/{attempt:02d}",
            task_id=_prefixed("t2"), benchmark=_FAKE, model_role="weak",
            model_name="weak-7b", attempt_index=attempt,
            steps=[{"action": "exec t2", "observation": "ok"}],
            correctness=False), "trajectories")
    second = stage1_baseline.run(make_ctx(tmp_path, config),
                                 train_tasks=tasks, test_tasks=tasks)
    assert second == first


# ---------------------------------------------------------------------------
# Pre-registered auxiliary metric (plugin-declared; README D3 / stage 1)
# ---------------------------------------------------------------------------

class AuxPlugin(StagePlugin):
    """Fake benchmark declaring an auxiliary metric: the behavior's
    RunOutcome carries ``meta["fake_aux"]``, which aux_success reads."""

    aux_metric_name = "fake_aux"

    def aux_success(self, traj):
        return traj.meta.get("fake_aux") is True


def _aux_outcome(task_id, correctness, aux):
    outcome = _outcome(task_id, correctness)
    outcome.meta["fake_aux"] = aux
    return outcome


def test_arm_summaries_report_the_auxiliary_metric(monkeypatch, tmp_path):
    def behavior(record):
        if record["handle"].role == "strong":
            return _aux_outcome(record["task_id"], True, True)
        # Weak arm per (task, attempt-index is unknown here, so per task):
        aux = {"t1": [False, True], "t2": [False, False],
               "t3": [True, True]}[record["task_id"]]
        attempt = len([c for c in plugin.calls
                       if c["task_id"] == record["task_id"]
                       and c["handle"] is record["handle"]]) - 1
        correctness = record["task_id"] == "t3"
        return _aux_outcome(record["task_id"], correctness, aux[attempt])

    plugin = _install_plugin(monkeypatch, AuxPlugin(behavior=behavior))
    ctx = make_ctx(tmp_path, make_config())
    tasks = list(TASK_IDS)
    result = stage1_baseline.run(ctx, train_tasks=tasks, test_tasks=tasks)

    # Weak arm: aux rates t1 1/2, t2 0/2, t3 2/2 -> avg 0.5 (both
    # disciplines: every task has a full valid sample).
    weak_aux = result["weak"]["summary"]["aux"]
    assert weak_aux["metric"] == "fake_aux"
    assert weak_aux["avg_at_n"] == pytest.approx(0.5)
    assert weak_aux["avg_at_n_conservative"] == pytest.approx(0.5)
    # The strong arm reports its own aux under the same contract.
    assert result["strong"]["summary"]["aux"]["avg_at_n"] == 1.0
    # The primary metric is untouched: F is still the failed-weak set (t3's
    # weak arm succeeded on every attempt, so it stays out).
    assert result["F"] == [_prefixed("t1"), _prefixed("t2")]
    assert result["weak"]["summary"]["pass_at_n"] == pytest.approx(1 / 3)
    # Per-task aux counts are part of the metrics (unit-level evidence).
    assert result["weak"]["metrics"][_prefixed("t1")]["aux_success"] == 1
    # JSON-safe, as always.
    json.dumps(result)


def test_plugin_without_aux_metric_reports_null_and_no_scorer_runs(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())
    result = stage1_baseline.run(ctx, train_tasks=list(TASK_IDS),
                                test_tasks=list(TASK_IDS))
    assert result["weak"]["summary"]["aux"] is None
    assert result["strong"]["summary"]["aux"] is None
    assert "aux_success" not in result["weak"]["metrics"][_prefixed("t1")]


def test_declared_name_with_undeclared_scorer_is_a_code_bug(
        monkeypatch, tmp_path):
    class BrokenPlugin(StagePlugin):
        aux_metric_name = "fake_aux"
        # aux_success NOT overridden: the default answers None.

    _install_plugin(monkeypatch, BrokenPlugin())
    ctx = make_ctx(tmp_path, make_config())
    from expedistill.infra.reliability import CodeBugError
    with pytest.raises(CodeBugError, match="fake_aux"):
        stage1_baseline.run(ctx, train_tasks=list(TASK_IDS),
                           test_tasks=list(TASK_IDS))
