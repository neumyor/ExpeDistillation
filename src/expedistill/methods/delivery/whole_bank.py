"""The whole-bank delivery driver: one fixed injection for the whole arm.

Every task of the arm sees the entire phase bank (the bank artifact the
latest learn phase left), and the with-bank trajectories record the bank
digest string as ``skills_injected`` (inject.bank_reference). The
per-task resolver returns that same digest and block for every task, so
the trajectory bookkeeping stays the frozen whole-bank shape (a digest
string, never a reference list).

The injection is the bank's ACTIVE cards only
(:func:`units.active_bank`): the phase artifacts carry discarded
members too, and they are never delivered.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
the skill bank (Skill objects or bank rows, whatever
``inject.bank_reference`` accepts), ``tasks`` the phase's tasks in any
form carrying a task identity, and ``params`` the WholeBankParams (``verbatim``, default off).
One row per task, every row the same fixed (bank digest, D9 block)
pair.
"""

from typing import Any, Sequence

from expedistill.core.contracts import ExecutionUnitList
from expedistill.inject.skills import bank_reference, format_skills_block
from expedistill.methods.delivery.params import WholeBankParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]


def resolve(bank: Sequence[Any], tasks: Any,
             params: WholeBankParams) -> ExecutionUnitList:
    """One row per task: the whole active bank, one fixed injection."""
    active = units.active_bank(bank)
    injected = bank_reference(active)
    block = format_skills_block(active, verbatim=params.verbatim)
    return [units.make_row(units.task_ref(task), injected, block)
            for task in tasks]


