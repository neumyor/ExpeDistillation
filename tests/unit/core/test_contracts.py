"""core/contracts.py: the frozen delivery / learn-operation shapes.

These tests freeze the FIELD SETS -- the join key and the value slots the
module boundaries doc names -- so any change to a shape has to touch this
file too (which is the point of a frozen contract: changes are deliberate
and reviewed). The execution -> acceptance record is
``core.answer_sheet.ExecutionRecord``, covered by its own test file.
"""

from dataclasses import fields

from expedistill.core.contracts import (
    ExecutionUnitList,
    ExecutionUnitRow,
    TaskListRow,
)


def _field_names(cls):
    return [f.name for f in fields(cls)]


def test_execution_unit_row_fields():
    assert _field_names(ExecutionUnitRow) == [
        "unit_id", "task_ref", "skills_injected", "skills_block", "aux",
    ]


def test_task_list_row_fields():
    assert _field_names(TaskListRow) == [
        "target_kind", "target_id", "action", "reason", "basis_unit",
    ]


def test_shapes_construct_with_plain_values():
    unit = ExecutionUnitRow(
        unit_id="p03:practice:tb:task1:weak:abcd0123", task_ref="tb:task1",
        skills_injected=["type/001/skill"], skills_block="", aux={})
    task = TaskListRow(
        target_kind="skill", target_id="type/001/skill",
        action="revise_content", reason="opened before the failing step",
        basis_unit=unit.unit_id)
    assert (unit.unit_id, task.basis_unit) == (unit.unit_id,) * 2


def test_execution_unit_list_is_a_plain_list_alias():
    units: ExecutionUnitList = [ExecutionUnitRow(
        unit_id="u", task_ref="t", skills_injected=None,
        skills_block="", aux={})]
    assert len(units) == 1
