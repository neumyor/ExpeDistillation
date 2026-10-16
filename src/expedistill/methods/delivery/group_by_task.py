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

from typing import Any, Dict, Mapping, Sequence, Tuple

from expedistill.composites.live_measurement import pending_groups
from expedistill.core import ids
from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store
from expedistill.inject import format_skills_block
from expedistill.methods.delivery import GroupByTaskParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]


def _resolve(bank: Mapping[str, Sequence[Skill]], tasks: Any,
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


def _legacy_resolve(ctx: Any, params: GroupByTaskParams, store: Store,
                    benchmark: str) -> Tuple[Dict[str, Any], None]:
    """(K1 with-group kwargs, None): the per-task group resolver.

    Delegates to the frozen signature per call: the resolver reads the
    store only at call time (once per task and attempt inside K1), so
    one resolution serves a whole round's dispatch and re-reads the
    survivors the round is actually about.
    """
    def skills_for_task(task: Any) -> Tuple[Any, str]:
        rows = _resolve(
            pending_groups(store),
            [ids.make_task_id(benchmark, task.task_id)], params)
        row = rows[0]
        return row.skills_injected, row.skills_block
    return {"skills_for_task": skills_for_task}, None


def resolve(*args: Any, **kwargs: Any) -> ExecutionUnitList:
    """resolve(bank, tasks, params) -> ExecutionUnitList  [frozen, R5].

    Wave 1 compat (removed in Wave 2, when the phase drivers replace the
    call sites): the module seam also accepts the live driver's legacy
    ``resolve(ctx, params, store, benchmark)`` and returns the K1
    per-task group resolver.
    """
    return units.dispatch(GroupByTaskParams, args, kwargs,
                          _legacy_resolve, _resolve)
