"""Stage 6 tests: with-bank vs control arms, strong model fully out.

Fake benchmark plugin served through the real import/registry machinery (the
stub-finder pattern of tests/composites/test_batch_execute.py), fake handles
from llm.fake, real Store on a temp run directory. The bank is fabricated
with append_bank rows (what K5 leaves behind). Pinned behaviours under test:
split mode runs BOTH arms with the right prompts (skills block present vs
bare) through the fake plugin; full mode reuses stage-1 weak bare trajectories
without re-running anything for the control; the strong handle is never
called; delta matches hand-computed metrics; a second call adds no
executions; a missing stage-1 unit stops the full-mode stage loudly.
"""

import importlib
import importlib.abc
import importlib.machinery
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
from expedistill.benchmarks.registry import ConfigError, _reset_registry
from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.inject import bank_reference
from expedistill.llm.fake import make_fake_handle
from expedistill.methods.delivery import retrieval
from expedistill.composites.decision import final_test_delta
from expedistill.stages import stage6_test
from dataclasses import replace
from expedistill.stages.context import RunSettings, StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


# ---------------------------------------------------------------------------
# Fake benchmark plugin
# ---------------------------------------------------------------------------

class RecordingPlugin(BenchmarkPlugin):
    """Records every run() call; the model "prompt" includes the skills
    block, so FakeClient logs prove which arm a call belonged to.

    ``outcome(task_id, attempt_token, armed) -> status`` scripts per-unit
    results; ``armed`` is True when a skills block was injected.
    """

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or (lambda task_id, token, armed: True)

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        armed = bool(skills_block)
        token = task.meta.get("attempt_token")
        self.calls.append({
            "task_id": task.task_id,
            "attempt_token": token,
            "skills_block": skills_block,
            "workdir": Path(workdir),
        })
        # The "prompt": what a real runner would build from the task template
        # -- instruction plus the injected block (empty for the bare arm).
        handle.client.complete(handle.name, "system",
                               f"{task.instruction}\n{skills_block}")
        correctness = self.outcome(task.task_id, token, armed)
        return RunOutcome(steps=[{"action": f"exec {task.task_id}",
                                  "observation": "ok"}],
                          correctness=correctness)

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery."""
    fullname = f"expedistill.benchmarks.{plugin.name}"
    if fullname in sys.modules:
        try:
            if _registry_get(plugin.name) is plugin:
                return plugin
        except ConfigError:
            pass

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


def _registry_get(name):
    from expedistill.benchmarks.registry import get_plugin
    return get_plugin(name)


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry()
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _tasks(*ids_):
    return [Task(task_id=i, instruction=f"do {i}", meta={"gold": i})
            for i in ids_]


def _retrieval_tasks():
    """Two tasks whose instructions each share vocabulary with exactly one
    bank skill, so the real select_skills routes them apart."""
    return [
        Task(task_id="t1", instruction="read the log file and debug",
             meta={"gold": "t1"}),
        Task(task_id="t2", instruction="restart after the config check",
             meta={"gold": "t2"}),
    ]


_BANK_SKILL_A = "traj/tb:t1/strong/00/seg00/skill"
_BANK_SKILL_B = "traj/tb:t2/strong/00/seg01/skill"


def _bank_rows():
    return [
        {"skill_id": _BANK_SKILL_A, "version": 1,
         "content": "Always read the log file first.", "group": "debugging"},
        {"skill_id": _BANK_SKILL_B, "version": 1,
         "content": "Restart only after the config check.", "group": None},
    ]


def _make_ctx(tmp_path, mode, *, n_attempts=2, delivery=None):
    run_dir = tmp_path / "run"
    handles = {
        "executor_strong": make_fake_handle("strong"),
        "executor_weak": make_fake_handle("weak"),
        "answerer": make_fake_handle("weak"),
        "judge": make_fake_handle("judge"),
        "curator": make_fake_handle("curator"),
    }
    config = ExperimentConfig(
        benchmark=_FAKE, mode=mode,
        models={"executor_strong": "strong-x", "executor_weak": "weak-7b"},
        n_attempts_weak=n_attempts,
        methods={"delivery":
                  delivery if delivery is not None else {}})
    ctx = StageContext(run_dir=run_dir, store=Store(run_dir), config=config,
                       usage=None, handles=handles,
                       workdir_root=run_dir / "work")
    for row in _bank_rows():
        ctx.store.append_bank(row)
    return ctx


def _stage1_weak_trajectory(ctx, task_id, attempt, correctness):
    """A stage-1 weak bare trajectory exactly as K1 would have stored it."""
    prefixed = make_task_id(_FAKE, task_id)
    return Trajectory(
        trajectory_id=make_trajectory_id(prefixed, "weak", attempt),
        task_id=prefixed, benchmark=_FAKE, model_role="weak",
        model_name="weak-7b", attempt_index=attempt, skills_injected=None,
        steps=[{"action": f"exec {task_id}", "observation": "ok"}],
        correctness=correctness)


def _run_stage6(ctx, monkeypatch, plugin, tasks):
    _install_plugin(monkeypatch, plugin)
    return stage6_test.run(ctx, test_tasks=tasks)


def _pin_k1_infra(monkeypatch):
    """Run K1 at the pre-resilience retry settings (one infra retry, zero
    backoff) in the tests whose with-bank t2 always infra-errors.

    The stage has no run setting for K1's retry knobs, so the pin goes
    through the call: a transparent wrapper around the stage's
    ``batch_execute`` injects the values these tests were written against
    (initial + one retry per attempt). Without it K1's resilience defaults
    (2 retries, 15-45s random backoff) would both change the plugin call
    counts and sleep for real between t2's attempts, slowing the suite by
    minutes.
    """
    def injecting(real):
        def wrapper(**kwargs):
            kwargs.setdefault("infra_retries", 1)
            kwargs.setdefault("infra_backoff", (0.0, 0.0))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(stage6_test, "batch_execute",
                        injecting(stage6_test.batch_execute))


def _summary_path(ctx):
    return (ctx.run_dir / stage6_test.TEST_DIR
            / stage6_test.SUMMARY_NAME)


def _arm_traj_ids(task_ids, arm, n=2):
    prefixed = [make_task_id(_FAKE, t) for t in task_ids]
    return sorted(make_trajectory_id(p, "weak", a)
                  for p in prefixed for a in range(n))


# ---------------------------------------------------------------------------
# Split mode
# ---------------------------------------------------------------------------

def test_split_mode_runs_both_arms_and_computes_delta(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split")

    def outcome(task_id, token, armed):
        if not armed:
            return False
        if task_id == "t1":
            return True
        return True if token == "a01" else False

    plugin = RecordingPlugin(outcome)
    summary = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))

    # Both arms really ran: 2 tasks x 2 attempts each, per arm.
    armed_calls = [c for c in plugin.calls if c["skills_block"]]
    bare_calls = [c for c in plugin.calls if not c["skills_block"]]
    assert len(armed_calls) == 4 and len(bare_calls) == 4
    # The with-bank prompts carried the skills block; the control was bare.
    block = plugin.calls[0]["skills_block"]
    assert "## Skill rules" in block
    assert "Always read the log file first." in block
    assert all(c["skills_block"] == block for c in armed_calls)
    assert all(c["skills_block"] == "" for c in bare_calls)
    # Same evidence from the model-client side (the actual prompt text).
    users = [call["user"] for call in
             ctx.handles["executor_weak"].client.calls]
    assert sum("## Skill rules" in u for u in users) == 4
    assert sum("## Skill rules" not in u for u in users) == 4

    # Hand-computed metrics: with-bank t1 2/2, t2 1/2 -> avg 0.75, pass 1.0;
    # control 0/2 everywhere -> avg 0.0, pass 0.0.
    assert summary["mode"] == "split"
    assert summary["control_source"] == "fresh_bare_run"
    assert summary["bank_reference"] == bank_reference(_bank_rows())
    assert summary["with_bank"]["avg_at_n"] == pytest.approx(0.75)
    assert summary["with_bank"]["pass_at_n"] == pytest.approx(1.0)
    assert summary["control"]["avg_at_n"] == pytest.approx(0.0)
    assert summary["control"]["pass_at_n"] == pytest.approx(0.0)
    assert summary["delta"]["avg_at_n"] == pytest.approx(0.75)
    assert summary["delta"]["pass_at_n"] == pytest.approx(1.0)
    # The summary is on disk and identical to the returned value.
    assert read_json(_summary_path(ctx)) == summary

    # The with-bank trajectories are recorded as bank-injected and live in
    # their own arm store, separate from the bare arm's.
    bank_store = Store(ctx.run_dir / "stage6_test" / "bank")
    bare_store = Store(ctx.run_dir / "stage6_test" / "bare")
    assert bank_store.ids("trajectories") == _arm_traj_ids(["t1", "t2"], "bank")
    assert bare_store.ids("trajectories") == _arm_traj_ids(["t1", "t2"], "bare")
    reference = bank_reference(_bank_rows())
    for trajectory_id in bank_store.ids("trajectories"):
        traj = bank_store.load("trajectories", trajectory_id, Trajectory)
        assert traj.skills_injected == reference


def test_strong_model_never_invoked(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split")
    summary = _run_stage6(ctx, monkeypatch, RecordingPlugin(),
                          _tasks("t1", "t2"))
    assert summary  # the stage completed
    # The strong executor made zero model calls -- as did every role other
    # than the weak executor.
    for role, handle in ctx.handles.items():
        expected = 0 if role != "executor_weak" else 8
        assert len(handle.client.calls) == expected, role


def test_split_mode_empty_bank_still_runs_both_arms(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split")
    # Empty the fabricated bank: the weak model "reads the whole bank",
    # which is nothing -- the arm still runs, with an empty block.
    (ctx.run_dir / "skill_bank.jsonl").unlink()
    plugin = RecordingPlugin()
    summary = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1"))
    assert summary["bank_reference"] == bank_reference([])
    assert len(plugin.calls) == 4  # 1 task x 2 attempts x both arms
    assert all(c["skills_block"] == "" for c in plugin.calls)
    # Both arms bare and identical in outcome -> delta of zero (the fake
    # benchmark declares no auxiliary metric, so the aux fields are null).
    assert summary["delta"] == {"avg_at_n": 0.0,
                                "avg_at_n_conservative": 0.0,
                                "pass_at_n": 0.0,
                                "aux": None}


def test_resume_second_call_adds_no_executions(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split")
    plugin = RecordingPlugin()
    first = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    n_plugin_calls = len(plugin.calls)
    n_client_calls = len(ctx.handles["executor_weak"].client.calls)

    plugin.calls.clear()
    second = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    assert plugin.calls == []
    assert len(ctx.handles["executor_weak"].client.calls) == n_client_calls
    assert second == first
    assert n_plugin_calls == 8


# ---------------------------------------------------------------------------
# Retrieval skill delivery (protocol v2): per-task top-k injection
# ---------------------------------------------------------------------------

#: The with-bank block each retrieval task must receive (verified against the
#: real select_skills/derive_categories over the fabricated two-skill bank).
_EXPECTED_T1_BLOCK = ("## Skill rules\n### debugging\n"
                     "- Always read the log file first.")
_EXPECTED_T2_BLOCK = "## Skill rules\n- Restart only after the config check."


def test_retrieval_mode_selects_and_injects_per_task(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split", delivery={
        "name": "retrieval", "top_k": 1, "max_categories": 2,
        "min_score": 0.0})
    plugin = RecordingPlugin()
    summary = _run_stage6(ctx, monkeypatch, plugin, _retrieval_tasks())

    # Each with-bank unit saw exactly its task's retrieved block; the
    # control arm stayed bare.
    armed_blocks = {call["task_id"]: call["skills_block"]
                   for call in plugin.calls if call["skills_block"]}
    assert armed_blocks == {"t1": _EXPECTED_T1_BLOCK, "t2": _EXPECTED_T2_BLOCK}
    assert all(call["skills_block"] == "" for call in plugin.calls
               if not call["skills_block"])

    # The stored with-bank trajectories carry the per-task injection records
    # (the new skills_injected shape: a list of {skill_id, version}).
    bank_store = Store(ctx.run_dir / "stage6_test" / "bank")
    injected = {}
    for trajectory_id in bank_store.ids("trajectories"):
        traj = bank_store.load("trajectories", trajectory_id, Trajectory)
        injected[traj.task_id] = traj.skills_injected
    assert injected == {
        make_task_id(_FAKE, "t1"): [{"skill_id": _BANK_SKILL_A, "version": 1}],
        make_task_id(_FAKE, "t2"): [{"skill_id": _BANK_SKILL_B, "version": 1}],
    }

    # The audit names the full resolution of every test task, one line each.
    audit = read_jsonl(ctx.run_dir / stage6_test.TEST_DIR
                       / retrieval.RETRIEVAL_AUDIT_NAME)
    assert [row["task_id"] for row in audit] == ["t1", "t2"]
    t1_row, t2_row = audit
    assert t1_row["routed"] == ["debugging", _BANK_SKILL_B]
    assert t1_row["selected"] == [{"skill_id": _BANK_SKILL_A, "version": 1}]
    assert t1_row["threshold_passed"] is True
    assert [entry["skill_id"] for entry in t1_row["scores"]] == [
        _BANK_SKILL_A, _BANK_SKILL_B]
    assert all("score" in entry for entry in t1_row["scores"])
    assert t1_row["categories"] == {"debugging": "debugging",
                                    _BANK_SKILL_B: _BANK_SKILL_B}
    assert t2_row["routed"] == [_BANK_SKILL_B, "debugging"]
    assert t2_row["selected"] == [{"skill_id": _BANK_SKILL_B, "version": 1}]

    # Summary compatibility: the whole-bank digest stays the bank identity
    # and every pre-existing key keeps its meaning.
    assert summary["bank_reference"] == bank_reference(_bank_rows())
    assert {"mode", "bank_reference", "control_source", "with_bank",
            "control", "delta", "infra_missing", "infra_accepted",
            "complete", "infra_shortfall_accepted"} <= set(summary)
    assert summary["control_source"] == "fresh_bare_run"


def test_retrieval_below_threshold_runs_bare(tmp_path, monkeypatch):
    # No task can clear a 0.99 gate: the with-bank arm is a bare run whose
    # skills_injected is [] -- distinguishable from the control's None.
    ctx = _make_ctx(tmp_path, "split", delivery={
        "name": "retrieval", "top_k": 2, "max_categories": 2,
        "min_score": 0.99})
    plugin = RecordingPlugin()
    _run_stage6(ctx, monkeypatch, plugin, _retrieval_tasks())
    assert all(call["skills_block"] == "" for call in plugin.calls)

    bank_store = Store(ctx.run_dir / "stage6_test" / "bank")
    gated = [bank_store.load("trajectories", trajectory_id, Trajectory)
             for trajectory_id in bank_store.ids("trajectories")]
    assert len(gated) == 4
    assert all(traj.skills_injected == [] for traj in gated)
    bare_store = Store(ctx.run_dir / "stage6_test" / "bare")
    bare = [bare_store.load("trajectories", trajectory_id, Trajectory)
            for trajectory_id in bare_store.ids("trajectories")]
    assert all(traj.skills_injected is None for traj in bare)

    # The audit kept the full ranking even though the gate failed.
    audit = read_jsonl(ctx.run_dir / stage6_test.TEST_DIR
                       / retrieval.RETRIEVAL_AUDIT_NAME)
    assert len(audit) == 2
    assert all(row["selected"] == [] and row["threshold_passed"] is False
               and row["scores"] for row in audit)


def test_retrieval_audit_is_overwritten_not_appended(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split", delivery={
        "name": "retrieval", "top_k": 1, "max_categories": 2,
        "min_score": 0.0})
    audit_path = (ctx.run_dir / stage6_test.TEST_DIR
                  / retrieval.RETRIEVAL_AUDIT_NAME)
    # A stale row an earlier resolution left behind: dispatch replaces the
    # whole file, never appends under it.
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text('{"stale": true}\n', encoding="utf-8")

    plugin = RecordingPlugin()
    _run_stage6(ctx, monkeypatch, plugin, _retrieval_tasks())
    first = read_jsonl(audit_path)
    assert [row["task_id"] for row in first] == ["t1", "t2"]
    assert all("stale" not in row for row in first)

    # The resume re-resolves and overwrites: still one line per task, and
    # zero new executions (the per-task resume comparison held).
    plugin.calls.clear()
    _run_stage6(ctx, monkeypatch, plugin, _retrieval_tasks())
    assert plugin.calls == []
    assert read_jsonl(audit_path) == first


def test_whole_bank_delivery_writes_no_retrieval_audit(tmp_path, monkeypatch):
    # The default delivery keeps the scalar path: digest injection, no
    # retrieval artifact.
    ctx = _make_ctx(tmp_path, "split")
    plugin = RecordingPlugin()
    summary = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    assert not (ctx.run_dir / stage6_test.TEST_DIR
                / retrieval.RETRIEVAL_AUDIT_NAME).exists()
    reference = bank_reference(_bank_rows())
    assert summary["bank_reference"] == reference
    bank_store = Store(ctx.run_dir / "stage6_test" / "bank")
    for trajectory_id in bank_store.ids("trajectories"):
        traj = bank_store.load("trajectories", trajectory_id, Trajectory)
        assert traj.skills_injected == reference


# ---------------------------------------------------------------------------
# Full mode
# ---------------------------------------------------------------------------

def _seed_stage1_weak(ctx, correctnesses):
    """Store stage-1 weak bare trajectories:
    {task_id: [verdict per attempt]}."""
    for task_id, per_attempt in correctnesses.items():
        for attempt, correctness in enumerate(per_attempt):
            ctx.store.save(
                _stage1_weak_trajectory(ctx, task_id, attempt, correctness),
                "trajectories")


def test_full_mode_reuses_stage1_without_rerunning(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "full")
    # Stage 1 already measured the weak model bare on the whole task set.
    _seed_stage1_weak(ctx, {"t1": [True, True],
                            "t2": [False, False]})
    n_stored_before = set(ctx.store.ids("trajectories"))

    def outcome(task_id, token, armed):
        assert armed, "the control arm must not execute in full mode"
        return True

    plugin = RecordingPlugin(outcome)
    summary = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))

    # Only the with-bank arm executed: 4 units, all armed.
    assert len(plugin.calls) == 4
    assert all(c["skills_block"] for c in plugin.calls)
    # The with-bank arm did not touch stage 1's trajectories.
    assert set(ctx.store.ids("trajectories")) == n_stored_before

    # Control = stage-1 reuse: t1 2/2, t2 0/2 -> avg 0.5, pass 0.5.
    # With-bank: 2/2 everywhere -> avg 1.0, pass 1.0.
    assert summary["mode"] == "full"
    assert summary["control_source"] == "stage1_reuse"
    assert summary["with_bank"]["avg_at_n"] == pytest.approx(1.0)
    assert summary["with_bank"]["pass_at_n"] == pytest.approx(1.0)
    assert summary["control"]["avg_at_n"] == pytest.approx(0.5)
    assert summary["control"]["pass_at_n"] == pytest.approx(0.5)
    assert summary["delta"]["avg_at_n"] == pytest.approx(0.5)
    assert summary["delta"]["pass_at_n"] == pytest.approx(0.5)


def test_full_mode_missing_stage1_unit_is_resumable_infra(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "full")
    # Stage 1 is missing t2's second attempt: an incomplete control would
    # manufacture a delta, so the stage must refuse to run.
    _seed_stage1_weak(ctx, {"t1": [True, True],
                            "t2": [False]})
    # A hole in stage 1 is an infra hole, not a config error: re-running the
    # same command resumes stage 1 first, which retries it.
    plugin = RecordingPlugin()
    with pytest.raises(InfrastructureError) as excinfo:
        _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    assert make_trajectory_id(make_task_id(_FAKE, "t2"), "weak", 1) \
        in str(excinfo.value)
    # Refused before executing anything or writing a summary.
    assert plugin.calls == []
    assert not _summary_path(ctx).exists()


def _bank_t2_infra(task_id, token, armed):
    """With-bank arm: t1 succeeds, t2 always fails on infrastructure (K1
    exhausts its retries and stores nothing). Bare arm: t1 fails, t2
    succeeds."""
    if armed:
        if task_id == "t2":
            raise InfrastructureError("sandbox down")
        return True
    return False if task_id == "t1" else True


def _units(task_id, n=2):
    return [make_trajectory_id(make_task_id(_FAKE, task_id), "weak", a)
            for a in range(n)]


def test_infra_holes_write_summary_then_raise(tmp_path, monkeypatch):
    _pin_k1_infra(monkeypatch)
    # Real K1 path: the with-bank arm's t2 units come back as nothing.
    ctx = _make_ctx(tmp_path, "split")
    plugin = RecordingPlugin(_bank_t2_infra)
    with pytest.raises(InfrastructureError, match="resume"):
        _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    # Both arms ran (t2 with-bank: initial + one infra retry per attempt).
    assert len(plugin.calls) == 2 + 4 + 4
    summary = read_json(_summary_path(ctx))
    assert summary["complete"] is False
    assert summary["infra_shortfall_accepted"] is False
    assert summary["infra_missing"] == {"with_bank": _units("t2"),
                                        "control": []}


def test_accepted_shortfall_keeps_one_denominator(tmp_path, monkeypatch):
    _pin_k1_infra(monkeypatch)
    # Reviewer probe: before the fix the with-bank arm silently had tasks=1
    # against the control's tasks=2, and a delta was reported anyway.
    ctx = _make_ctx(tmp_path, "split")
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    summary = _run_stage6(ctx, monkeypatch, RecordingPlugin(_bank_t2_infra),
                          _tasks("t1", "t2"))

    with_bank, control = summary["with_bank"], summary["control"]
    assert with_bank["tasks"] == control["tasks"] == 2
    assert with_bank["infra_tasks"] == [make_task_id(_FAKE, "t2")]
    # With-bank: t1 2/2, t2 all infra -> conditional 1.0, conservative 0.5,
    # pass 0.5. Control: t1 0/2, t2 2/2 -> 0.5 / 0.5 / 0.5.
    assert with_bank["avg_at_n"] == pytest.approx(1.0)
    assert with_bank["avg_at_n_conservative"] == pytest.approx(0.5)
    assert with_bank["pass_at_n"] == pytest.approx(0.5)
    assert summary["delta"]["avg_at_n"] == pytest.approx(0.5)
    assert summary["delta"]["avg_at_n_conservative"] == pytest.approx(0.0)
    assert summary["delta"]["pass_at_n"] == pytest.approx(0.0)
    assert summary["complete"] is False
    assert summary["infra_shortfall_accepted"] is True
    assert summary["infra_missing"]["with_bank"] == _units("t2")
    assert read_json(_summary_path(ctx)) == summary


def test_complete_run_records_no_holes(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split")
    summary = _run_stage6(ctx, monkeypatch, RecordingPlugin(),
                          _tasks("t1", "t2"))
    assert summary["complete"] is True
    assert summary["infra_shortfall_accepted"] is False
    assert summary["infra_missing"] == {"with_bank": [], "control": []}


def test_full_mode_flag_alone_cannot_accept_a_stage1_hole(
        tmp_path, monkeypatch):
    # A full-mode control hole belongs to stage 1: only stage 1 can accept
    # it durably (otherwise a later stage-1 resume could fill it and move F
    # and the bank). The flag in stage 6 alone is not enough.
    ctx = _make_ctx(tmp_path, "full")
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    _seed_stage1_weak(ctx, {"t1": [False, False],
                            "t2": [True]})
    plugin = RecordingPlugin()
    with pytest.raises(InfrastructureError, match="stage 1"):
        _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    assert plugin.calls == []


def test_delta_conservative_defined_when_conditional_is_none():
    # An arm with no valid trial has avg_at_n None, but its conservative
    # avg is still a number (0.0), so its delta is a number too.
    with_bank = {"avg_at_n": 0.5, "avg_at_n_conservative": 0.5,
                 "pass_at_n": 0.5}
    control = {"avg_at_n": None, "avg_at_n_conservative": 0.0,
               "pass_at_n": 0.0}
    delta = final_test_delta(with_bank, control)
    assert delta["avg_at_n"] is None
    assert delta["avg_at_n_conservative"] == pytest.approx(0.5)
    assert delta["pass_at_n"] == pytest.approx(0.5)


def test_accepted_shortfall_is_durable_across_resume(tmp_path, monkeypatch):
    _pin_k1_infra(monkeypatch)
    healthy = {"on": False}

    def outcome(task_id, token, armed):
        if healthy["on"]:
            return True
        return _bank_t2_infra(task_id, token, armed)

    ctx = _make_ctx(tmp_path, "split")
    ctx = replace(ctx, run_settings=RunSettings(allow_infra_shortfall=True))
    plugin = RecordingPlugin(outcome)
    first = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    assert first["infra_accepted"] == {"with_bank": _units("t2"),
                                       "control": []}

    healthy["on"] = True
    plugin.calls.clear()
    ctx = replace(ctx, run_settings=RunSettings(
        allow_infra_shortfall=False))  # the resume does not pass the flag
    second = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))
    assert plugin.calls == []
    assert second == first


def test_full_mode_control_inherits_stage1_accepted_holes(
        tmp_path, monkeypatch):
    # Stage 1 durably accepted t2/weak/01; stage 6 (no flag) must not demand
    # a unit that stage 1 will never re-execute.
    from expedistill.stages import stage1_baseline

    ctx = _make_ctx(tmp_path, "full")
    _seed_stage1_weak(ctx, {"t1": [False, False],
                            "t2": [True]})
    stage1_baseline.write_accepted_infra_shortfall(
        ctx.run_dir, [_units("t2")[1]])
    summary = _run_stage6(ctx, monkeypatch, RecordingPlugin(),
                          _tasks("t1", "t2"))
    assert summary["infra_accepted"] == {"with_bank": [],
                                         "control": [_units("t2")[1]]}
    assert summary["infra_shortfall_accepted"] is True
    assert summary["control"]["infra_tasks"] == [make_task_id(_FAKE, "t2")]


# ---------------------------------------------------------------------------
# Pre-registered auxiliary metric (plugin-declared; README stage 6)
# ---------------------------------------------------------------------------

class AuxRecordingPlugin(RecordingPlugin):
    """RecordingPlugin whose outcomes carry an auxiliary verdict.

    ``aux_fn(task_id, token, armed) -> bool`` scripts it; the RunOutcome's
    meta carries ``fake_aux`` the way the SearchQA plugin carries the
    evaluator's sub_em, and the declared scorer reads it back."""

    aux_metric_name = "fake_aux"

    def __init__(self, outcome=None, aux_fn=None):
        super().__init__(outcome)
        self.aux_fn = aux_fn or (lambda task_id, token, armed: False)

    def aux_success(self, traj):
        return traj.meta.get("fake_aux") is True

    def run(self, task, handle, skills_block, workdir):
        outcome = super().run(task, handle, skills_block, workdir)
        token = task.meta.get("attempt_token")
        outcome.meta["fake_aux"] = self.aux_fn(
            task.task_id, token, bool(skills_block))
        return outcome


def test_arms_report_aux_and_delta_has_the_same_aux_deltas(
        tmp_path, monkeypatch):
    # The pilot case, reproduced: the primary (status-based) delta is zero,
    # the auxiliary view carries the signal. Both are reported; the primary
    # is never swapped for the friendlier one.
    ctx = _make_ctx(tmp_path, "split")

    def outcome(task_id, token, armed):
        return False  # primary: nothing succeeds on either arm

    def aux_fn(task_id, token, armed):
        return armed and task_id == "t1"  # with-bank t1 answers aux-good

    plugin = AuxRecordingPlugin(outcome, aux_fn)
    summary = _run_stage6(ctx, monkeypatch, plugin, _tasks("t1", "t2"))

    # Primary: both arms 0.0 everywhere.
    assert summary["with_bank"]["avg_at_n"] == 0.0
    assert summary["control"]["avg_at_n"] == 0.0
    assert summary["delta"]["avg_at_n"] == 0.0
    # Auxiliary: with-bank t1 2/2, t2 0/2 -> 0.5; control 0.0.
    assert summary["with_bank"]["aux"] == {
        "metric": "fake_aux", "avg_at_n": 0.5,
        "avg_at_n_conservative": 0.5}
    assert summary["control"]["aux"]["avg_at_n"] == 0.0
    assert summary["delta"]["aux"] == {
        "metric": "fake_aux", "avg_at_n": 0.5,
        "avg_at_n_conservative": 0.5}
    # JSON-safe, on disk and identical.
    assert read_json(_summary_path(ctx)) == summary


def test_without_a_declaration_aux_is_null_everywhere(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, "split")
    summary = _run_stage6(ctx, monkeypatch, RecordingPlugin(),
                          _tasks("t1", "t2"))
    assert summary["with_bank"]["aux"] is None
    assert summary["control"]["aux"] is None
    assert summary["delta"]["aux"] is None


def test_aux_delta_conditional_none_when_an_arm_has_no_valid_trial():
    # Same honesty rule as the primary: a delta between a number and an
    # absent conditional average is null, not zero.
    with_bank = {"avg_at_n": 0.5, "avg_at_n_conservative": 0.5,
                 "pass_at_n": 0.5,
                 "aux": {"metric": "fake_aux", "avg_at_n": 0.5,
                         "avg_at_n_conservative": 0.5}}
    control = {"avg_at_n": None, "avg_at_n_conservative": 0.0,
               "pass_at_n": 0.0,
               "aux": {"metric": "fake_aux", "avg_at_n": None,
                       "avg_at_n_conservative": 0.0}}
    delta = final_test_delta(with_bank, control)
    assert delta["avg_at_n"] is None
    assert delta["aux"]["avg_at_n"] is None
    assert delta["aux"]["avg_at_n_conservative"] == pytest.approx(0.5)
