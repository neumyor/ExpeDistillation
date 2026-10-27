"""Trial-level review: failed-trial selection, analysis and aggregation."""

import pytest

import expedistill.benchmarks.tb  # noqa: F401
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
from expedistill.methods.review import trial, trial_review
from expedistill.methods.review.contracts import ReviewContext
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
