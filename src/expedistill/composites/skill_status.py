"""Shared skill-status operations.

``discard_skill`` (the terminal write for a skill given up on outside its
loop) and ``stage3_candidates`` (the store scan that enumerates the
candidate skills).
"""

from typing import List

from expedistill.core.ids import is_stage3_candidate
from expedistill.core.models import Skill
from expedistill.infra.store import Store

__all__ = ["discard_skill", "stage3_candidates"]


def discard_skill(store: Store, skill: Skill, reason: str) -> Skill:
    """Store the LATEST version of ``skill`` as discarded with ``reason``.

    For callers that give up on a skill outside the loop (stage 4 after a
    unit failure): the stored skill may be newer than the object they hold,
    so the stored one is what gets the terminal status.
    """
    if store.exists("skills", skill.skill_id):
        skill = store.load("skills", skill.skill_id, Skill)
    skill.status = "discarded"
    skill.discard_reason = reason
    store.save(skill, "skills")
    return skill


def stage3_candidates(store: Store) -> List[Skill]:
    """Every stored stage-3 candidate skill, in skill_id order.

    A candidate's id is the stage-3 id of its own subtask, so
    ``bank/merge/NNNN`` products are excluded -- they are K5's, proven by
    regression exams, never re-accepted here.
    """
    candidates = []
    # store.ids is sorted, so the iteration order is deterministic.
    for skill_id in store.ids("skills"):
        skill = store.load("skills", skill_id, Skill)
        if is_stage3_candidate(skill):
            candidates.append(skill)
    return candidates
