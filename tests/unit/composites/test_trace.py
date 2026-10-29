"""TraceRef: the exact location a skill version and an exam are cut at."""

import pytest

from expedistill.composites import answer_sheets, phase_report, trace
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
)
from expedistill.core.models import Exam, Step
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError

UNIT = make_unit_id(phase_label="p01:baseline", benchmark="tb",
                    task_ref="tb:t1", role="strong", skills_injected=None)


def _sheet(attempt, n_steps, phase="p01:baseline"):
    trajectory = f"traj/tb:t1/strong/{attempt:02d}"
    sheet_id = f"{UNIT}::{trajectory}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=UNIT, phase_label=phase,
        task_ref="tb:t1", role="strong", arm=None, model_role="strong",
        model_name="m", sample_index=attempt,
        steps=[Step(action=f"a{attempt}-{i}", observation=f"o{attempt}-{i}")
               for i in range(n_steps)],
        artifacts={"trajectory_id": trajectory}, raw_correctness=attempt == 0)
    return AnswerSheet(
        sheet_id=sheet_id, phase_label=phase,
        coverage=CoverageRecord(unit_id=UNIT, attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))


def _exam_answer_sheet():
    exam_id = "traj/tb:t1/strong/00/seg00/skill/v01/exam"
    sheet_id = exam_id + "/att00"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id="p03:exam:tb:" + exam_id + ":weak:abcd1234",
        phase_label="p03:exam", task_ref=exam_id, role="weak", arm=None,
        model_role="weak", model_name="m", sample_index=0,
        steps=[Step(action="select", observation=""),
               Step(action="plan", observation="")], answer="plan")
    return AnswerSheet(
        sheet_id=sheet_id, phase_label="p03:exam",
        coverage=CoverageRecord(unit_id=execution.unit_id, attempt_index=0,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))


@pytest.fixture()
def run_dir(tmp_path):
    sheets = [_sheet(0, 5), _sheet(1, 9), _sheet(2, 3)]
    dest = tmp_path / "phases" / "p01_baseline"
    answer_sheets.write_answer_sheets(
        sheets, dest / "answer_sheets", render_markdown=False)
    answer_sheets.write_coverage(
        [sheet.coverage for sheet in sheets], dest / "coverage.jsonl")
    phase_report.write_unit_specs(
        [UnitSpec(unit_id=UNIT, phase_label="p01:baseline", task_ref="tb:t1",
                  role="strong", arm=None, expected_attempts=3,
                  injected_skills=None)], dest)
    exam_dest = tmp_path / "phases" / "p03_exam"
    answer_sheets.write_answer_sheets(
        [_exam_answer_sheet()], exam_dest / "answer_sheets",
        render_markdown=False)
    return tmp_path


def test_trace_at_names_one_attempt_not_the_unit():
    first, last = _sheet(0, 5), _sheet(2, 3)
    ref0, ref2 = trace.trace_at(first, 2), trace.trace_at(last, 2)
    assert ref0.sheet_id != ref2.sheet_id
    assert (ref0.phase_label, ref0.step) == ("p01:baseline", 2)


@pytest.mark.parametrize("step", [-1, 3, 99, True, "1"])
def test_trace_at_rejects_a_step_outside_the_execution(step):
    with pytest.raises(CodeBugError):
        trace.trace_at(_sheet(2, 3), step)


def test_trace_at_refuses_an_exam_answer():
    with pytest.raises(CodeBugError, match="exam answer"):
        trace.trace_at(_exam_answer_sheet(), 0)


def test_resolve_returns_the_text_around_the_exact_attempt(run_dir):
    sheet = _sheet(1, 9)
    ref = trace.trace_at(sheet, 4)

    context = trace.resolve(run_dir, ref, after_steps=2)

    assert context.ref == ref
    assert "a1-3" in context.before and "a1-4" not in context.before
    assert "a1-4" in context.after and "a1-5" in context.after
    assert "a1-6" not in context.after
    assert "a0-" not in context.before + context.after
    assert "a2-" not in context.before + context.after
    assert context.outcome is False
    assert context.task_ref == "tb:t1"


def test_resolve_uses_the_injected_instruction_lookup(run_dir):
    ref = trace.trace_at(_sheet(0, 5), 1)

    context = trace.resolve(
        run_dir, ref, instruction_for=lambda task: f"do {task}")

    assert context.task_instruction == "do tb:t1"


def test_resolve_survives_a_failing_instruction_lookup(run_dir):
    ref = trace.trace_at(_sheet(0, 5), 1)

    def broken(task):
        raise RuntimeError("no plugin")

    assert trace.resolve(
        run_dir, ref, instruction_for=broken).task_instruction == ""


def test_resolve_fails_on_a_missing_sheet_instead_of_guessing(run_dir):
    ref = TraceRef("p01:baseline", "no-such-sheet", 0)
    with pytest.raises(ModelOutputError):
        trace.resolve(run_dir, ref)


def test_resolve_fails_on_an_out_of_range_step(run_dir):
    ref = TraceRef("p01:baseline", _sheet(2, 3).sheet_id, 7)
    with pytest.raises(ModelOutputError):
        trace.resolve(run_dir, ref)


def test_resolve_refuses_a_hand_built_ref_to_an_exam_answer(run_dir):
    ref = TraceRef("p03:exam", _exam_answer_sheet().sheet_id, 0)
    with pytest.raises(CodeBugError, match="exam answer"):
        trace.resolve(run_dir, ref)


def test_scene_of_returns_the_exams_own_scene():
    scene = TraceRef("p01:baseline", "s", 3)
    exam = Exam(exam_id="e", skill_id="s", skill_version=1, scene_text="x",
                expected_behavior="y", reference_answer="z", scene=scene)
    assert trace.scene_of(exam) is scene


def test_trace_ref_rejects_a_malformed_shape():
    for bad in (("", "s", 0), ("p1:b", "", 0), ("p1:b", "s", -1),
                ("p1:b", "s", True), ("p1:b", "s", 1.5)):
        with pytest.raises(ValueError):
            TraceRef(*bad)


def test_trace_ref_round_trips_and_rejects_missing_keys():
    ref = TraceRef("p01:baseline", "s", 4)
    assert TraceRef.from_dict(ref.to_dict()) == ref
    with pytest.raises(ValueError, match="missing"):
        TraceRef.from_dict({"phase_label": "p01:baseline", "sheet_id": "s"})


def test_provenance_requires_a_scene_except_for_a_merge():
    ref = TraceRef("p01:baseline", "s", 0)
    Provenance("k", 1, "p02:learn", "add", ref)
    Provenance("m", 1, "p06:learn", "merge", None)
    with pytest.raises(ValueError, match="needs a scene"):
        Provenance("k", 1, "p02:learn", "add", None)
    with pytest.raises(ValueError, match="cause"):
        Provenance("k", 1, "p02:learn", "guess", ref)


def test_provenance_round_trips():
    ref = TraceRef("p01:baseline", "s", 2)
    row = Provenance("k", 2, "p04:learn", "revise", ref, [ref], "why")
    assert Provenance.from_dict(row.to_dict()) == row
    merge = Provenance("m", 1, "p06:learn", "merge", None, [ref])
    assert Provenance.from_dict(merge.to_dict()) == merge


def test_trace_of_trajectory_picks_the_named_attempt(run_dir):
    ref = trace.trace_of_trajectory(
        run_dir, "p01:baseline", "traj/tb:t1/strong/01", 4)

    assert ref.sheet_id == _sheet(1, 9).sheet_id
    assert trace.resolve(run_dir, ref).before.count("a1-") == 4


def test_trace_of_trajectory_rejects_an_unknown_trajectory(run_dir):
    with pytest.raises(ModelOutputError, match="no answer sheet"):
        trace.trace_of_trajectory(
            run_dir, "p01:baseline", "traj/tb:t1/strong/07", 0)


def test_trace_of_trajectory_rejects_an_out_of_range_step(run_dir):
    with pytest.raises(CodeBugError):
        trace.trace_of_trajectory(
            run_dir, "p01:baseline", "traj/tb:t1/strong/02", 3)
