"""Trial-level review: failed-trial selection, analysis and aggregation."""

import dataclasses

import pytest

import expedistill.benchmarks.tb  # noqa: F401
from expedistill.composites.evidence_package import EvidencePackage
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    PhaseReport,
    UnitSummary,
)
from expedistill.core.models import Step
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.review import (
    aggregation,
    trial,
    trial_evidence,
    trial_review,
)
from expedistill.methods.review.contracts import ReviewContext, TrialFinding
from tests.helpers.plugins import ensure_plugin
from tests.helpers.skills import make_skill

TASK = "tb:t1"
SKILL = make_skill("traj/tb:t1/weak/00/seg00", "read the error first",
                   summary="read the error")


@pytest.fixture(autouse=True)
def _tb():
    ensure_plugin("tb")


def _sheet(*, correct=False, attempt=0, folder=None):
    unit = make_unit_id(
        phase_label="p03:live", benchmark="tb", task_ref=TASK,
        role="weak", skills_injected=[{"skill_id": SKILL.skill_id,
                                       "version": 1}])
    artifacts = {"trajectory_id": f"traj/{TASK}/weak/{attempt:02d}"}
    if folder is not None:
        artifacts["skill_folder"] = folder
    execution = ExecutionRecord(
        sheet_id=f"{unit}::{attempt}",
        unit_id=unit,
        phase_label="p03:live",
        task_ref=TASK,
        role="weak",
        arm=None,
        model_role="weak",
        model_name="weak-model",
        sample_index=attempt,
        skills_injected=[{"skill_id": SKILL.skill_id, "version": 1}],
        steps=[Step(action="cat skill", observation=""),
               Step(action="run the wrong command", observation="error")],
        raw_correctness=correct,
        artifacts=artifacts,
    )
    return AnswerSheet(
        sheet_id=execution.sheet_id,
        phase_label="p03:live",
        coverage=CoverageRecord(
            unit_id=unit, attempt_index=attempt, present=True,
            status="present"),
        execution=execution,
        grading=GradingRecord(
            verdict="success" if correct else "failure",
            basis="tb_scorer", rule_name="tb_scorer"))


def _unit(sheet):
    return UnitSummary(
        unit_id=sheet.coverage.unit_id, phase_label="p03:live",
        task_ref=TASK, role="weak", arm=None, expected_attempts=1,
        injected_skills=[{"skill_id": SKILL.skill_id, "version": 1}],
        valid_count=1, success_count=0, status="normal", cause="")


def _context():
    return ReviewContext(
        phase_kind="live", phase_label="p04:learn",
        source_phase_label="p03:live", checks_left=1,
        round=0, delivery="folder_by_task", compare_rule="strict_rate_gain",
        n_attempts_weak=1, benchmark="tb", weak_profile="",
        task_select_rule="any_weak_failed",
        trajectory_select="all",
        segment_select="strong_success_weak_failed", workers=2)


def test_only_failed_trials_are_analyzed(monkeypatch):
    sheet = _sheet(correct=False)
    ok = _sheet(correct=True, attempt=1)
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[sheet, ok],
        units=[_unit(sheet), _unit(ok)])
    monkeypatch.setattr(
        trial.attribution, "task_instruction_for",
        lambda benchmark, task: "do the task")
    handle = make_fake_handle("author", [json_script({
        "reason": "the model read the skill and still chose the wrong command",
        "profile_note": "it ignores verification steps",
        "skills": [{"skill_id": SKILL.skill_id, "issue": "not applied",
                    "step_start": 0, "step_end": 2}],
        "new_skill_note": "",
    })])

    findings, failed, model_errors = trial.analyze_trials(
        current=report, bank=[SKILL], context=_context(), handle=handle)

    assert not failed and not model_errors
    assert [finding.sheet_id for finding in findings] == [sheet.sheet_id]
    assert findings[0].profile_note == "it ignores verification steps"
    assert findings[0].skill_ids == [SKILL.skill_id]
    assert SKILL.skill_id in findings[0].evidence


@pytest.mark.parametrize("alias", [
    "traj-tb-t1-weak-00-seg00-skill",
    "traj-tb-t1-weak-00-seg00-skill/SKILL.md",
    "traj-tb-t1-weak-00-seg00-skill-SKILL.md",
])
def test_trial_analysis_resolves_folder_aliases(monkeypatch, alias):
    folder = {"files": [{
        "skill_id": SKILL.skill_id,
        "filename": "traj-tb-t1-weak-00-seg00-skill/SKILL.md",
    }]}
    sheet = _sheet(correct=False, folder=folder)
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[sheet],
        units=[_unit(sheet)])
    monkeypatch.setattr(
        trial.attribution, "task_instruction_for",
        lambda benchmark, task: "do the task")
    handle = make_fake_handle("author", [json_script({
        "reason": "the model read the skill and still chose the wrong command",
        "profile_note": "",
        "skills": [{"skill_id": alias, "issue": "not applied",
                    "step_start": 0, "step_end": 2}],
        "new_skill_note": "",
    })])

    findings, failed, model_errors = trial.analyze_trials(
        current=report, bank=[SKILL], context=_context(), handle=handle)

    assert not failed and not model_errors
    assert findings[0].skill_ids == [SKILL.skill_id]
    assert SKILL.skill_id in findings[0].evidence


def test_trial_review_aggregates_actions_requirements_and_profile(monkeypatch):
    sheet = _sheet(correct=False)
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[sheet],
        units=[_unit(sheet)])
    monkeypatch.setattr(
        trial.attribution, "task_instruction_for",
        lambda benchmark, task: "do the task")
    handle = make_fake_handle("author", [
        json_script({
            "reason": "the skill was never opened",
            "profile_note": "it skips the available trigger",
            "skills": [{"skill_id": SKILL.skill_id, "issue": "not read",
                        "step_start": 0, "step_end": 2}],
            "new_skill_note": "needs a recovery procedure",
        }),
        json_script({"profile_text": ""}),
        json_script({
            "skill_actions": [{
                "skill_id": SKILL.skill_id, "action": "revise",
                "reason": "make the trigger observable",
                "source_sheet_ids": [sheet.sheet_id]}],
            "new_skill_requirements": [{
                "title": "recover a stuck service",
                "description": "teach the recovery procedure",
                "trigger": "When a command fails,",
                "source_sheet_ids": [sheet.sheet_id]}],
        }),
    ])

    result = trial_review.run_trial_review(
        current=report, bank=[SKILL], context=_context(),
        handles={"author": handle})

    assert len(result.trial_findings) == 1
    assert [item.row.action for item in result.items] == ["revise"]
    assert result.items[0].evidence is not None
    assert len(result.new_skill_requirements) == 1
    assert result.new_skill_requirements[0].source_sheet_ids == [
        sheet.sheet_id]
    assert result.profile_update == ""


def test_aggregation_defaults_unmentioned_measured_skills_to_keep():
    """Every measured skill gets a row, even when no finding mentions it."""
    other = make_skill("traj/tb:t1/weak/00/seg01", "second skill",
                       summary="second")
    units = [
        _unit_for("unit-a", SKILL),
        _unit_for("unit-b", other),
    ]
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[], units=units)
    finding = TrialFinding(
        sheet_id="sheet-1", unit_id="unit-a", task_ref=TASK,
        reason="the first skill was not applied",
        skill_ids=[SKILL.skill_id], step_start=0, step_end=1)
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [{
            "skill_id": SKILL.skill_id, "action": "keep",
            "reason": "the card itself is correct"}],
        "new_skill_requirements": [],
    })])

    items, requirements = aggregation.aggregate_findings(
        findings=[finding], bank=[SKILL, other], current=report,
        context=_context(), handle=handle)

    assert requirements == []
    assert [item.row.target_id for item in items] == [
        SKILL.skill_id, other.skill_id]
    assert [item.row.action for item in items] == ["keep", "keep"]
    assert items[1].row.reason.startswith("no trial finding implicated")
    assert items[1].row.basis_unit == "unit-b"


def _unit_for(unit_id, skill):
    return UnitSummary(
        unit_id=unit_id, phase_label="p03:live", task_ref=TASK,
        role="weak", arm=None, expected_attempts=1,
        injected_skills=[{"skill_id": skill.skill_id, "version": 1}],
        valid_count=1, success_count=0, status="normal", cause="")


def test_aggregation_accepts_prompt_compliant_keep_without_finding():
    """The prompt now tells the model to emit keep for every measured skill.

    A keep row with no supporting finding is valid; it must not hit the
    old "no finding supports" error.
    """
    other = make_skill("traj/tb:t1/weak/00/seg01", "second skill",
                       summary="second")
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[],
        units=[_unit_for("unit-a", SKILL), _unit_for("unit-b", other)])
    finding = TrialFinding(
        sheet_id="sheet-1", unit_id="unit-a", task_ref=TASK,
        reason="the first skill was not applied",
        skill_ids=[SKILL.skill_id], step_start=0, step_end=1)
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [
            {"skill_id": SKILL.skill_id, "action": "keep",
             "reason": "the card itself is correct"},
            {"skill_id": other.skill_id, "action": "keep",
             "reason": "no finding implicated this measured skill"},
        ],
        "new_skill_requirements": [],
    })])

    items, requirements = aggregation.aggregate_findings(
        findings=[finding], bank=[SKILL, other], current=report,
        context=_context(), handle=handle)

    assert requirements == []
    assert [item.row.action for item in items] == ["keep", "keep"]
    assert items[1].row.basis_unit == "unit-b"


def test_evidence_keeps_one_window_per_skill():
    sheet = _sheet(correct=False)
    evidence = trial_evidence.evidence_for_skills(
        sheet, skill_ids=[SKILL.skill_id, "other/skill"],
        issue="the trigger was missed", reason="wrong behavior",
        step_start=0, step_end=2, bank=[SKILL],
        ranges={SKILL.skill_id: (0, 1), "other/skill": (1, 2)})
    assert evidence[SKILL.skill_id].windows[0].step_start == 0
    assert evidence[SKILL.skill_id].windows[0].step_end == 1
    assert evidence["other/skill"].windows[0].step_start == 1
    assert evidence["other/skill"].windows[0].step_end == 2


def test_exam_trial_revision_builds_exam_evidence_package():
    exam_task = "traj/tb:t1/weak/00/seg00/skill/v01/exam"
    execution = ExecutionRecord(
        sheet_id="exam-sheet-1", unit_id="exam-unit",
        phase_label="p03:exam", task_ref=exam_task, role="weak", arm=None,
        model_role="weak", model_name="weak-model", sample_index=0,
        skills_injected=[{"skill_id": SKILL.skill_id, "version": 1}],
        steps=[Step(action="select skill", observation=""),
               Step(action="write plan", observation="")],
        answer="PLAN: restart the service",
        metrics={"scene_text": "The service is down."},
        aux={"judge_reason": "the plan misses verification"},
        kind="trajectory")
    sheet = AnswerSheet(
        sheet_id=execution.sheet_id, phase_label="p03:exam",
        coverage=CoverageRecord(
            unit_id="exam-unit", attempt_index=0, present=True,
            status="present"),
        execution=execution,
        grading=GradingRecord(
            verdict="failure", basis="exam_judge",
            rule_name="exam_judge"))
    report = PhaseReport(
        phase_kind="exam", learning_usable=True, sheets=[sheet],
        units=[_unit_for("exam-unit", SKILL)])
    finding = TrialFinding(
        sheet_id=sheet.sheet_id, unit_id="exam-unit", task_ref=exam_task,
        reason="the plan misses verification", skill_ids=[SKILL.skill_id],
        step_start=0, step_end=2)
    context = dataclasses.replace(
        _context(), phase_kind="exam", source_phase_label="p03:exam")
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [{
            "skill_id": SKILL.skill_id, "action": "revise",
            "reason": "make the verification step explicit"}],
        "new_skill_requirements": [],
    })])

    items, requirements = aggregation.aggregate_findings(
        findings=[finding], bank=[SKILL], current=report,
        context=context, handle=handle)

    assert requirements == []
    package = items[0].evidence
    assert isinstance(package, EvidencePackage)
    assert package.old_skill.skill_id == SKILL.skill_id
    assert package.fact("exam_question") == "The service is down."
    assert package.fact("weak_answer") == "PLAN: restart the service"
    assert package.fact("judge_reason") == "the plan misses verification"
