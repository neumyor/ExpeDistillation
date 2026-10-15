"""Shared skill-status operations of the acceptance drivers and K6.

``discard_skill`` (the terminal write for a skill given up on outside its
loop), ``stage3_candidates`` (the store scan both stage-4 drivers
enumerate) and ``tally_verdicts`` (the cumulative verdict accounting both
summaries carry). The exam and live drivers used to carry byte-identical
copies, kept apart only to protect the exam path's frozen prompt-name
surface -- a concern the lazy-register removal retired.
"""

from typing import Any, Dict, List

from expedistill.core.ids import is_stage3_candidate
from expedistill.core.models import Skill
from expedistill.infra.store import Store

__all__ = ["discard_skill", "stage3_candidates", "tally_verdicts"]


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


def tally_verdicts(store: Any, candidates: List[Skill]) -> Dict[str, Any]:
    """Cumulative verdicts of every candidate, read back from the store.

    "accepted" includes the skills stage 5 has since folded into a merge.
    A discarded skill's free-form reason is tallied as given; a missing
    reason is tallied instead of guessed at.
    """
    accepted = 0
    discarded: Dict[str, int] = {}
    for candidate in candidates:
        skill = store.load("skills", candidate.skill_id, Skill)
        if skill.status in ("accepted", "merged"):
            accepted += 1
        elif skill.status == "discarded":
            reason = skill.discard_reason or "unspecified"
            discarded[reason] = discarded.get(reason, 0) + 1
    return {"accepted": accepted, "discarded": discarded}
