"""TraceRef: the exact location a skill version's evidence points at."""

import pytest

from expedistill.composites import trace
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
)
from expedistill.core.ids import make_trajectory_id
from expedistill.core.models import Step
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import object_filename, write_json

LABEL = "p01:baseline"
UNIT = make_unit_id(phase_label=LABEL, benchmark="tb", task_ref="tb:t1",
                    role="strong", skills_injected=None)


def _trajectory(attempt):
    return make_trajectory_id(LABEL, "tb:t1", "strong", attempt)


def _sheet(attempt, n_steps):
    trajectory = _trajectory(attempt)
    sheet_id = f"{UNIT}::{trajectory}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=UNIT, phase_label=LABEL,
        task_ref="tb:t1", role="strong", arm=None, model_role="strong",
        model_name="m", sample_index=attempt,
        steps=[Step(action=f"a{attempt}-{i}", observation=f"o{attempt}-{i}")
               for i in range(n_steps)],
        artifacts={"trajectory_id": trajectory}, raw_correctness=attempt == 0)
    return AnswerSheet(
        sheet_id=sheet_id, phase_label=LABEL,
        coverage=CoverageRecord(unit_id=UNIT, attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))


def _scenes(run_dir, refs, *, before_steps=trace.WRITER_BEFORE_STEPS,
            after_steps=30):
    """The usual writer window around every cut."""
    return trace.render_windows(
        run_dir,
        [(ref, ref.step - before_steps, ref.step + after_steps)
         for ref in refs],
        header="STEPS (the skill applies at step {cuts}):")


def _write(run_dir, sheets):
    dest = run_dir / "phases" / "p01_baseline" / "answer_sheets"
    for sheet in sheets:
        write_json(dest / object_filename(sheet.sheet_id), sheet.to_dict())


@pytest.fixture()
def run_dir(tmp_path):
    _write(tmp_path, [_sheet(0, 5), _sheet(1, 9), _sheet(2, 3)])
    return tmp_path


def test_trace_at_names_one_attempt_not_the_unit():
    first, last = _sheet(0, 5), _sheet(2, 3)
    ref0, ref2 = trace.trace_at(first, 2), trace.trace_at(last, 2)
    assert ref0.sheet_id != ref2.sheet_id
    assert (ref0.phase_label, ref0.step) == (LABEL, 2)


@pytest.mark.parametrize("step", [-1, 3, 99, True, "1"])
def test_trace_at_rejects_a_step_outside_the_execution(step):
    with pytest.raises(CodeBugError):
        trace.trace_at(_sheet(2, 3), step)


def test_render_windows_around_cuts_shows_a_step_once_when_cuts_overlap(run_dir):
    sheet = _sheet(1, 9)
    refs = [trace.trace_at(sheet, 2), trace.trace_at(sheet, 4)]

    text = _scenes(run_dir, refs, before_steps=2, after_steps=3)

    # Cuts 2 and 4 cover steps 0-4 and 2-6: one run 0-6, each step once.
    assert text.count("action: a1-3") == 1
    assert text.count("action: a1-0") == 1 and text.count("action: a1-6") == 1
    assert "a1-7" not in text
    assert "the skill applies at step 2, 4" in text
    assert "are not shown" not in text
    assert text.count("failed.") == 1


def test_render_windows_around_cuts_marks_the_steps_a_gap_skips(run_dir):
    sheet = _sheet(1, 9)
    refs = [trace.trace_at(sheet, 0), trace.trace_at(sheet, 8)]

    text = _scenes(run_dir, refs, before_steps=0, after_steps=2)

    assert "action: a1-0" in text and "action: a1-1" in text
    assert "action: a1-8" in text
    assert "action: a1-4" not in text
    assert "[steps 2-7 are not shown]" in text


def test_render_windows_around_cuts_keeps_separate_executions_apart(run_dir):
    refs = [trace.trace_at(_sheet(0, 5), 1), trace.trace_at(_sheet(1, 9), 1)]

    text = _scenes(run_dir, refs, before_steps=1, after_steps=1)

    assert "[execution 1]" in text and "[execution 2]" in text
    assert text.count("action: a0-1") == 1 and text.count("action: a1-1") == 1
    assert "succeeded." in text and "failed." in text


def test_render_windows_around_cuts_of_one_location_has_no_execution_label(run_dir):
    text = _scenes(
        run_dir, [trace.trace_at(_sheet(0, 5), 1)], after_steps=2)

    assert "[execution" not in text
    assert text.startswith("STEPS (the skill applies at step 1)")


def test_render_windows_shows_exactly_the_window(run_dir):
    ref = trace.trace_at(_sheet(1, 9), 5)

    text = trace.render_windows(run_dir, [(ref, 3, 6)])

    assert text.startswith("STEPS (the trial went wrong at step 5)")
    assert "action: a1-3" in text and "action: a1-5" in text
    assert "a1-2" not in text and "a1-6" not in text


def test_render_windows_clips_to_the_recorded_steps(run_dir):
    ref = trace.trace_at(_sheet(2, 3), 1)

    text = trace.render_windows(run_dir, [(ref, -4, 40)])

    assert "action: a2-0" in text and "action: a2-2" in text


def test_render_windows_numbers_executions_with_the_label(run_dir):
    windows = [(trace.trace_at(_sheet(0, 5), 1), 0, 2),
               (trace.trace_at(_sheet(1, 9), 1), 0, 2)]

    text = trace.render_windows(run_dir, windows, label="failed trial")

    assert "[failed trial 1]" in text and "[failed trial 2]" in text
    assert text.index("a0-0") < text.index("a1-0")


def test_render_windows_around_cuts_refuses_a_missing_sheet(run_dir):
    with pytest.raises(ModelOutputError):
        _scenes(run_dir, [TraceRef(LABEL, "nope", 0)])


def test_instruction_with_initial_is_idempotent():
    assert trace.instruction_with_initial({}, "do it") == "do it"
    assert trace.instruction_with_initial(
        {"initial_observation": "room"}, "do it") == "do it\n\nroom"
    assert trace.instruction_with_initial(
        {"initial_observation": "room"}, "do it\n\nroom") == "do it\n\nroom"


def test_check_ref_fails_on_a_missing_sheet_instead_of_guessing(run_dir):
    with pytest.raises(ModelOutputError):
        trace.check_ref(run_dir, TraceRef(LABEL, "no-such-sheet", 0))


def test_check_ref_fails_on_an_out_of_range_step(run_dir):
    ref = TraceRef(LABEL, _sheet(2, 3).sheet_id, 7)
    with pytest.raises(ModelOutputError):
        trace.check_ref(run_dir, ref)


def test_trace_ref_rejects_a_malformed_shape():
    for bad in (("", "s", 0), ("p1:b", "", 0), ("p1:b", "s", -1),
                ("p1:b", "s", True), ("p1:b", "s", 1.5)):
        with pytest.raises(ValueError):
            TraceRef(*bad)


def test_trace_ref_round_trips_and_rejects_missing_keys():
    ref = TraceRef(LABEL, "s", 4)
    assert TraceRef.from_dict(ref.to_dict()) == ref
    with pytest.raises(ValueError, match="missing"):
        TraceRef.from_dict({"phase_label": LABEL, "sheet_id": "s"})


def test_provenance_requires_a_known_cause_and_trace_ref_sources():
    ref = TraceRef(LABEL, "s", 0)
    Provenance("k", 1, "p02:learn", "add", [ref])
    for cause in ("guess", "merge"):
        with pytest.raises(ValueError, match="cause"):
            Provenance("k", 1, "p02:learn", cause, [ref])
    with pytest.raises(ValueError, match="TraceRef"):
        Provenance("k", 1, "p02:learn", "add", [ref.to_dict()])


def test_provenance_round_trips():
    ref = TraceRef(LABEL, "s", 2)
    row = Provenance("k", 2, "p04:learn", "revise_content", [ref], "why")
    assert Provenance.from_dict(row.to_dict()) == row


def test_trace_of_trajectory_picks_the_named_attempt(run_dir):
    ref = trace.trace_of_trajectory(run_dir, LABEL, _trajectory(1), 4)

    assert ref.sheet_id == _sheet(1, 9).sheet_id
    trace.check_ref(run_dir, ref)
    assert ref.step == 4


def test_trace_of_trajectory_rejects_an_unknown_trajectory(run_dir):
    with pytest.raises(ModelOutputError, match="no answer sheet"):
        trace.trace_of_trajectory(run_dir, LABEL, _trajectory(7), 0)


def test_trace_of_trajectory_rejects_an_out_of_range_step(run_dir):
    with pytest.raises(CodeBugError):
        trace.trace_of_trajectory(run_dir, LABEL, _trajectory(2), 3)
