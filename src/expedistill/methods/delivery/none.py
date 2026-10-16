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

from typing import Any

from expedistill.core.contracts import ExecutionUnitList
from expedistill.methods.delivery import NoneParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]


def resolve(bank: Any, tasks: Any, params: NoneParams) -> ExecutionUnitList:
    """One row per task: no skills, the empty block."""
    return [units.make_row(units.task_ref(task), None, "") for task in tasks]


