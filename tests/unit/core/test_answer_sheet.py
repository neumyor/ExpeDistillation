"""Trial-level answer-sheet serialization unit tests.

``to_dict`` is the derived-artifact surface (the JSON export). The golden
answer sheets pin the per-field payloads; these tests keep the three
structural behaviours: steps flatten to plain dicts, the envelope nests
coverage, execution and grading, and a gap sheet keeps execution and
grading as None.
"""

from dataclasses import fields

from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
)
from expedistill.core.models import Step


def _execution(**overrides):
    base = dict(
        sheet_id="sheet-1",
        unit_id="unit-1",
        phase_label="p03:live",
        task_ref="tb:task-1",
        role="weak",
        arm=None,
        model_role="weak",
        model_name="model-a",
        sample_index=0,
    )
    base.update(overrides)
    return ExecutionRecord(**base)


def test_execution_record_to_dict_serializes_steps_as_plain_dicts():
    record = _execution(
        steps=[Step(action="ls", observation="a.txt"),
               Step(action="cat a.txt", observation="hello")],
        kind="trajectory",
        metrics={"reward": 1.0},
        raw_correctness=True,
    )
    payload = record.to_dict()

    assert set(payload) == {field.name for field in fields(ExecutionRecord)}
    assert payload["steps"] == [
        {"action": "ls", "observation": "a.txt"},
        {"action": "cat a.txt", "observation": "hello"},
    ]
    assert all(isinstance(step, dict) for step in payload["steps"])
    # The record itself keeps Step objects; only the serialized view flattens.
    assert isinstance(record.steps[0], Step)
    assert payload["kind"] == "trajectory"
    assert payload["metrics"] == {"reward": 1.0}
    assert payload["raw_correctness"] is True


def test_answer_sheet_to_dict_nests_execution_coverage_and_grading():
    coverage = CoverageRecord(
        unit_id="unit-1", attempt_index=0, present=True, status="present")
    execution = _execution(steps=[Step(action="a", observation="o")])
    grading = GradingRecord(verdict="failure", basis="basis", rule_name="r")
    sheet = AnswerSheet(
        sheet_id="sheet-1",
        phase_label="p03:live",
        coverage=coverage,
        execution=execution,
        grading=grading,
    )

    payload = sheet.to_dict()
    assert payload == {
        "sheet_id": "sheet-1",
        "phase_label": "p03:live",
        "coverage": coverage.to_dict(),
        "execution": execution.to_dict(),
        "grading": grading.to_dict(),
    }
    assert payload["execution"]["steps"] == [
        {"action": "a", "observation": "o"}]


def test_answer_sheet_to_dict_keeps_a_gap_sheet_execution_and_grading_none():
    coverage = CoverageRecord(
        unit_id="unit-1", attempt_index=0, present=False,
        status="missing", cause="accepted_hole")
    sheet = AnswerSheet(
        sheet_id="gap:unit-1:0",
        phase_label="p03:live",
        coverage=coverage,
    )
    payload = sheet.to_dict()
    assert payload["execution"] is None
    assert payload["grading"] is None
    assert payload["coverage"]["cause"] == "accepted_hole"
