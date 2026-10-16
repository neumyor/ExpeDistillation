"""The none delivery driver: the bare injection, nothing selected.

The empty twin of ``whole_bank`` (same call shape, same two scalar K1
kwargs): every unit of the arm runs with no skills. The
``skills_injected`` a bare run records is ``None`` -- never ``[]``,
which the retrieval driver reserves for a below-threshold task inside a
with-bank arm -- and the block is the empty D9 block. Extracted from
stage 6's hard-coded bare control arm (M2); in the v4 shape this is the
D phase's delivery and the fresh-bare control's spec (plan section 3).
"""

from typing import Any, Dict, Tuple

from expedistill.methods.delivery import NoneParams

__all__ = ["resolve"]


def resolve(ctx: Any, params: NoneParams, bank: Any,
            test_tasks: Tuple[Any, ...]) -> Tuple[Dict[str, Any], None]:
    """(K1 bare kwargs, None): no skills, the empty block.

    ``bank`` and ``test_tasks`` are the family's operands and are
    ignored on purpose -- there is nothing to select from.
    """
    return {"skills_injected": None, "skills_block": ""}, None
