"""core/contracts.py: the frozen interface shapes (R0).

These tests freeze the FIELD SETS -- the join key, the value slots the
module boundaries doc names, and the one Optional field -- so any change
to a shape has to touch this file too (which is the point of a frozen
contract: changes are deliberate and reviewed).
"""

from dataclasses import fields

from expedistill.core.contracts import (
    ExecutionUnitList,
    ExecutionUnitRow,
    RunRecord,
    TaskListRow,
)


def _field_names(cls):
    return [f.name for f in fields(cls)]


def test_run_record_fields():
    assert _field_names(RunRecord) == [
        "unit_id", "task_ref", "skills_injected", "skills_block",
        "model_role", "model_name", "sample_index", "dispatch_index",
        "correctness", "metrics", "aux", "artifacts",
    ]


def test_execution_unit_row_fields():
    assert _field_names(ExecutionUnitRow) == [
        "unit_id", "task_ref", "skills_injected", "skills_block", "aux",
    ]


def test_task_list_row_fields():
    assert _field_names(TaskListRow) == [
        "target_kind", "target_id", "action", "reason", "basis_unit",
    ]


def test_shapes_construct_with_plain_values():
    run = RunRecord(
        unit_id="p00:baseline:tb:task1:weak:abcd0123", task_ref="tb:task1",
        skills_injected=None, skills_block="", model_role="weak",
        model_name="m", sample_index=0, dispatch_index=0,
        correctness=None, metrics={}, aux={}, artifacts={})
    unit = ExecutionUnitRow(
        unit_id=run.unit_id, task_ref="tb:task1", skills_injected=["s1"],
        skills_block="[s1]", aux={})
    task = TaskListRow(
        target_kind="skill", target_id="s1", action="keep",
        reason="improved", basis_unit=run.unit_id)
    assert (run.unit_id, unit.unit_id, task.basis_unit) == (run.unit_id,) * 3


def test_execution_unit_list_is_a_plain_list_alias():
    units: ExecutionUnitList = [ExecutionUnitRow(
        unit_id="u", task_ref="t", skills_injected=None,
        skills_block="", aux={})]
    assert len(units) == 1
