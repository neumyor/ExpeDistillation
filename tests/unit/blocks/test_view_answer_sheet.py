"""``render_answer_sheet`` (D5) unit tests: the Markdown answer-sheet view.

The JSON record stays canonical; this renderer is the human-readable
derived artifact.  Each execution kind gets its own section (trajectory
steps, exam answer, external reward table), and a missing/terminal trial
renders the coverage section alone.
"""

from expedistill.benchmarks.base import ViewRules
from expedistill.blocks.view import render_answer_sheet
from expedistill.composites.answer_sheets import (
    execution_from_attempt,
    execution_from_external,
    execution_from_trajectory,
)
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    GradingRecord,
)
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Step, Trajectory

TASK = "tb:task-1"
EXAM = make_exam_id(
    make_skill_id(make_subtask_id(make_trajectory_id(TASK, "weak", 0), 0)), 1)


def _sheet(execution, *, unit_id="unit-1", attempt=0, grading=None):
    return AnswerSheet(
        sheet_id=execution.sheet_id,
        phase_label=execution.phase_label,
        coverage=CoverageRecord(
            unit_id=unit_id, attempt_index=attempt, present=True,
            status="present"),
        execution=execution,
        grading=grading,
    )


def test_trajectory_sheet_renders_headers_and_the_numbered_steps():
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(TASK, "weak", 0),
        task_id=TASK,
        benchmark="tb",
        model_role="weak",
        model_name="model-a",
        attempt_index=0,
        skills_injected=[{"skill_id": "s1", "version": 2}],
        steps=[Step(action="ls", observation="a.txt"),
               Step(action="cat a.txt", observation="hello")],
        correctness=True,
    )
    execution = execution_from_trajectory(
        trajectory, unit_id="unit-1", phase_label="p03:live",
        arm="with_bank")
    grading = GradingRecord(
        verdict="success", basis="scorer: the benchmark's own scorer",
        rule_name="scorer")

    text = render_answer_sheet(_sheet(execution, grading=grading))

    assert text.startswith(
        f"# Answer sheet `unit-1::{trajectory.trajectory_id}`")
    assert "- phase: `p03:live`" in text
    assert "- unit: `unit-1`" in text
    assert "- attempt: `0`" in text
    assert "- coverage: `present`" in text.splitlines()
    assert f"- task: `{TASK}`" in text
    assert "- arm: `with_bank`" in text
    assert "- role/model: `weak` / `model-a`" in text
    assert "- skills: `[{'skill_id': 's1', 'version': 2}]`" in text
    assert "- raw correctness: `True`" in text
    assert "- verdict: `success`" in text
    assert "- basis: scorer: the benchmark's own scorer" in text
    assert "- rule: `scorer`" in text
    assert "## Trajectory" in text
    assert "step 0\naction: ls\nobservation: a.txt" in text
    assert "step 1\naction: cat a.txt\nobservation: hello" in text


def test_trajectory_sheet_honours_the_view_rules():
    rules = ViewRules(
        max_obs_chars=20, head_keep=5, tail_keep=5,
        truncation_marker="[... {omitted} characters omitted ...]")
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(TASK, "weak", 0),
        task_id=TASK,
        benchmark="tb",
        model_role="weak",
        model_name="model-a",
        attempt_index=0,
        steps=[Step(action="ls", observation="x" * 100)],
        correctness=False,
    )
    execution = execution_from_trajectory(
        trajectory, unit_id="unit-1", phase_label="p03:live")

    text = render_answer_sheet(_sheet(execution), rules)

    assert "... 90 characters omitted ..." in text
    assert "x" * 50 not in text


def test_exam_sheet_renders_the_answer_and_the_judge_grading():
    attempt = Attempt(
        attempt_id=make_attempt_id(EXAM, 0),
        exam_id=EXAM,
        skill_id="skill-1",
        skill_version=2,
        purpose="acceptance",
        weak_answer="the answer is 42",
        steps=[Step(action="cat foo/SKILL.md", observation="rule"),
               Step(action="the answer is 42", observation="")],
        judge_verdict=True,
        judge_reason="correct",
        retry_round=1,
    )
    execution = execution_from_attempt(
        attempt, unit_id="unit-2", phase_label="p03:exam")
    grading = GradingRecord(
        verdict="success", basis="exam_judge: correct",
        rule_name="exam_judge")

    text = render_answer_sheet(_sheet(
        execution, unit_id="unit-2", attempt=1, grading=grading))

    assert "## Trajectory" in text
    assert "the answer is 42" in text
    assert "- verdict: `success`" in text
    assert "- basis: exam_judge: correct" in text


def test_external_sheet_renders_the_reward_table_and_provenance():
    execution = execution_from_external(
        task_ref=TASK, unit_id="unit-3", phase_label="p06:test",
        reward=1.0, sample_index=0, source="campaign.csv",
        sha256="deadbeef")
    grading = GradingRecord(
        verdict="success", basis="external_reward: campaign reward",
        rule_name="external_reward")

    text = render_answer_sheet(_sheet(
        execution, unit_id="unit-3", grading=grading))

    assert "## External campaign reward" in text
    assert "| attempt | reward |" in text
    assert "|---:|---:|" in text
    assert "| 0 | 1.0 |" in text
    assert "- source: `campaign.csv`" in text
    assert "- sha256: `deadbeef`" in text


def test_a_missing_or_terminal_sheet_renders_coverage_alone():
    coverage = CoverageRecord(
        unit_id="unit-4", attempt_index=0, present=False,
        status="terminal", cause="leak")
    sheet = AnswerSheet(
        sheet_id="gap:unit-4:0",
        phase_label="p03:live",
        coverage=coverage,
    )

    text = render_answer_sheet(sheet)

    assert text.startswith("# Answer sheet `gap:unit-4:0`")
    assert "- coverage: `terminal` / `leak`" in text
    assert ("_No execution record: this trial was missing or terminal "
            "before an answer existed._") in text
    assert "## " not in text
    assert "- task:" not in text
    assert "- verdict:" not in text
