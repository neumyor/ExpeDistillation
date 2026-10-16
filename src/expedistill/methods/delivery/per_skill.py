"""The per_skill delivery driver: one skill, one single-skill block.

The v4 A-phase delivery (plan section 3): the exam execution driver
answers the exam with the ONE skill under test injected, exactly the
way K4 renders acceptance-time blocks today. That consumer first
appears in M4; the driver is registered and pinned by contract tests
now because the v4 shape fixes it. M2 deliberately wires no caller:
K4 (composites, layer 5) cannot call a layer-6 driver, so the
per-skill block keeps going through ``inject.format_skills_block``
directly until the exam execution driver exists.
"""

from typing import Any, Dict, Tuple

from expedistill.core.models import Skill
from expedistill.inject import format_skills_block
from expedistill.methods.delivery import PerSkillParams

__all__ = ["resolve"]


def resolve(ctx: Any, params: PerSkillParams, skill: Skill
            ) -> Tuple[Dict[str, str], None]:
    """(K3 with-skill kwargs, None): the single skill's D9 block.

    The block comes from the same layer-1 renderer K4 uses, so
    acceptance-time and test-time injection stay one rendering by
    construction.
    """
    return {"skills_block": format_skills_block([skill])}, None
