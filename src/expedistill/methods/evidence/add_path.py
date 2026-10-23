"""The evidence driver for the add path (a task's source experiences).

The add action's target is a TASK (boundaries section 2.5: "when
adding: which experiences to learn from"). Decision names the task in
the task list after the baseline-phase screening; this collector reads the
baseline-phase trajectories' segmentation results from the store (strong
segments, plus weak ones when ``write_lessons`` is on), keeps
the segments the named segment_select rule passes, renders each one's
steps view from its source trajectory, and packages them as one
evidence package's ``source_experiences`` -- the material the author
module writes new skills from. No actions, no store writes, no model
calls: segmentation itself is the evidence family's ``segment`` method
(K2 territory -- ``methods.evidence.segmentation.segment_add_material``,
which the landing calls right before this collector reads the store);
this driver only gathers what it produced.
"""

from collections import Counter
from typing import Any, List

from expedistill.blocks.select import select_subtasks
from expedistill.composites.decision import ADD
from expedistill.blocks.view import render_steps_view
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Subtask, Trajectory
from expedistill.infra.store import Store

from expedistill.methods.evidence.package import EvidencePackage

__all__ = ["NoAddMaterial", "collect_add_evidence", "segment_position"]


def segment_position(subtask: Subtask, n_segments: int,
                     n_steps: int) -> str:
    """Where one segment sits in its trajectory, as plain text for G2.

    The steps view restarts its numbering at 0, so without this the
    author cannot tell an opening segment from a closing one -- the
    main hint for the card's trigger timing. Step numbers are the
    original trajectory's, 0-based, end inclusive.
    """
    start, end = subtask.step_range
    return (f"segment {subtask.segment_index + 1} of {n_segments} in its "
            f"trajectory; it covers original steps {start} to {end - 1} "
            f"out of {n_steps} steps (steps are numbered from 0)")


class NoAddMaterial(ValueError):
    """The task has no segment the rule keeps: a data fact, not a wiring
    bug. The decision authorizes an add off the WEAK arm's failure
    alone, so the collected roles may hold no learnable material --
    never run, all segments cut away by the segment rule, or K2
    exhausted the trajectories (terminal within a run).
    With segmentation wired into the add path this is reachable on real
    data; the landing records it (learn_tasks' ``skipped_no_material``
    receipt) where the old stage 3 answered with silence. A ValueError
    subclass because the contract violation shape (a caller handing in
    a task with material it knows exists) is still a bug and older
    tests assert ValueError."""


def collect_add_evidence(
    *,
    task_row: TaskListRow,
    store: Store,
    rule_name: str,
    write_lessons: bool = True,
    view_rules: Any = None,
) -> EvidencePackage:
    """Collect one task's add-path evidence (its strong segments, plus
    its weak segments when ``write_lessons`` is on).

    ``task_row`` is the decision module's authorization (action ``add``,
    target a task id); ``rule_name`` is the configured segment_select
    rule (the same named rule stage 3 uses, resolved through
    blocks.select). Strong segments teach what the strong model did
    right; with ``write_lessons`` on, weak segments teach which mistake
    the weak model made and what to do instead (the 2026-10-19 ruling;
    the default segment_select rule keeps strong successes and weak
    failures). Each source experience carries the subtask, its rendered
    steps view and the write mode the author uses -- ``distill`` for a
    strong segment, ``diagnose`` for a weak one; both write the same
    four-section skill. Each experience also carries its
    ``segment_position`` text (the author's trigger-timing hint). The
    package's segments pin the subtask ids and step ranges so the
    author's output stays traceable to its evidence.
    """
    if task_row.action != ADD:
        raise ValueError(
            "collect_add_evidence needs a task list row with action "
            f"'add', got {task_row.action!r} on target "
            f"{task_row.target_id!r}")
    task_id = task_row.target_id
    subtasks = [store.load("subtasks", subtask_id, Subtask)
                for subtask_id in store.ids("subtasks")]
    roles = ("strong", "weak") if write_lessons else ("strong",)
    subtasks = [sub for sub in subtasks if sub.task_id == task_id
                and sub.source in roles]
    # Counted before the segment rule drops any: the position is about
    # the trajectory's own cut, not about which segments are material.
    per_trajectory = Counter(sub.trajectory_id for sub in subtasks)
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
            "mode": "distill" if sub.source == "strong" else "diagnose",
            "segment_position": segment_position(
                sub, per_trajectory[sub.trajectory_id],
                len(trajectory.steps)),
        })
        segments.append({"subtask_id": sub.subtask_id,
                         "trajectory_id": sub.trajectory_id,
                         "step_range": [start, end]})
    if not source_experiences:
        raise NoAddMaterial(
            f"task {task_id!r} has no {'/'.join(roles)} segment the rule "
            f"{rule_name!r} keeps: there is nothing to learn from on "
            "this task")
    return EvidencePackage(
        target=task_id,
        facts=[{"kind": "add_target_task", "text": task_id}],
        segments=segments,
        source_experiences=source_experiences,
    )
