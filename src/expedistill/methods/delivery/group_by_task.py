"""The group_by_task delivery driver: inject a task's whole skill group.

The live-run delivery, extracted from the live driver's inline injection
resolver (M2): each task's injection is its group of pending candidate
skills at their CURRENT stored versions. Both values come from K6's
group derivation (``pending_groups`` / ``survivors_and_block``) -- the
same derivation ``audit_row`` freezes, so the dispatch-time freeze
compare enforces byte-identity with the frozen audit line across
resumes; that holds for round 0 and round 1 alike, because "survivors"
IS "pending members" and the un-revised round-0 group is its own
survivor set.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank`` the
previous learn phase's bank artifact as a group view -- one entry per
task that still has pending members, ``{prefixed task id: [Skill, ...]}``
(today's producer: ``composites.live_measurement.pending_groups``) --
``tasks`` the phase's tasks as the prefixed task ids that view is keyed
by, and ``params`` the empty GroupByTaskParams. One row per task: the
group's reference list and the block those members render to. An empty
group is a CodeBugError: an empty injection would be a bare run under
the with-skills label.
"""

from typing import Any, Mapping, Sequence

from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject import format_skills_block
from expedistill.methods.delivery import GroupByTaskParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]


def resolve(bank: Mapping[str, Sequence[Skill]], tasks: Any,
             params: GroupByTaskParams) -> ExecutionUnitList:
    """One row per task: its whole pending group's references + block."""
    rows = []
    for task in tasks:
        ref = units.task_ref(task)
        members = bank.get(ref, [])
        if not members:
            raise CodeBugError(
                f"task {ref!r} has no pending skill group to "
                "inject; it must not have been dispatched")
        references = [units.skill_reference(skill) for skill in members]
        rows.append(units.make_row(
            ref, references, format_skills_block(members)))
    return rows


