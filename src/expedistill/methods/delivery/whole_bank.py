"""The whole-bank delivery driver: one fixed injection for the whole arm.

The frozen v1 behaviour (README stage 6): every test task's weak run
sees the entire curated bank, and the with-bank trajectories record the
bank digest string as ``skills_injected`` (inject.bank_reference). The
scalar K1 kwargs are kept on purpose in the legacy seam -- routing
whole_bank through K1's per-task resolver would turn the recorded
``skills_injected`` into a reference list and change the trajectory
bookkeeping.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
the curated skill bank (Skill objects or bank rows, whatever
``inject.bank_reference`` accepts), ``tasks`` the phase's tasks in any
form carrying a task identity, and ``params`` the empty WholeBankParams.
One row per task, every row the same fixed (bank digest, D9 block)
pair.
"""

from typing import Any, Sequence

from expedistill.core.contracts import ExecutionUnitList
from expedistill.inject import bank_reference, format_skills_block
from expedistill.methods.delivery import WholeBankParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]


def resolve(bank: Sequence[Any], tasks: Any,
             params: WholeBankParams) -> ExecutionUnitList:
    """One row per task: the whole bank, one fixed injection."""
    injected = bank_reference(bank)
    block = format_skills_block(bank)
    return [units.make_row(units.task_ref(task), injected, block)
            for task in tasks]


