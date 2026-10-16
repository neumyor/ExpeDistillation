"""The group_by_task delivery driver: inject a task's whole skill group.

The live-run delivery, extracted from the live driver's inline
injection resolver (M2): each task's injection is its group of pending
candidate skills at their CURRENT stored versions, as a per-task
``(references, block)`` resolver for K1. Both values come from K6's
``survivors_and_block`` -- the same derivation ``audit_row`` freezes,
so the dispatch-time freeze compare enforces byte-identity with the
frozen audit line across resumes; that holds for round 0 and round 1
alike, because "survivors" IS "pending members" and the un-revised
round-0 group is its own survivor set.
"""

from typing import Any, Dict, List, Tuple

from expedistill.composites.live_measurement import survivors_and_block
from expedistill.core import ids
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store
from expedistill.methods.delivery import GroupByTaskParams

__all__ = ["resolve"]


def resolve(ctx: Any, params: GroupByTaskParams, store: Store, benchmark: str
            ) -> Tuple[Dict[str, Any], None]:
    """(K1 with-group kwargs, None): the per-task group resolver.

    The resolver reads the store only at call time (once per task and
    attempt inside K1), so one resolution serves a whole round's
    dispatch and re-reads the survivors the round is actually about.
    An empty group is a CodeBugError: an empty injection would be a
    bare run under the with-skills label.
    """
    def skills_for_task(task: Any) -> Tuple[List[Dict[str, int]], str]:
        prefixed = ids.make_task_id(benchmark, task.task_id)
        members, block = survivors_and_block(store, prefixed)
        if not members:
            raise CodeBugError(
                f"task {prefixed!r} has no pending skill group to "
                "inject; it must not have been dispatched")
        references = [{"skill_id": skill.skill_id, "version": skill.version}
                      for skill in members]
        return references, block
    return {"skills_for_task": skills_for_task}, None
