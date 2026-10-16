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

from typing import Any, Dict, Sequence, Tuple

from expedistill.core.contracts import ExecutionUnitList
from expedistill.inject import bank_reference, format_skills_block
from expedistill.methods.delivery import WholeBankParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]

#: Stands in for a task when the legacy arm-level call passes an empty
#: task set: the bank pair is task-independent, so any ref yields it.
_NO_TASK = ""


def _resolve(bank: Sequence[Any], tasks: Any,
             params: WholeBankParams) -> ExecutionUnitList:
    """One row per task: the whole bank, one fixed injection."""
    injected = bank_reference(bank)
    block = format_skills_block(bank)
    return [units.make_row(units.task_ref(task), injected, block)
            for task in tasks]


def _legacy_resolve(ctx: Any, params: WholeBankParams, bank: Sequence[Any],
                    test_tasks: Any) -> Tuple[Dict[str, Any], None]:
    """(K1 with-bank kwargs, None): the arm-level scalar pair.

    Delegates to the frozen signature: the injection is task-
    independent, so the arm-level kwargs are one row's values.
    """
    row = _resolve(bank, list(test_tasks) or [_NO_TASK], params)[0]
    return ({"skills_injected": row.skills_injected,
             "skills_block": row.skills_block}, None)


def resolve(*args: Any, **kwargs: Any) -> ExecutionUnitList:
    """resolve(bank, tasks, params) -> ExecutionUnitList  [frozen, R5].

    Wave 1 compat (removed in Wave 2, when the phase drivers replace the
    call sites): the module seam also accepts stage 6's legacy
    ``resolve(ctx, params, bank, test_tasks)`` and returns the arm-level
    K1 scalar kwargs.
    """
    return units.dispatch(WholeBankParams, args, kwargs,
                          _legacy_resolve, _resolve)
