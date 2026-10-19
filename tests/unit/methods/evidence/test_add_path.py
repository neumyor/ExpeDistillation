"""The add-path evidence driver: a task's source experiences (R6).

The add action's target is a task; the evidence is the D-phase strong
segments the configured segment_select rule keeps, each with its
rendered steps view. Fact-only, like every evidence package.
"""

import pytest

from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import (
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Step, Subtask, Trajectory
from expedistill.infra.store import Store
from expedistill.methods.evidence.package import ensure_fact_only

from expedistill.methods.evidence.add_path import collect_add_evidence

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")


def make_strong_traj(attempt):
    return Trajectory(
        trajectory_id=make_trajectory_id(TASK_ID, "strong", attempt),
        task_id=TASK_ID, benchmark=BENCHMARK, model_role="strong",
        model_name="strong-model", attempt_index=attempt,
        steps=[Step(action=f"do s{attempt} {i}",
                    observation=f"obs s{attempt} {i}") for i in range(4)],
        correctness=True,
    )


def make_subtask(trajectory_id, segment, success):
    return Subtask(
        subtask_id=make_subtask_id(trajectory_id, segment),
        task_id=TASK_ID, trajectory_id=trajectory_id, source="strong",
        trajectory_success=True, segment_index=segment,
        step_range=(1, 3), label="restart the service",
        description="restart the stuck service and verify",
        success=success)


def seeded_store(tmp_path):
    store = Store(tmp_path / "run")
    for attempt in range(2):
        store.save(make_strong_traj(attempt), "trajectories")
    kept = make_subtask(make_trajectory_id(TASK_ID, "strong", 0), 0, True)
    dropped = make_subtask(make_trajectory_id(TASK_ID, "strong", 0), 1,
                           False)   # a failed strong segment: not material
    for sub in (kept, dropped):
        store.save(sub, "subtasks")
    return store, kept, dropped


def add_row():
    return TaskListRow(target_kind="task", target_id=TASK_ID,
                       action="add", reason="the weak model fails this "
                       "task and has no skill for it", basis_unit="u")


def test_collects_the_kept_strong_segments_as_source_experiences(tmp_path):
    store, kept, dropped = seeded_store(tmp_path)

    package = collect_add_evidence(task_row=add_row(), store=store,
                                   rule_name="strong_success_weak_failed")

    ensure_fact_only(package)
    assert package.target == TASK_ID
    assert [e["subtask"].subtask_id for e in package.source_experiences] \
        == [kept.subtask_id]          # the failed segment is not material
    experience = package.source_experiences[0]
    assert experience["mode"] == "distill"
    assert "do s0 1" in experience["steps_view"]
    assert "do s0 0" not in experience["steps_view"]   # step_range honored
    assert package.segments == [{"subtask_id": kept.subtask_id,
                                 "trajectory_id": kept.trajectory_id,
                                 "step_range": [1, 3]}]
    assert package.old_skill is None


def test_refuses_when_nothing_is_left_to_learn_from(tmp_path):
    """A task whose strong segments all failed the rule has no material:
    the add task row contradicts the run's segmentation, a loud refusal
    rather than an empty package the author would silently skip."""
    store = Store(tmp_path / "run")
    store.save(make_strong_traj(0), "trajectories")
    store.save(make_subtask(make_trajectory_id(TASK_ID, "strong", 0), 0,
                            False), "subtasks")   # a failed strong segment
    with pytest.raises(ValueError, match="nothing to learn from"):
        collect_add_evidence(task_row=add_row(), store=store,
                             rule_name="strong_success_weak_failed")


def test_refuses_a_task_row_that_is_not_an_add(tmp_path):
    store, _, _ = seeded_store(tmp_path)
    row = TaskListRow(target_kind="task", target_id=TASK_ID,
                      action="revise", reason="r", basis_unit="u")
    with pytest.raises(ValueError, match="action 'add'"):
        collect_add_evidence(task_row=row, store=store,
                             rule_name="strong_success_weak_failed")
