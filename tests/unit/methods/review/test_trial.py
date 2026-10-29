"""Trial-level review: failed-trial selection, analysis and aggregation."""

import dataclasses
from types import SimpleNamespace

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
from expedistill.core.models import Exam, Step
from expedistill.core.trace import TraceRef
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


def test_aggregation_drops_requirement_with_no_finding_source():
    """A requirement citing a non-finding sheet is dropped, not fatal."""
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[],
        units=[_unit_for("unit-a", SKILL)])
    finding = TrialFinding(
        sheet_id="sheet-1", unit_id="unit-a", task_ref=TASK,
        reason="the skill was not applied", skill_ids=[SKILL.skill_id],
        step_start=0, step_end=1)
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [{
            "skill_id": SKILL.skill_id, "action": "keep",
            "reason": "the card is correct"}],
        "new_skill_requirements": [{
            "title": "missing capability",
            "description": "teach it",
            "trigger": "when needed",
            "source_sheet_ids": ["not-a-finding-sheet"]}],
    })])

    items, requirements = aggregation.aggregate_findings(
        findings=[finding], bank=[SKILL], current=report,
        context=_context(), handle=handle)

    assert [item.row.action for item in items] == ["keep"]
    assert requirements == []


OTHER = make_skill("traj/tb:t1/weak/00/seg01", "check the output file",
                   summary="check the output")


def _folder(*skills):
    return {
        "files": [{"skill_id": skill.skill_id,
                   "filename": skill.skill_id.replace("/", "-").replace(
                       ":", "-") + "/SKILL.md"}
                  for skill in skills],
        "catalog_text": "\n".join(
            f"{skill.skill_id} | {skill.summary}" for skill in skills),
    }


def _exam_sheet(skill, unit_id, *, opened=(), verdict="failure",
                scene="The service is down.", version=1,
                expected="restart the service and verify it answers",
                judge="the plan never verifies the service answers"):
    folder = _folder(SKILL, OTHER)
    cats = "\n".join(
        "cat " + entry["filename"] for entry in folder["files"]
        if entry["skill_id"] in {item.skill_id for item in opened})
    metrics = {"scene_text": scene}
    if expected is not None:
        metrics["expected_behavior"] = expected
    execution = ExecutionRecord(
        sheet_id=f"{unit_id}/att00", unit_id=unit_id,
        phase_label="p03:exam",
        task_ref=f"{skill.skill_id}/v{version:02d}/exam", role="weak",
        arm=None, model_role="weak", model_name="weak-model",
        sample_index=0,
        skills_injected=[{"skill_id": skill.skill_id, "version": version}],
        steps=[Step(action=cats or "# no skill opened", observation="..."),
               Step(action="restart the service", observation="")],
        answer="restart the service",
        metrics=metrics,
        aux={"judge_verdict": verdict, "judge_reason": judge},
        artifacts={"skill_folder": folder},
        kind="trajectory")
    return AnswerSheet(
        sheet_id=execution.sheet_id, phase_label="p03:exam",
        coverage=CoverageRecord(
            unit_id=unit_id, attempt_index=0, present=True,
            status="present"),
        execution=execution,
        grading=GradingRecord(verdict=verdict, basis="exam_judge",
                              rule_name="exam_judge"))


def _exam_unit(unit_id, skill, version=1):
    return UnitSummary(
        unit_id=unit_id, phase_label="p03:exam",
        task_ref=f"{skill.skill_id}/v{version:02d}/exam", role="weak",
        arm=None, expected_attempts=1,
        injected_skills=[{"skill_id": skill.skill_id, "version": version}],
        valid_count=1, success_count=0, status="normal", cause="")


def _exam_context():
    return dataclasses.replace(
        _context(), phase_kind="exam", source_phase_label="p03:exam")


def test_exam_trial_analysis_reads_the_judge_and_names_the_exam_skill():
    sheet = _exam_sheet(SKILL, "exam-a", opened=[OTHER])
    report = PhaseReport(phase_kind="exam", learning_usable=True,
                         sheets=[sheet], units=[_exam_unit("exam-a", SKILL)])
    handle = make_fake_handle("author", [json_script({
        "reason": "the opened card pointed at the output file instead",
        "exam_skill_issue": "not opened; its catalog line matches a down "
                            "service, so its trigger is too narrow",
        "profile_note": "",
        "skills": [{"skill_id": OTHER.skill_id,
                    "issue": "opened and misleading here"}],
        "new_skill_note": "",
    })])

    real_scene = TraceRef("p01:baseline", "real-sheet", 4)
    store = SimpleNamespace(load=lambda kind, object_id, cls=None: Exam(
        exam_id=object_id, skill_id=SKILL.skill_id, skill_version=1,
        scene_text="s", expected_behavior="e", reference_answer="r",
        scene=real_scene))

    findings, failed, model_errors = trial.analyze_trials(
        current=report, bank=[SKILL, OTHER], context=_exam_context(),
        handle=handle, store=store)

    assert not failed and not model_errors
    # Every window of a paper answer points at the exam's REAL scene.
    assert all(window.ref == real_scene
               for evidence in findings[0].evidence.values()
               for window in evidence.windows)
    prompt = handle.client.calls[0]["user"]
    assert "paper exam" in prompt
    assert "the plan never verifies the service answers" in prompt
    assert "restart the service and verify it answers" in prompt
    assert SKILL.content in prompt
    assert f"SKILL_ID: {OTHER.skill_id}\n{OTHER.content}" in prompt
    finding = findings[0]
    assert finding.skill_ids == [SKILL.skill_id, OTHER.skill_id]
    assert (finding.step_start, finding.step_end) == (0, 2)
    issues = finding.evidence[SKILL.skill_id].windows[0].trigger_context
    assert issues.startswith(f"{SKILL.skill_id}: not opened")
    assert "trigger is too narrow" in aggregation._findings_text(findings)


def test_live_trial_analysis_keeps_the_executed_trial_prompt(monkeypatch):
    sheet = _sheet(correct=False)
    report = PhaseReport(phase_kind="live", learning_usable=True,
                         sheets=[sheet], units=[_unit(sheet)])
    monkeypatch.setattr(
        trial.attribution, "task_instruction_for",
        lambda benchmark, task: "do the task")
    handle = make_fake_handle("author", [json_script({
        "reason": "wrong command", "profile_note": "", "skills": [],
        "new_skill_note": ""})])

    trial.analyze_trials(current=report, bank=[SKILL], context=_context(),
                         handle=handle)

    prompt = handle.client.calls[0]["user"]
    assert "TRIAL STEPS:" in prompt and "paper exam" not in prompt


def test_exam_trial_analysis_needs_the_expected_behavior():
    sheet = _exam_sheet(SKILL, "exam-a", expected=None)
    report = PhaseReport(phase_kind="exam", learning_usable=True,
                         sheets=[sheet], units=[_exam_unit("exam-a", SKILL)])

    with pytest.raises(trial.CodeBugError, match="expected_behavior"):
        trial.analyze_trials(
            current=report, bank=[SKILL, OTHER], context=_exam_context(),
            handle=make_fake_handle("author", []))


def test_exam_trial_analysis_requires_a_verdict_on_the_exam_skill():
    sheet = _exam_sheet(SKILL, "exam-a")
    report = PhaseReport(phase_kind="exam", learning_usable=True,
                         sheets=[sheet], units=[_exam_unit("exam-a", SKILL)])
    handle = make_fake_handle("author", [json_script({
        "reason": "unclear", "exam_skill_issue": " ", "profile_note": "",
        "skills": [], "new_skill_note": ""})])

    findings, failed, model_errors = trial.analyze_trials(
        current=report, bank=[SKILL, OTHER], context=_exam_context(),
        handle=handle)

    assert findings == [] and not failed
    assert model_errors == {sheet.sheet_id}


def _exam_finding(sheet, skills):
    return TrialFinding(
        sheet_id=sheet.sheet_id, unit_id=sheet.coverage.unit_id,
        task_ref=sheet.execution.task_ref, reason="the plan is wrong",
        skill_ids=[skill.skill_id for skill in skills],
        step_start=0, step_end=2)


def test_exam_actions_answer_only_to_the_skills_own_exam():
    sheet_a = _exam_sheet(SKILL, "exam-a", scene="Scene of skill A.")
    sheet_b = _exam_sheet(OTHER, "exam-b", scene="Scene of skill B.")
    report = PhaseReport(
        phase_kind="exam", learning_usable=True, sheets=[sheet_b, sheet_a],
        units=[_exam_unit("exam-b", OTHER), _exam_unit("exam-a", SKILL)])
    findings = [_exam_finding(sheet_b, [OTHER, SKILL]),
                _exam_finding(sheet_a, [SKILL])]
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [
            {"skill_id": SKILL.skill_id, "action": "revise",
             "reason": "widen its trigger"},
            {"skill_id": OTHER.skill_id, "action": "revise",
             "reason": "fix its steps"}],
        "new_skill_requirements": [],
    })])

    items, _ = aggregation.aggregate_findings(
        findings=findings, bank=[SKILL, OTHER], current=report,
        context=_exam_context(), handle=handle)

    by_skill = {item.row.target_id: item for item in items}
    assert by_skill[SKILL.skill_id].row.basis_unit == "exam-a"
    assert by_skill[SKILL.skill_id].evidence.fact(
        "exam_question") == "Scene of skill A."
    assert by_skill[OTHER.skill_id].row.basis_unit == "exam-b"
    assert by_skill[OTHER.skill_id].evidence.fact(
        "exam_question") == "Scene of skill B."


def test_exam_skill_that_passed_its_own_exam_is_kept():
    sheet_a = _exam_sheet(SKILL, "exam-a", verdict="success")
    sheet_b = _exam_sheet(OTHER, "exam-b")
    report = PhaseReport(
        phase_kind="exam", learning_usable=True, sheets=[sheet_a, sheet_b],
        units=[_exam_unit("exam-a", SKILL), _exam_unit("exam-b", OTHER)])
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [
            {"skill_id": SKILL.skill_id, "action": "revise",
             "reason": "it was not opened in another scene"},
            {"skill_id": OTHER.skill_id, "action": "keep",
             "reason": "model limitation"}],
        "new_skill_requirements": [],
    })])

    items, _ = aggregation.aggregate_findings(
        findings=[_exam_finding(sheet_b, [OTHER, SKILL])],
        bank=[SKILL, OTHER], current=report, context=_exam_context(),
        handle=handle)

    row = {item.row.target_id: item.row for item in items}[SKILL.skill_id]
    assert row.action == "keep"
    assert row.basis_unit == "exam-a"
    assert "own exam" in row.reason


def test_exam_review_ignores_skills_not_examined_this_phase():
    unexamined = make_skill("traj/tb:t1/weak/00/seg02", "unrelated card",
                            summary="unrelated")
    sheet_a = _exam_sheet(SKILL, "exam-a")
    report = PhaseReport(
        phase_kind="exam", learning_usable=True, sheets=[sheet_a],
        units=[_exam_unit("exam-a", SKILL)])
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [
            {"skill_id": SKILL.skill_id, "action": "keep", "reason": "ok"},
            {"skill_id": unexamined.skill_id, "action": "keep",
             "reason": "mentioned elsewhere"}],
        "new_skill_requirements": [],
    })])

    items, _ = aggregation.aggregate_findings(
        findings=[_exam_finding(sheet_a, [SKILL, unexamined])],
        bank=[SKILL, OTHER, unexamined], current=report,
        context=_exam_context(), handle=handle)

    assert [item.row.target_id for item in items] == [SKILL.skill_id]


def test_exam_basis_is_the_highest_examined_version():
    report = PhaseReport(
        phase_kind="exam", learning_usable=True, sheets=[],
        units=[_exam_unit("exam-a-v2", SKILL, version=2),
               _exam_unit("exam-a-v1", SKILL, version=1)])
    assert aggregation._exam_units(report) == {SKILL.skill_id: "exam-a-v2"}


def test_exam_review_skips_a_failed_bare_control_answer():
    bare = dataclasses.replace(
        _exam_sheet(SKILL, "exam-bare"), sheet_id="bare-sheet")
    bare = dataclasses.replace(
        bare, execution=dataclasses.replace(
            bare.execution, sheet_id="bare-sheet", skills_injected=None))
    report = PhaseReport(phase_kind="exam", learning_usable=True,
                         sheets=[bare], units=[])

    findings, failed, model_errors = trial.analyze_trials(
        current=report, bank=[SKILL, OTHER], context=_exam_context(),
        handle=make_fake_handle("author", []))

    assert findings == [] and not failed and not model_errors


def test_last_check_turns_revise_into_delete():
    """With no later check a rewrite could not be tested: delete, no evidence."""
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[],
        units=[_unit_for("unit-a", SKILL)])
    finding = TrialFinding(
        sheet_id="sheet-1", unit_id="unit-a", task_ref=TASK,
        reason="the skill misses the recovery step",
        skill_ids=[SKILL.skill_id], step_start=0, step_end=1)
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [{
            "skill_id": SKILL.skill_id, "action": "revise",
            "reason": "add the recovery step"}],
        "new_skill_requirements": [],
    })])
    context = dataclasses.replace(_context(), checks_left=0)

    items, requirements = aggregation.aggregate_findings(
        findings=[finding], bank=[SKILL], current=report,
        context=context, handle=handle)

    assert requirements == []
    assert [item.row.action for item in items] == ["delete"]
    assert items[0].row.reason == "retry_exhausted"
    assert items[0].evidence is None


def test_last_check_leaves_keep_rows_unchanged():
    """Only revise changes on the last check; keep rows pass through."""
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[],
        units=[_unit_for("unit-a", SKILL)])
    finding = TrialFinding(
        sheet_id="sheet-1", unit_id="unit-a", task_ref=TASK,
        reason="the model did not run the command",
        skill_ids=[SKILL.skill_id], step_start=0, step_end=1)
    handle = make_fake_handle("author", [json_script({
        "skill_actions": [{
            "skill_id": SKILL.skill_id, "action": "keep",
            "reason": "the card is correct"}],
        "new_skill_requirements": [],
    })])
    context = dataclasses.replace(_context(), checks_left=0)

    items, _ = aggregation.aggregate_findings(
        findings=[finding], bank=[SKILL], current=report,
        context=context, handle=handle)

    assert [item.row.action for item in items] == ["keep"]
    assert items[0].row.reason == "the card is correct"
