"""W1-D (R5): the delivery drivers' frozen resolve signature.

``resolve(bank, tasks, params) -> ExecutionUnitList`` -- asserted per
driver, positionally and by keyword, for every registered delivery
family entry. The Wave 1 legacy module seam was removed with the Wave 2
phase drivers' call-site rewrite (W2-A); the frozen form is the only
form. (tests/unit/methods/delivery/test_drivers.py pins the drivers'
operands.)

Decision doc: docs/decisions/execute_learn_repair_plan_20261016.md, R5.
"""

import pytest

from expedistill.benchmarks.base import Task
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.ids import make_type_id
from expedistill.core.models import Skill
from expedistill.inject.skills import bank_reference, format_skills_block
from expedistill.methods.registry import resolve_fixed
from expedistill.methods.delivery.params import (
    FolderParams,
    NoneParams,
    WholeBankParams,
)
from expedistill.methods.delivery import units
from tests.helpers.skills import MARKED, make_skill


def _skill(serial: int, status: str = "active") -> Skill:
    return make_skill(make_type_id(serial), **MARKED, status=status)


def _driver(name: str):
    """The registered driver module for a fixed name (the registry seam)."""
    return resolve_fixed("delivery", name)[0]


def _operands(name: str):
    """A minimal (bank, tasks, params) triple the driver resolves."""
    skill = _skill(1)
    task = Task(task_id="t1", instruction="restart the service")
    if name == "none":
        return None, [task], NoneParams()
    if name == "whole_bank":
        return [skill], [task], WholeBankParams()
    if name == "folder":
        return [skill], [task], FolderParams()
    raise AssertionError(f"unknown delivery driver {name!r}")


@pytest.mark.parametrize("name", ["none", "whole_bank", "folder"])
def test_frozen_signature_returns_unit_rows(name):
    # The one signature every delivery driver resolves: positional and
    # keyword spellings agree, and every row is a frozen ExecutionUnitRow.
    bank, tasks, params = _operands(name)
    driver = _driver(name)
    rows = driver.resolve(bank, tasks, params)
    assert driver.resolve(bank=bank, tasks=tasks, params=params) == rows
    assert isinstance(rows, list) and rows
    assert all(isinstance(row, ExecutionUnitRow) for row in rows)
    for row in rows:
        assert isinstance(row.unit_id, str) and row.unit_id
        assert isinstance(row.task_ref, str)
        assert isinstance(row.skills_block, str)
        assert isinstance(row.aux, dict)


def test_none_rows_are_control_units():
    # skills_injected=None is the control-unit marker; the block is the
    # empty D9 block. One row per task, input order.
    driver = _driver("none")
    tasks = [Task(task_id="t1", instruction="do t1"),
             Task(task_id="t2", instruction="do t2")]
    rows = driver.resolve(None, tasks, NoneParams())
    assert [(row.task_ref, row.skills_injected, row.skills_block)
            for row in rows] == [("t1", None, ""), ("t2", None, "")]
    assert all(row.unit_id == units.unit_id(row.task_ref, None)
               for row in rows)


def test_whole_bank_rows_carry_the_bank_digest():
    # The fixed injection: the bank digest string (never a reference
    # list -- that would change the trajectory bookkeeping) and the
    # whole-bank D9 block, identical on every row.
    bank = [_skill(1), _skill(2)]
    driver = _driver("whole_bank")
    rows = driver.resolve(bank, [Task(task_id="t1", instruction="do t1"),
                                 Task(task_id="t2", instruction="do t2")],
                         WholeBankParams())
    assert all(row.skills_injected == bank_reference(bank)
               and row.skills_block == format_skills_block(bank)
               for row in rows)
    assert [row.task_ref for row in rows] == ["t1", "t2"]


def test_unit_id_digest_rules():
    # The delivery-side join key: deterministic in (task_ref,
    # skills_injected), and the two empty-value semantics never collide.
    assert units.unit_id("t1", None) == units.unit_id("t1", None)
    assert units.unit_id("t1", None) != units.unit_id("t2", None)
    assert units.unit_id("t1", None) != units.unit_id("t1", [])
    assert units.unit_id("t1", None) != units.unit_id(
        "t1", [{"skill_id": "s", "version": 1}])


@pytest.mark.parametrize("name", ["none", "whole_bank", "folder"])
def test_malformed_resolve_calls_fail_loudly(name):
    # Neither convention accepts a half-spelled call; the dispatcher
    # refuses instead of guessing (the params position is the only
    # structural tell, and it is checked, never sniffed).
    driver = _driver(name)
    with pytest.raises(TypeError):
        driver.resolve(1, 2)
    with pytest.raises(TypeError):
        driver.resolve(bank=1, tasks=2, params=3, extra=4)
