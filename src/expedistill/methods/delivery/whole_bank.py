"""The whole-bank delivery driver: one fixed injection for the whole arm.

The frozen v1 behaviour (README stage 6): every test task's weak run
sees the entire curated bank, and the with-bank trajectories record the
bank digest string as ``skills_injected`` (inject.bank_reference). The
scalar K1 kwargs are kept on purpose -- routing whole_bank through K1's
per-task resolver would turn the recorded ``skills_injected`` into a
reference list and change the trajectory bookkeeping.
"""

from typing import Any, Dict, Tuple

from expedistill.inject import bank_reference, format_skills_block

__all__ = ["resolve"]


def resolve(ctx: Any, bank: Any,
            test_tasks: Tuple[Any, ...]) -> Tuple[Dict[str, Any], None]:
    """(K1 with-bank kwargs, None): the whole bank, one fixed injection."""
    return {
        "skills_injected": bank_reference(bank),
        "skills_block": format_skills_block(bank),
    }, None
