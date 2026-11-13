"""``discard_skill``: the terminal write for a skill given up on."""

from expedistill.core.models import Skill
from expedistill.infra.store import Store

__all__ = ["discard_skill"]


def discard_skill(store: Store, skill: Skill, reason: str) -> Skill:
    """Store the LATEST version of ``skill`` as discarded with ``reason``.

    The stored skill may be newer than the object the caller holds, so the
    stored one is what gets the terminal status.
    """
    if store.exists("skills", skill.skill_id):
        skill = store.load("skills", skill.skill_id, Skill)
    skill.status = "discarded"
    skill.discard_reason = reason
    store.save(skill, "skills")
    return skill
