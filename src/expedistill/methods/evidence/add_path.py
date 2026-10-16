"""The evidence driver for the add path (a task's source experiences).

The add action's target is a TASK (boundaries section 2.5: "when
adding: which experiences to learn from"). Decision names the task in
the task list after the D-phase screening; this collector reads the
D-phase STRONG trajectories' segmentation results from the store, keeps
the segments the named segment_select rule passes, renders each one's
steps view from its source trajectory, and packages them as one
evidence package's ``source_experiences`` -- the material the author
module writes new skills from. No actions, no store writes, no model
calls: segmentation itself is the evidence family's ``segment`` method
(stage 2 / K2 territory); this driver only gathers what it produced.
"""

from typing import Any, List

from expedistill.blocks.select import select_subtasks
from expedistill.blocks.view import render_steps_view
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Subtask, Trajectory
from expedistill.infra.store import Store

from expedistill.methods.evidence.package import EvidencePackage

__all__ = ["collect_add_evidence"]


def collect_add_evidence(
    *,
    task_row: TaskListRow,
    store: Store,
    rule_name: str,
    view_rules: Any = None,
) -> EvidencePackage:
    """Collect one task's add-path evidence (its strong segments).

    ``task_row`` is the decision module's authorization (action ``add``,
    target a task id); ``rule_name`` is the configured segment_select
    rule (the same named rule stage 3 uses, resolved through
    blocks.select). Only strong-source subtasks are gathered: an add
    writes a procedure skill from what the strong model did right on a
    task the weak model fails (the diagnose mode belongs to revision
    evidence, not to a first skill). Each source experience carries the
    subtask, its rendered steps view and the write mode the author uses
    (``distill``); the package's segments pin the subtask ids and step
    ranges so the author's output stays traceable to its evidence.
    """
    if task_row.action != "add":
        raise ValueError(
            "collect_add_evidence needs a task list row with action "
            f"'add', got {task_row.action!r} on target "
            f"{task_row.target_id!r}")
    task_id = task_row.target_id
    subtasks = [store.load("subtasks", subtask_id, Subtask)
                for subtask_id in store.ids("subtasks")]
    subtasks = [sub for sub in subtasks if sub.task_id == task_id
                and sub.source == "strong"]
    subtasks = select_subtasks(subtasks, rule_name)

    source_experiences: List[dict] = []
    segments: List[dict] = []
    for sub in subtasks:
        trajectory = store.load("trajectories", sub.trajectory_id,
                                Trajectory)
        start, end = sub.step_range
        steps_view = render_steps_view(trajectory.steps[start:end],
                                      view_rules)
        source_experiences.append({
            "subtask": sub,
            "steps_view": steps_view,
            "mode": "distill",
        })
        segments.append({"subtask_id": sub.subtask_id,
                         "trajectory_id": sub.trajectory_id,
                         "step_range": [start, end]})
    if not source_experiences:
        raise ValueError(
            f"task {task_id!r} has no strong segment the rule "
            f"{rule_name!r} keeps: there is nothing to learn from, so "
            "the add task row contradicts the run's segmentation")
    return EvidencePackage(
        target=task_id,
        facts=[{"kind": "add_target_task", "text": task_id}],
        segments=segments,
        source_experiences=source_experiences,
    )
