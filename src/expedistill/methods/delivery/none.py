"""The none delivery driver: the bare injection, nothing selected.

The empty twin of ``whole_bank`` (same operands, same row values): every
unit runs with no skills. The ``skills_injected`` a bare run records is
``None`` -- never ``[]``, which the retrieval driver reserves for a
below-threshold task inside a with-bank arm -- and the block is the
empty D9 block. In the R5 row semantics ``skills_injected=None`` IS the
control unit's marker. Extracted from stage 6's hard-coded bare control
arm (M2); in the v4 shape this is the D phase's delivery and the
fresh-bare control's spec.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
ignored on purpose (there is nothing to select from), ``tasks`` the
phase's tasks in any form carrying a task identity (a Task, an exam ref,
a prefixed task id), and ``params`` the empty NoneParams. One row per
task.
"""

from typing import Any, Dict, Tuple

from expedistill.core.contracts import ExecutionUnitList
from expedistill.methods.delivery import NoneParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]

#: Stands in for a task when the legacy arm-level call passes an empty
#: task set: the bare pair is task-independent, so any ref yields it.
_NO_TASK = ""


def _resolve(bank: Any, tasks: Any, params: NoneParams) -> ExecutionUnitList:
    """One row per task: no skills, the empty block."""
    return [units.make_row(units.task_ref(task), None, "") for task in tasks]


def _legacy_resolve(ctx: Any, params: NoneParams, bank: Any, test_tasks: Any
                    ) -> Tuple[Dict[str, Any], None]:
    """(K1 bare kwargs, None): the arm-level scalar pair.

    Delegates to the frozen signature: every row carries the same
    (None, "") pair, so the arm-level kwargs are one row's values.
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
    return units.dispatch(NoneParams, args, kwargs,
                          _legacy_resolve, _resolve)
