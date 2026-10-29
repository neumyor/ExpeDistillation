"""learn_add: the add landing resumes per skill, not per task."""

from types import SimpleNamespace

import pytest

from expedistill.core import ids
from expedistill.core.contracts import TaskListRow
from expedistill.infra.store import read_jsonl
from expedistill.methods.review.contracts import ReviewItem
from expedistill.stages import learn_add, learn_ledger
from tests.helpers.traces import baseline_sheet, write_baseline_sheets

BENCHMARK = "tb"
TASK_ID = ids.make_task_id(BENCHMARK, "t1")
TRAJECTORY_ID = ids.make_trajectory_id(TASK_ID, "strong", 0)
SUBTASK_IDS = (ids.make_subtask_id(TRAJECTORY_ID, 0),
               ids.make_subtask_id(TRAJECTORY_ID, 1))
SKILL_IDS = [ids.make_skill_id(subtask_id) for subtask_id in SUBTASK_IDS]


class _FlakyStore:
    """A minimal store whose save calls can fail on demand."""

    def __init__(self):
        self.saved = {}
        self.fail_on_call = None
        self.calls = 0

    def exists(self, kind, object_id):
        return object_id in self.saved

    def save(self, obj, kind):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("disk died")
        self.saved[obj.skill_id] = obj


def _package():
    experiences = [
        {"subtask": SimpleNamespace(
            subtask_id=subtask_id, trajectory_id=TRAJECTORY_ID,
            step_range=(index, index + 2), label=f"segment {index}"),
         "steps_view": "steps", "mode": "distill",
         "segment_position": ""}
        for index, subtask_id in enumerate(SUBTASK_IDS)
    ]
    return SimpleNamespace(target=TASK_ID, facts=[], segments=[],
                           source_experiences=experiences)


def _skill(subtask_id):
    return SimpleNamespace(skill_id=ids.make_skill_id(subtask_id), version=1,
                           subtask_id=subtask_id)


def _ctx(tmp_path, store):
    return SimpleNamespace(
        store=store,
        run_dir=tmp_path / "run",
        usage=None,
        config=SimpleNamespace(
            benchmark=BENCHMARK,
            rules={"trajectory_select": "all",
                   "segment_select": "strong_success_weak_failed"},
            effective_weak_profile=lambda: ""),
        handle=lambda role: SimpleNamespace(role=role))


def _phase():
    return SimpleNamespace(
        label="p02:learn",
        spec=SimpleNamespace(author={"name": "distill_diagnose"}))


def test_a_partial_add_resume_writes_the_missing_skill(tmp_path, monkeypatch):
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    write_baseline_sheets(tmp_path / "run", [baseline_sheet(TASK_ID, "strong", 0)])
    store = _FlakyStore()
    ctx = _ctx(tmp_path, store)
    row = TaskListRow(target_kind="task", target_id=TASK_ID,
                      action="add", reason="weak fails", basis_unit="u")
    item = ReviewItem(row=row, evidence=_package())

    resolved = []

    def fake_write_new(**kwargs):
        return [_skill(experience["subtask"].subtask_id)
                for experience in kwargs["package"].source_experiences]

    def fake_resolve(family, raw):
        resolved.append((family, dict(raw)))
        return SimpleNamespace(write_new=fake_write_new), object()

    monkeypatch.setattr(learn_add.method_registry, "resolve", fake_resolve)

    # The first pass saves skill 0, receipts it, then dies before skill 1.
    store.fail_on_call = 2
    with pytest.raises(RuntimeError, match="disk died"):
        learn_add._land_add(ctx, _phase(), item, phase_dir, "p01:baseline")

    store.fail_on_call = None
    learn_add._land_add(ctx, _phase(), item, phase_dir, "p01:baseline")

    assert sorted(store.saved) == sorted(SKILL_IDS)
    outcomes = read_jsonl(phase_dir / learn_ledger.OUTCOMES_NAME)
    assert [record["skill_id"] for record in outcomes] == SKILL_IDS
    origins = read_jsonl(phase_dir / "provenance.jsonl")
    assert [row["skill_id"] for row in origins] == SKILL_IDS
    # Every scene is the exact attempt that ran the skill's own trajectory.
    assert all(row["cause"] == "add" for row in origins)
    assert [row["scene"]["step"] for row in origins] == [0, 1]
    assert {row["scene"]["sheet_id"].split("::")[1] for row in origins} == {
        TRAJECTORY_ID}

    # A completed task replays without another write or receipt.
    learn_add._land_add(ctx, _phase(), item, phase_dir, "p01:baseline")
    assert len(read_jsonl(phase_dir / learn_ledger.OUTCOMES_NAME)) == 2

    # The configured author method is the driver that ran.
    assert resolved and resolved[0] == (
        "author", {"name": "distill_diagnose"})
