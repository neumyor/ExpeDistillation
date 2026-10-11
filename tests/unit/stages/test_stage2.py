"""Stage 2 unit tests: both roles segmented via K2, resume, skipped counting.

Trajectories are planted straight into the store (stage 1 has its own tests),
so this file exercises exactly the stage's own wiring: which trajectories it
offers to K2, in what order, and that a rerun offers them again only when K2
has nothing stored. The segmenter is a fake handle whose script proves the
model-call count."""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types

import pytest

from expedistill.benchmarks.base import BenchmarkPlugin, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.blocks.rules import register_trajectory_select
from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Step, Trajectory
from expedistill.infra.reliability import (
    InfrastructureError,
    ProviderRefusalError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import json_script, make_fake_handle
from expedistill.stages import stage2_segmentation
from expedistill.stages.context import StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


class StagePlugin(BenchmarkPlugin):
    """Fake benchmark: only view_rules matters here (K2 renders with it)."""

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        raise AssertionError("stage 2 must not execute tasks")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


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
        K=4,
        segment_retry_limit=0,
        rules={"task_select": "all_valid_failed", "trajectory_select": "all"},
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_ctx(tmp_path, config, segmenter=None, run_name="run"):
    run_dir = tmp_path / run_name
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={"segmenter": segmenter if segmenter is not None
                 else make_fake_handle("segmenter")},
        workdir_root=run_dir / "workdirs",
    )


def plant(ctx, task, role, attempt=0, status="failure", n_steps=4):
    task_id = make_task_id(_FAKE, task)
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id,
        benchmark=_FAKE,
        model_role=role,
        model_name=f"{role}-model",
        attempt_index=attempt,
        steps=[Step(action=f"a{i}", observation=f"o{i}")
               for i in range(n_steps)],
        status=status,
    )
    ctx.store.save(trajectory, "trajectories")
    return trajectory


def seg(start, end, label="seg", description="does things", success=True):
    return {"start": start, "end": end, "label": label,
            "description": description, "success": success}


def valid_script(count=1):
    """``count`` payloads that pass D7 for a 4-step trajectory."""
    payload = [seg(0, 2, "setup", "prepare", True),
               seg(2, 4, "run", "execute", False)]
    return [json_script(payload) for _ in range(count)]


def _plant_failed_task(ctx):
    """t1 with a strong and two weak trajectories; t2 stays out of F."""
    plant(ctx, "t1", "strong")
    plant(ctx, "t1", "weak", attempt=0)
    plant(ctx, "t1", "weak", attempt=1)
    plant(ctx, "t2", "strong")


# ---------------------------------------------------------------------------
# Both roles segmented
# ---------------------------------------------------------------------------

def test_segments_both_roles_and_writes_summary(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config(),
                   segmenter=make_fake_handle(script=valid_script(3)))
    _plant_failed_task(ctx)

    result = stage2_segmentation.run(ctx, failed_task_ids=[
        make_task_id(_FAKE, "t1")])

    assert result == {"segmented": 3, "skipped": 0, "total_subtasks": 6,
                      "infra_failed": []}
    assert read_json(ctx.run_dir / "stage2_summary.json") == result
    # One model call per trajectory (3 trajectories), via the segmenter role.
    assert len(ctx.handles["segmenter"].client.calls) == 3
    assert len(ctx.store.ids("subtasks")) == 6
    subtask_ids = ctx.store.ids("subtasks")
    assert subtask_ids[:2] == [
        "traj/fakebench:t1/strong/00/seg00",
        "traj/fakebench:t1/strong/00/seg01"]
    assert any("/strong/" in sid for sid in subtask_ids)
    assert any("/weak/" in sid for sid in subtask_ids)
    # The task outside F was never touched.
    assert not any(":t2" in sid for sid in subtask_ids)


# ---------------------------------------------------------------------------
# Resume: rerun does not re-model stored trajectories
# ---------------------------------------------------------------------------

def test_rerun_skips_stored_trajectories(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config(),
                   segmenter=make_fake_handle(script=valid_script(3)))
    _plant_failed_task(ctx)
    failed = [make_task_id(_FAKE, "t1")]
    first = stage2_segmentation.run(ctx, failed_task_ids=failed)

    # A "restarted process": fresh store and a handle whose empty script
    # raises on any call -- zero model calls is the pass condition.
    resumed_ctx = make_ctx(tmp_path, make_config(),
                           segmenter=make_fake_handle(script=[]))
    second = stage2_segmentation.run(resumed_ctx, failed_task_ids=failed)

    assert second == first
    assert resumed_ctx.handles["segmenter"].client.calls == []


# ---------------------------------------------------------------------------
# Skipped trajectories are counted, not crashed
# ---------------------------------------------------------------------------

def test_exhausted_trajectory_is_skipped_and_counted(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    gap = [seg(0, 1), seg(2, 4)]  # leaves step 1 uncovered: D7 rejects
    # Sorted trajectory order puts the strong run first: it gets the bad
    # payload and (retry limit 0) is skipped; the two weak runs segment.
    script = [json_script(gap)] + valid_script(2)
    ctx = make_ctx(tmp_path, make_config(),
                   segmenter=make_fake_handle(script=script))
    _plant_failed_task(ctx)

    result = stage2_segmentation.run(ctx, failed_task_ids=[
        make_task_id(_FAKE, "t1")])

    assert result == {"segmented": 2, "skipped": 1, "total_subtasks": 4,
                      "infra_failed": []}
    audit = read_jsonl(
        ctx.run_dir / "stage2_segments" / "segmentation_audit.jsonl")
    assert [r["trajectory_id"] for r in audit] == ["traj/fakebench:t1/strong/00"]
    assert audit[0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# The trajectory_select rule is consulted
# ---------------------------------------------------------------------------

def test_trajectory_select_rule_filters_the_units(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())

    @register_trajectory_select("stage2_test_only_weak")
    def _only_weak(trajectory):
        return trajectory.model_role == "weak"

    ctx = make_ctx(
        tmp_path, make_config(rules={"trajectory_select": "stage2_test_only_weak"}),
        segmenter=make_fake_handle(script=valid_script(2)))
    _plant_failed_task(ctx)

    result = stage2_segmentation.run(ctx, failed_task_ids=[
        make_task_id(_FAKE, "t1")])

    assert result == {"segmented": 2, "skipped": 0, "total_subtasks": 4,
                      "infra_failed": []}
    assert not any("/strong/" in sid for sid in ctx.store.ids("subtasks"))


def test_trajectory_select_is_read_from_config_rules(monkeypatch, tmp_path):
    # The stage looks the rule up by exactly the configured name; it keeps
    # no local default of its own.
    _install_plugin(monkeypatch, StagePlugin())
    seen = []
    real = stage2_segmentation.get_trajectory_select

    def spy(name):
        seen.append(name)
        return real(name)

    monkeypatch.setattr(stage2_segmentation, "get_trajectory_select", spy)

    @register_trajectory_select("stage2_test_none")
    def _none(trajectory):
        return False

    ctx = make_ctx(
        tmp_path, make_config(rules={"trajectory_select": "stage2_test_none"}),
        segmenter=make_fake_handle(script=[]))
    _plant_failed_task(ctx)

    result = stage2_segmentation.run(ctx, failed_task_ids=[
        make_task_id(_FAKE, "t1")])

    assert seen == ["stage2_test_none"]
    assert result["segmented"] == 0


# ---------------------------------------------------------------------------
# Per-trajectory failure disposition
# ---------------------------------------------------------------------------

def _fail_first_g1_call(monkeypatch, exc):
    """G1 (inside K2) raises ``exc`` on its first call, then behaves.

    Patched at K2's import of the block, below call_model, so the test does
    not depend on call-level retry policy."""
    # The package re-exports the function under the module's name, so
    # reach the module itself through the import system.
    k2 = importlib.import_module("expedistill.composites.segment_checked")

    real = k2.segment
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise exc
        return real(*args, **kwargs)

    monkeypatch.setattr(k2, "segment", flaky)


def test_infra_failure_skips_one_trajectory_then_raises(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    # Sorted order puts the strong run first: it hits the outage.
    _fail_first_g1_call(monkeypatch, InfrastructureError("endpoint down"))
    ctx = make_ctx(tmp_path, make_config(),
                   segmenter=make_fake_handle(script=valid_script(2)))
    _plant_failed_task(ctx)
    failed = [make_task_id(_FAKE, "t1")]
    strong_id = "traj/fakebench:t1/strong/00"

    with pytest.raises(InfrastructureError) as info:
        stage2_segmentation.run(ctx, failed_task_ids=failed)

    assert "1 unit" in str(info.value)
    assert "resume" in str(info.value)
    # The other two trajectories were still segmented, and the summary
    # (written before the raise) names the hole.
    summary = read_json(ctx.run_dir / "stage2_summary.json")
    assert summary == {"segmented": 2, "skipped": 0, "total_subtasks": 4,
                       "infra_failed": [strong_id]}
    assert not any("/strong/" in sid for sid in ctx.store.ids("subtasks"))
    # Infra is not a segmentation failure: no "skipped" audit record ...
    audit = read_jsonl(
        ctx.run_dir / "stage2_segments" / "segmentation_audit.jsonl")
    assert audit == []
    # ... but the unit is in the failure ledger.
    ledger = UnitFailureRecorder(ctx.run_dir).list_failures()
    assert [(r["unit_id"], r["stage"], r["error_class"]) for r in ledger] == [
        (strong_id, "stage2_segmentation", "InfrastructureError")]

    # Resume with the service back: exactly the failed trajectory is
    # segmented (one model call), the stored ones are reloaded. The patched
    # G1 only failed its first call, so it now delegates.
    resumed = make_ctx(tmp_path, make_config(),
                       segmenter=make_fake_handle(script=valid_script(1)))
    result = stage2_segmentation.run(resumed, failed_task_ids=failed)

    assert result == {"segmented": 3, "skipped": 0, "total_subtasks": 6,
                      "infra_failed": []}
    assert len(resumed.handles["segmenter"].client.calls) == 1


def test_provider_refusal_stops_the_stage(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    _fail_first_g1_call(monkeypatch, ProviderRefusalError("bad key"))
    ctx = make_ctx(tmp_path, make_config(),
                   segmenter=make_fake_handle(script=valid_script(2)))
    _plant_failed_task(ctx)

    with pytest.raises(ProviderRefusalError):
        stage2_segmentation.run(ctx, failed_task_ids=[
            make_task_id(_FAKE, "t1")])

    assert ctx.handles["segmenter"].client.calls == []
    assert ctx.store.ids("subtasks") == []
    assert UnitFailureRecorder(ctx.run_dir).list_failures() == []


def test_rerun_after_exhaustion_makes_zero_calls(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    gap = [seg(0, 1), seg(2, 4)]
    script = [json_script(gap)] + valid_script(2)
    ctx = make_ctx(tmp_path, make_config(),
                   segmenter=make_fake_handle(script=script))
    _plant_failed_task(ctx)
    failed = [make_task_id(_FAKE, "t1")]
    first = stage2_segmentation.run(ctx, failed_task_ids=failed)

    # Re-running the finished stage: the skipped trajectory stays skipped
    # (terminal within the run), so zero model calls and the same summary.
    rerun = make_ctx(tmp_path, make_config(),
                     segmenter=make_fake_handle(script=valid_script(1)))
    second = stage2_segmentation.run(rerun, failed_task_ids=failed)

    assert second == first
    assert read_json(rerun.run_dir / "stage2_summary.json") == first
    assert rerun.handles["segmenter"].client.calls == []
    audit = read_jsonl(
        rerun.run_dir / "stage2_segments" / "segmentation_audit.jsonl")
    assert len(audit) == 1
