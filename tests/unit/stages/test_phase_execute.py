"""The execute-phase driver -- baseline, practice and test phases over
the fake benchmark.

Fake plugin through the real import machinery (the stub finder in
tests/helpers/fake_plugin.py), fake model handles from helpers.fake_llm, phase specs
built directly (core.config's parsed shapes). The independent probes
are the plugin's call records, the FakeClient call counts (a rerun
makes zero model calls) and the phase directory's persisted units /
report rows.
"""

import hashlib
import json
from pathlib import Path

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import ConfigError
from expedistill.composites import (
    batch_execute as batch_module,
    evolving_text,
    phase_bank,
    phase_report,
    shortfall,
    unit_ids,
    verdicts,
)
from expedistill.core import vocabulary
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    ExternalControlSpec,
    LearnPhaseSpec,
    PhaseInstance,
)
from expedistill.core.ids import (
    make_task_id,
    make_trajectory_id,
    make_type_id,
    parse_trajectory_id,
)
from expedistill.core.models import EvolvingText, Trajectory
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.composites.skill_use import skill_read_events
from expedistill.inject.skills import bank_reference, format_playbook_block
from tests.helpers.fake_llm import make_fake_handle
from expedistill.methods.delivery import folder_delivery as skill_folder
from expedistill.methods.delivery import units as delivery_units
from expedistill.stages import phase_execute
from expedistill.stages.phase_execute import engine
from expedistill.stages.context import RunSettings, StageContext
from tests.helpers.configs import reference_method
from tests.helpers.skills import MARKED, make_skill
from tests.helpers.fake_plugin import serve_plugin

pytestmark = pytest.mark.usefixtures("clean_registry")

_FAKE = "fakebench"
TASK_IDS = ("t1", "t2", "t3")

class PhasePlugin(BenchmarkPlugin):
    """The fixed task set; ``behavior(record)`` decides each run's
    outcome (default success). The call records are the independent
    probe for order and resume assertions; a record carries the task's
    meta, so a folder unit's manifest is visible to the probe."""

    skill_folder_prompt_name = "FAKE_TASK_FOLDER"

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
                  "skills_block": skills_block, "meta": dict(task.meta)}
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


def make_config(**overrides):
    # A legal v4 method (the parse_phases checklist runs at construction,
    # so these ExperimentConfigs freeze the way a loaded one does). The
    # phases the tests RUN are constructed directly through the spec
    # helpers below; this sequence only has to be a legal method.
    defaults = dict(
        benchmark=_FAKE,
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        n_attempts_strong=1,
        n_attempts_weak=2,
        rules={},
        sequence=["baseline"],
        phases={"baseline": {"kind": "execute", "role": "baseline",
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
        "judge": make_fake_handle("judge"),
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
    """K1 at one infra retry and zero backoff so failing-task tests stay
    fast and countable."""
    monkeypatch.setattr(batch_module, "_INFRA_RETRIES", 1)
    monkeypatch.setattr(batch_module, "_INFRA_BACKOFF", (0.0, 0.0))


def baseline_spec(**overrides):
    spec = dict(role="baseline", tasks="train",
                delivery={"name": "none"},
                execution={"name": "real",
                           "arms": ["executor_strong", "executor_weak"]})
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def practice_spec(**overrides):
    spec = dict(role="practice", tasks="train",
                delivery={"name": "folder"},
                execution={"name": "real_folder"})
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def reference_config(**overrides):
    """A config whose sequence is the reference method (practice phases
    at p03 / p05 read the learn phases at p02 / p04)."""
    return make_config(**{**reference_method(), **overrides})


def c_spec(**overrides):
    spec = dict(role="test", tasks="test",
                delivery={"name": "whole_bank"},
                execution={"name": "real"},
                compare=CompareSpec(against="fresh_bare"))
    spec.update(overrides)
    return ExecutePhaseSpec(**spec)


def phase_of(index, name, spec, task_ids=None):
    label = f"p{index + 1:02d}:{name}"
    return PhaseInstance(index=index, name=name, label=label, spec=spec,
                         task_ids=tuple(task_ids if task_ids is not None
                                        else TASK_IDS))


def make_card(serial=1, fields=MARKED, version=1, status="active"):
    """The card of subtask type ``type/<serial>``."""
    return make_skill(make_type_id(serial), version=version, status=status,
                      **{**fields, "summary": f"Card {serial}."})


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


def write_playbook(ctx, label, text):
    """The playbook a learn phase leaves for the next execute phase."""
    evolving_text.write_text(ctx.run_dir, EvolvingText(
        kind=evolving_text.PLAYBOOK, text=text, version=1,
        phase_label=label))


def read_report(ctx, phase):
    """The phase's trial-level report, read from its artifacts."""
    report = phase_report.read_phase_report(
        phase_bank.phase_bank_dir(ctx.run_dir, phase.label))
    if report is None:
        raise AssertionError(
            f"phase {phase.label!r} wrote no trial-level artifacts")
    return report


def report_rows(ctx, phase):
    """The phase's trial-level unit summaries, read from its artifacts."""
    return read_report(ctx, phase).units


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
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    phase = phase_of(0, "baseline", baseline_spec())

    result = phase_execute.run(ctx, phase)

    assert result.label == "p01:baseline"
    assert result.summary["role"] == "baseline"
    # Strong once per task, weak twice per task (the frozen depths).
    strong_calls = [call for call in plugin.calls
                    if call["handle"].role == "strong"]
    weak_calls = [call for call in plugin.calls
                  if call["handle"].role == "weak"]
    assert len(strong_calls) == 3
    assert len(weak_calls) == 6
    rows = report_rows(ctx, phase)
    assert len(rows) == 6  # 3 tasks x 2 roles (baseline phases split by role)
    assert all(row.status == vocabulary.STATUS_NORMAL for row in rows)
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


def _record_rollout_workers(monkeypatch):
    seen = []

    def spy(real):
        def wrapper(**kwargs):
            seen.append(kwargs.get("workers"))
            return real(**kwargs)
        return wrapper

    monkeypatch.setattr(engine, "batch_execute",
                        spy(engine.batch_execute))
    return seen


@pytest.mark.parametrize("settings, expected", [
    pytest.param({"workers": 128, "rollout_workers": 64}, 64, id="limit"),
    pytest.param({"workers": 8}, 8, id="follows_workers"),
])
def test_rollouts_are_dispatched_with_the_rollout_limit(
        monkeypatch, tmp_path, settings, expected):
    serve_plugin(monkeypatch, PhasePlugin())
    seen = _record_rollout_workers(monkeypatch)
    ctx = make_ctx(tmp_path, make_config(), **settings)

    phase_execute.run(ctx, phase_of(0, "baseline", baseline_spec()))

    assert seen and set(seen) == {expected}


def test_baseline_leaves_the_bank_unchanged_r4(monkeypatch, tmp_path):
    # R4's byte invariant holds for every phase that makes no bank calls.
    serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    before = snapshot_bank(ctx)

    phase_execute.run(ctx, phase_of(0, "baseline", baseline_spec()))
    assert snapshot_bank(ctx) == before
    card = make_card()
    ctx.store.save(card, "skills")
    write_bank_artifact(ctx, "p02:learn", [card])
    before = snapshot_bank(ctx)
    phase_execute.run(ctx, phase_of(2, "practice", practice_spec()))
    assert snapshot_bank(ctx) == before
    write_bank_artifact(ctx, "p04:learn", [card])
    before = snapshot_bank(ctx)
    phase_execute.run(ctx, phase_of(4, "test", c_spec(compare=CompareSpec(
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


def _holes_setup(monkeypatch, tmp_path, role):
    """A phase whose task t2 dies on infrastructure, plus its hole ids and
    the file the accepted holes land in."""
    _pin_k1(monkeypatch)

    def behavior(record):
        if record["task_id"] == "t2":
            raise InfrastructureError("sandbox exploded")
        return _outcome(record["task_id"], True)

    plugin = serve_plugin(monkeypatch, PhasePlugin(behavior))
    if role == "baseline":
        ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
        phase = phase_of(0, "baseline", baseline_spec())
        stem = "p01_baseline"
        holes = [f"traj/{stem}/fakebench:t2/strong/00",
                 f"traj/{stem}/fakebench:t2/weak/00"]
        accepted_path = shortfall.stage1_shortfall_path(ctx.run_dir)
    else:
        ctx = make_ctx(tmp_path, reference_config(n_attempts_weak=1))
        write_bank_artifact(ctx, "p02:learn", [make_card()])
        phase = phase_of(2, "practice", practice_spec())
        holes = [make_trajectory_id("p03:practice", _prefixed("t2"),
                                    "weak", 0)]
        accepted_path = (phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
                         / "weak" / shortfall.SHORTFALL_NAME)
    return plugin, ctx, phase, holes, accepted_path


@pytest.mark.parametrize("role", ["baseline", "practice"])
def test_redispatch_budget_then_exhausted_hole(monkeypatch, tmp_path, role):
    plugin, ctx, phase, holes, accepted_path = _holes_setup(
        monkeypatch, tmp_path, role)
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)

    with pytest.raises(InfrastructureError, match="infra_missing"):
        phase_execute.run(ctx, phase)

    # The fully-missing task has no report row yet (the frozen reader's
    # universe is the store plus the accepted record); the summary names
    # the hole and the raise stops the sequence.
    assert read_json(directory / "phase_summary.json")["infra_missing"] \
        == holes
    state = read_json(directory / "weak" / phase_execute.REDISPATCH_NAME)
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

    accepting = make_ctx(tmp_path, ctx.config, handles=ctx.handles,
                         allow_infra_shortfall=True)
    summary = phase_execute.run(accepting, phase).summary
    assert summary["infra_shortfall_accepted"] is True
    assert summary["complete"] is False  # the hole stays a hole
    assert summary["infra_accepted"] == holes
    assert read_json(accepted_path)["unit_ids"] == holes
    # With the holes durably accepted the report row appears: the
    # accepted record widens the reader's universe, and every unit of
    # the task being a hole reads as all_holes.
    hole_rows = [row for row in report_rows(ctx, phase)
                 if "t2" in row.unit_id and row.role == "weak"]
    assert hole_rows
    for row in hole_rows:
        assert row.status == vocabulary.STATUS_NOT_TESTABLE
        assert row.cause == vocabulary.CAUSE_ALL_HOLES

    # Durable acceptance: a later resume WITHOUT the flag never re-raises,
    # re-runs nothing and leaves the summary unchanged.
    calls_before = len(plugin.calls)
    assert phase_execute.run(ctx, phase).summary == summary
    assert len(plugin.calls) == calls_before


def test_unscored_runs_report_all_holes(monkeypatch, tmp_path):
    # Every run comes back stored without a scorer verdict (R2b's normal
    # reachable state): the settled zero-measurement cause, all_holes --
    # never invalid_redispatch_exhausted, because nothing is missing and
    # no dispatch died. (A fresh sampling attempt would extend the depth
    # beyond the frozen n_attempts, a shape no frozen reader represents;
    # that channel is Wave 3's, disclosed in the wave report.)
    plugin = serve_plugin(
        monkeypatch, PhasePlugin(lambda record: _outcome(
            record["task_id"], None)))
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    phase = phase_of(0, "baseline", baseline_spec(acceptance="scorer"))

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
    assert weak_row.status == vocabulary.STATUS_NOT_TESTABLE
    assert weak_row.cause == vocabulary.CAUSE_ALL_HOLES
    assert len(weak_row.artifacts["sheet_ids"]) == 1
    assert weak_row.valid_count == 0
    assert summary["complete"] is True  # nothing is missing


# ---------------------------------------------------------------------------
# The practice phase
# ---------------------------------------------------------------------------

def _read_first(record):
    """A run that opens the first listed card at step 0 (when the folder
    lists one); only t1 succeeds."""
    folder = record["meta"].get("skill_folder") or {}
    steps = []
    if folder.get("files"):
        steps.append({"action": f"ReadSkill[{folder['files'][0]['filename']}]",
                      "observation": "the card"})
    steps.append({"action": f"exec {record['task_id']}", "observation": "ok"})
    return RunOutcome(steps=steps, correctness=record["task_id"] == "t1")


def test_practice_runs_the_executor_with_the_active_cards(monkeypatch,
                                                          tmp_path):
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, reference_config())
    active, discarded = make_card(1), make_card(2, status="discarded")
    write_bank_artifact(ctx, "p02:learn", [active, discarded])
    write_playbook(ctx, "p02:learn", "Always look first.")
    phase = phase_of(2, "practice", practice_spec(playbook="evolved"))

    summary = phase_execute.run(ctx, phase).summary

    # Only the weak executor ran, at its global depth, on every task.
    assert {call["handle"].role for call in plugin.calls} == {"weak"}
    assert len(plugin.calls) == len(TASK_IDS) * 2
    # One phase-level folder: the active card only, the playbook beside it.
    manifests = [call["meta"]["skill_folder"] for call in plugin.calls]
    assert all(manifest == manifests[0] for manifest in manifests)
    assert [entry["skill_id"] for entry in manifests[0]["files"]] == [
        active.skill_id]
    assert manifests[0]["playbook_text"] == "Always look first."
    folder_dir = Path(plugin.calls[0]["meta"]["skill_folder_dir"])
    assert (folder_dir / skill_folder.skill_filename(
        active.skill_id)).is_file()
    assert not (folder_dir / skill_folder.skill_filename(
        discarded.skill_id)).exists()
    # The runs land in the run-level store, tagged with this phase.
    assert sorted(ctx.store.ids("trajectories")) == sorted(
        make_trajectory_id("p03:practice", _prefixed(task), "weak", attempt)
        for task in TASK_IDS for attempt in range(2))
    assert summary["role"] == "practice"
    assert summary["tasks"] == "train"
    assert summary["executor"] == "weak"
    assert summary["n_attempts"] == 2
    assert summary["bank_reference"] == bank_reference([active])
    assert summary["playbook_shown"] is True
    assert summary["complete"] is True
    assert summary["metrics"]["tasks"] == len(TASK_IDS)
    rows = report_rows(ctx, phase)
    assert {row.arm for row in rows} == {"weak"}
    assert all(row.success_count == 2 for row in rows)
    # A rerun of the finished phase executes nothing.
    calls_before = len(plugin.calls)
    phase_execute.run(ctx, phase)
    assert len(plugin.calls) == calls_before


def test_practice_runs_its_task_list_at_its_attempts(monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, reference_config())
    write_bank_artifact(ctx, "p02:learn", [make_card()])
    phase = phase_of(2, "practice", practice_spec(
        tasks="val", execution={"name": "real_folder", "attempts": 3}),
        task_ids=("t3",))

    summary = phase_execute.run(ctx, phase).summary

    assert [call["task_id"] for call in plugin.calls] == ["t3"] * 3
    assert summary["tasks"] == "val"
    assert summary["n_attempts"] == 3
    assert sorted(ctx.store.ids("trajectories")) == [
        make_trajectory_id("p03:practice", _prefixed("t3"), "weak", attempt)
        for attempt in range(3)]


def test_practice_sheets_carry_the_trajectory_and_the_folder(monkeypatch,
                                                             tmp_path):
    serve_plugin(monkeypatch, PhasePlugin(_read_first))
    ctx = make_ctx(tmp_path, reference_config(n_attempts_weak=1))
    card = make_card()
    write_bank_artifact(ctx, "p02:learn", [card])
    write_playbook(ctx, "p02:learn", "Look first.")
    phase = phase_of(2, "practice", practice_spec(playbook="evolved"))

    phase_execute.run(ctx, phase)

    sheets = read_report(ctx, phase).sheets
    assert len(sheets) == len(TASK_IDS)
    for sheet in sheets:
        artifacts = sheet.execution.artifacts
        trajectory = ctx.store.load("trajectories",
                                    artifacts["trajectory_id"], Trajectory)
        assert trajectory.task_id == sheet.execution.task_ref
        folder = artifacts["skill_folder"]
        assert [entry["skill_id"] for entry in folder["files"]] == [
            card.skill_id]
        assert card.skill_id in folder["catalog_text"]
        assert folder["playbook_text"] == "Look first."
        # What the learn phase recovers: the card was opened at step 0.
        assert [(event["skill_id"], event["step_index"])
                for event in skill_read_events(trajectory, folder)] == [
            (card.skill_id, 0)]


def test_practice_summary_reports_the_mechanism_metrics(monkeypatch,
                                                        tmp_path):
    serve_plugin(monkeypatch, PhasePlugin(_read_first))
    ctx = make_ctx(tmp_path, reference_config(n_attempts_weak=1))
    first, second = make_card(1), make_card(2)
    write_bank_artifact(ctx, "p02:learn", [first, second])

    summary = phase_execute.run(
        ctx, phase_of(2, "practice", practice_spec())).summary

    use = summary["skill_use"]
    assert (use["trials"], use["valid"], use["success"]) == (3, 3, 1)
    assert use["success_rate"] == pytest.approx(1 / 3)
    assert (use["opened_any"], use["opened_any_success"]) == (3, 1)
    assert use["cards"] == {
        first.skill_id: {"opened": 3, "opened_success": 1},
        second.skill_id: {"opened": 0, "opened_success": 0}}
    assert summary["skill_files_read"] == {
        make_trajectory_id("p03:practice", _prefixed(task), "weak", 0):
            [first.skill_id]
        for task in TASK_IDS}
    assert summary["metrics"]["pass_at_n"] == pytest.approx(1 / 3)


def test_baseline_and_two_practice_phases_never_share_an_id(monkeypatch,
                                                            tmp_path):
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, reference_config(n_attempts_weak=1))
    card = make_card()

    phase_execute.run(ctx, phase_of(0, "baseline", baseline_spec()))
    write_bank_artifact(ctx, "p02:learn", [card])
    phase_execute.run(ctx, phase_of(2, "practice", practice_spec()))
    # The same bank again: only the phase tells the two practices apart.
    write_bank_artifact(ctx, "p04:learn", [card])
    phase_execute.run(ctx, phase_of(4, "practice", practice_spec()))

    stored = ctx.store.ids("trajectories")
    assert len(plugin.calls) == len(stored) == 4 * len(TASK_IDS)
    weak_t1 = sorted(parse_trajectory_id(trajectory_id).phase_label
                     for trajectory_id in stored
                     if parse_trajectory_id(trajectory_id).task_id
                     == _prefixed("t1")
                     and parse_trajectory_id(trajectory_id).role == "weak")
    assert weak_t1 == ["p01:baseline", "p03:practice", "p05:practice"]


def test_an_empty_bank_still_runs_as_a_folder_unit(monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, reference_config(n_attempts_weak=1))
    write_bank_artifact(ctx, "p02:learn", [make_card(status="discarded")])
    write_playbook(ctx, "p02:learn", "Look first.")
    phase = phase_of(2, "practice", practice_spec(playbook="evolved"))

    summary = phase_execute.run(ctx, phase).summary

    assert len(plugin.calls) == len(TASK_IDS)
    for call in plugin.calls:
        manifest = call["meta"]["skill_folder"]
        assert manifest["files"] == []
        assert manifest["catalog_text"] == ""
        assert manifest["playbook_text"] == "Look first."
        assert Path(call["meta"]["skill_folder_dir"]).is_dir()
        assert call["skills_block"] == format_playbook_block("Look first.")
    assert summary["bank_reference"] == bank_reference([])
    assert summary["skill_use"]["cards"] == {}


def test_the_whole_bank_test_delivers_active_cards_only(monkeypatch,
                                                        tmp_path):
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    active = make_card(1)
    discarded = make_card(2, status="discarded", fields={
        **MARKED, "when_to_use": "The disk is full."})
    write_bank_artifact(ctx, "p02:learn", [active, discarded])

    summary = phase_execute.run(ctx, phase_of(2, "test", c_spec())).summary

    blocks = {call["skills_block"] for call in plugin.calls
              if call["skills_block"]}
    assert len(blocks) == 1
    block = blocks.pop()
    assert MARKED["when_to_use"] in block
    assert "The disk is full." not in block
    assert summary["bank_reference"] == bank_reference([active])


# ---------------------------------------------------------------------------
# The final-test phase (C)
# ---------------------------------------------------------------------------


def test_c_phase_fresh_bare_comparison(monkeypatch, tmp_path):
    serve_plugin(monkeypatch, PhasePlugin())
    config = make_config(n_attempts_weak=1)
    ctx = make_ctx(tmp_path, config)
    write_bank_artifact(ctx, "p02:learn", [make_card()])

    phase = phase_of(2, "test", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS)
    result = phase_execute.run(ctx, phase).summary

    assert result["control_source"] == "fresh_bare_run"
    # Both arms ran on the same task set: the delta is over identical
    # denominators, and both summaries cover every task.
    assert result["with_bank"]["tasks"] == result["control"]["tasks"] == 3
    assert result["delta"]["pass_at_n"] == pytest.approx(0.0)
    # C writes trial-level answer sheets, both arms included.
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    test_report = phase_report.read_phase_report(directory)
    assert test_report is not None
    assert {unit.arm for unit in test_report.units} == {"with_bank", "control"}


def test_c_phase_external_campaign_control_zero_execution(
        monkeypatch, tmp_path):
    """``against: external_campaign``: the control is the pinned score
    file's numbers -- zero control executions, no control store, the
    file's bytes copied into the phase directory and the delta computed
    against the aligned first-n rewards."""
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    config = make_config(n_attempts_weak=1)
    ctx = make_ctx(tmp_path, config)
    write_bank_artifact(ctx, "p02:learn", [make_card()])

    rewards = {"t1": [1.0, 0.0, 1.0, 0.0, 0.0],
               "t2": [0.0, 0.0, 0.0, 1.0, 1.0],
               "t3": [1.0, 0.0, 0.0, 0.0, 0.0]}
    payload = {"tasks": {f"qwen:{task}": {"n": 5, "rewards": values,
                                          "success_rate": sum(values) / 5}
                        for task, values in rewards.items()}}
    score_path = tmp_path / "scores.json"
    score_path.write_text(json.dumps(payload), encoding="utf-8")
    sha = hashlib.sha256(score_path.read_bytes()).hexdigest()

    phase = phase_of(2, "test", c_spec(compare=CompareSpec(
        against="external_campaign",
        external=ExternalControlSpec(path=str(score_path), sha256=sha,
                                     model_key="qwen"))),
        task_ids=TASK_IDS)
    result = phase_execute.run(ctx, phase).summary

    # Only the with-bank arm executed (3 tasks x 1 attempt).
    assert len(plugin.calls) == 3
    # The control never ran: no control store, no shortfall record.
    control_dir = phase_bank.phase_bank_dir(ctx.run_dir, phase.label) \
        / "control"
    assert not control_dir.exists()
    # The summary owns the comparison the old stage-6 ruling moved out:
    # control_source keeps the old vocabulary, the delta is computed
    # against the aligned first-n rewards (here n=1: t1 1.0, t2 0.0,
    # t3 1.0 -- per-task mean then task mean).
    assert result["control_source"] == "external_campaign"
    assert result["with_bank"]["tasks"] == result["control"]["tasks"] == 3
    assert result["control"]["avg_at_n"] == pytest.approx(2 / 3)
    assert result["control"]["pass_at_n"] == pytest.approx(2 / 3)
    assert result["delta"]["avg_at_n"] == pytest.approx(1.0 - 2 / 3)
    assert result["infra_missing"]["control"] == []
    # The exact bytes the control consumed are in the phase directory,
    # and the summary's audit record reproduces them without the file.
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    copy_path = directory / "external_control.json"
    assert hashlib.sha256(copy_path.read_bytes()).hexdigest() == sha
    record = result["external_control"]
    assert record["sha256"] == sha
    assert record["model_key"] == "qwen"
    assert record["n_attempts"] == 1
    assert record["rewards"] == {_prefixed("t1"): [1.0],
                                 _prefixed("t2"): [0.0],
                                 _prefixed("t3"): [1.0]}


def test_c_phase_reruns_every_time(monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config(n_attempts_weak=1))
    write_bank_artifact(ctx, "p02:learn", [make_card()])

    phase_execute.run(ctx, phase_of(2, "test", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS))
    calls_after_first = len(plugin.calls)
    phase_execute.run(ctx, phase_of(4, "test", c_spec(compare=CompareSpec(
        against="fresh_bare")), task_ids=TASK_IDS))

    # No reuse across test phases: every unit re-ran (the frozen rule).
    assert len(plugin.calls) == 2 * calls_after_first


# ---------------------------------------------------------------------------
# Unit ids, params, the driver's guards
# ---------------------------------------------------------------------------

def test_unit_id_rebuild_matches_the_delivery_sha8():
    row = delivery_units.make_row(_prefixed("t1"),
                                  [{"skill_id": "s1", "version": 2}],
                                  "block bytes")
    full = unit_ids.make_unit_id(phase_label="p03:test", benchmark=_FAKE,
                                 task_ref=row.task_ref, role="weak",
                                 skills_injected=row.skills_injected)
    sha8 = hashlib.sha256(unit_ids.canonical_skills(
        row.skills_injected).encode("utf-8")).hexdigest()[:8]
    assert full == f"p03:test:{_FAKE}:{_prefixed('t1')}:weak:{sha8}"
    # The delivery-side id is exactly the join the rebuild consumes.
    assert row.unit_id == f"{_prefixed('t1')}:{sha8}"
    # The three raw shapes stay distinct keys (the frozen rule).
    none_row = delivery_units.make_row(_prefixed("t1"), None, "")
    empty_row = delivery_units.make_row(_prefixed("t1"), [], "")
    assert row.unit_id != none_row.unit_id != empty_row.unit_id


def test_run_rejects_a_learn_phase(tmp_path):
    ctx = make_ctx(tmp_path, make_config())
    phase = PhaseInstance(index=1, name="learn", label="p02:learn",
                          spec=LearnPhaseSpec(), task_ids=())
    with pytest.raises(ConfigError, match="learn-phase driver"):
        phase_execute.run(ctx, phase)


def test_execution_params_refuse_unknown_keys(tmp_path):
    ctx = make_ctx(tmp_path, make_config())
    bad = phase_of(2, "test", c_spec(
        execution={"name": "real", "surprise": 1}))
    with pytest.raises(ConfigError, match="surprise"):
        phase_execute.run(ctx, bad)
    arms_off_baseline = phase_of(
        2, "test", c_spec(execution={"name": "real",
                                     "arms": ["executor_weak"]}))
    with pytest.raises(ConfigError, match="arms"):
        phase_execute.run(ctx, arms_off_baseline)


def test_unknown_named_rule_fails_before_any_work(monkeypatch, tmp_path):
    serve_plugin(monkeypatch, PhasePlugin())
    ctx = make_ctx(tmp_path, make_config())
    phase = phase_of(0, "baseline", baseline_spec(acceptance="no_such_rule"))
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
    assert verdict == vocabulary.RUN_INVALID
    assert "pseudo zero" in basis
    # The verifier's evidence never came back (the log was missing).
    verdict, basis = rule({"correctness": None,
                           "metrics": {"verifier_ran": None}})
    assert verdict == vocabulary.RUN_INVALID
    assert "never came back" in basis
    # A run that died on a model output error.
    verdict, basis = rule({"correctness": None,
                           "metrics": {"exception_type":
                                       "ModelOutputError"}})
    assert verdict == vocabulary.RUN_INVALID
    assert "ModelOutputError" in basis
    # A plain missing verdict (no facts at all).
    verdict, _ = rule({"correctness": None, "metrics": {}})
    assert verdict == vocabulary.RUN_INVALID
    # The scorer's own answer decides when it exists.
    assert rule({"correctness": True, "metrics": {}})[0] == \
        vocabulary.RUN_SUCCESS
    assert rule({"correctness": False, "metrics": {}})[0] == \
        vocabulary.RUN_FAILURE
