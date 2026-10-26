"""Trial-level answer-sheet dataclasses' serialization unit tests.

``to_dict`` is the derived-artifact surface (the JSON export), so each
record kind is checked for its exact payload shape: a trajectory's steps
become plain dicts, an exam answer carries its answer text, an external
trial carries its rewards, and the trial envelope nests coverage,
execution and grading without inventing fields.
"""

from dataclasses import fields

from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSummary,
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


def test_execution_record_to_dict_keeps_an_absent_step_list_none():
    payload = _execution(steps=None, kind="external").to_dict()
    assert payload["steps"] is None


def test_execution_record_to_dict_carries_the_exam_answer_payload():
    record = _execution(
        sheet_id="att-1",
        task_ref="exam-1",
        kind="attempt",
        answer="42",
        skills_injected=[{"skill_id": "s1", "version": 2}],
        aux={"judge_verdict": True, "judge_reason": "correct"},
        raw_correctness=None,
    )
    payload = record.to_dict()
    assert payload["kind"] == "attempt"
    assert payload["answer"] == "42"
    assert payload["skills_injected"] == [{"skill_id": "s1", "version": 2}]
    assert payload["aux"] == {"judge_verdict": True,
                              "judge_reason": "correct"}
    assert payload["raw_correctness"] is None


def test_execution_record_to_dict_carries_external_rewards():
    record = _execution(
        sheet_id="external:unit-1:0",
        kind="external",
        source="external",
        rewards=[1.0],
        metrics={"reward": 1.0},
        raw_correctness=True,
        artifacts={"source": "campaign", "sha256": "abc"},
    )
    payload = record.to_dict()
    assert payload["kind"] == "external"
    assert payload["source"] == "external"
    assert payload["rewards"] == [1.0]
    assert payload["artifacts"] == {"source": "campaign", "sha256": "abc"}


def test_coverage_and_grading_records_serialize_every_field():
    coverage = CoverageRecord(
        unit_id="unit-1",
        attempt_index=1,
        present=False,
        status="terminal",
        cause="leak",
        artifacts={"skill_id": "s1"},
    )
    assert coverage.to_dict() == {
        "unit_id": "unit-1",
        "attempt_index": 1,
        "present": False,
        "status": "terminal",
        "cause": "leak",
        "artifacts": {"skill_id": "s1"},
    }

    grading = GradingRecord(
        verdict="success",
        basis="scorer: the benchmark's own scorer",
        rule_name="scorer",
    )
    assert grading.to_dict() == {
        "verdict": "success",
        "basis": "scorer: the benchmark's own scorer",
        "rule_name": "scorer",
    }


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


def test_unit_summary_to_dict_is_its_whole_declared_state():
    summary = UnitSummary(
        unit_id="unit-1",
        phase_label="p03:live",
        task_ref="tb:task-1",
        role="weak",
        arm="with_bank",
        expected_attempts=2,
        injected_skills=[{"skill_id": "s1", "version": 1}],
        valid_count=1,
        success_count=1,
        status="normal",
        cause="",
        sheet_ids=["sheet-0"],
        artifacts={"sheet_ids": ["sheet-0", "gap:unit-1:1"]},
        aux_success_count=1,
    )
    payload = summary.to_dict()
    assert set(payload) == {field.name for field in fields(UnitSummary)}
    assert payload["valid_count"] == 1
    assert payload["success_count"] == 1
    assert payload["status"] == "normal"
    assert payload["cause"] == ""
    assert payload["sheet_ids"] == ["sheet-0"]
    assert payload["aux_success_count"] == 1
    assert payload["injected_skills"] == [{"skill_id": "s1", "version": 1}]
