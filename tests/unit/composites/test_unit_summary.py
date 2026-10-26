"""Unit/task aggregation tests (composites/unit_summary.py).

This is the one place trial-level answer sheets become unit counts.  The
tests pin the status/cause mapping (normal counts, accepted holes,
missing dispatches, control-perfect skips, leak priority), the auxiliary
success count, the unknown-unit guard, and the D3-shaped task metrics.
"""

import pytest

from expedistill.composites import unit_summary
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
    UnitSummary,
)
from expedistill.infra.reliability import CodeBugError


def _sheet(unit_id, attempt=0, *, verdict=None, cause="", status="present",
           present=True, aux_success=None, task_ref="tb:t1",
           with_execution=True):
    execution = None
    if with_execution:
        execution = ExecutionRecord(
            sheet_id=f"sheet:{unit_id}:{attempt}",
            unit_id=unit_id,
            phase_label="p03:live",
            task_ref=task_ref,
            role="weak",
            arm=None,
            model_role="weak",
            model_name="model-a",
            sample_index=attempt,
            aux_success=aux_success,
        )
    grading = None
    if verdict is not None:
        grading = GradingRecord(verdict=verdict, basis="", rule_name="scorer")
    return AnswerSheet(
        sheet_id=f"sheet:{unit_id}:{attempt}",
        phase_label="p03:live",
        coverage=CoverageRecord(
            unit_id=unit_id, attempt_index=attempt, present=present,
            status=status, cause=cause),
        execution=execution,
        grading=grading,
    )


def _gap(unit_id, attempt, cause):
    status = ("missing" if cause in ("missing", "accepted_hole")
              else "terminal")
    return _sheet(unit_id, attempt, cause=cause, status=status,
                  present=False, with_execution=False)


def _spec(unit_id="unit-1", *, task_ref="tb:t1", expected_attempts=2,
          phase_label="p03:live", role="weak", arm=None,
          injected_skills=None):
    return UnitSpec(
        unit_id=unit_id,
        phase_label=phase_label,
        task_ref=task_ref,
        role=role,
        arm=arm,
        expected_attempts=expected_attempts,
        injected_skills=injected_skills,
    )


# ---------------------------------------------------------------------------
# summarize_unit
# ---------------------------------------------------------------------------

def test_normal_unit_counts_valid_and_success_and_excludes_invalid():
    sheets = [
        _sheet("unit-1", 0, verdict="success", aux_success=True),
        _sheet("unit-1", 1, verdict="failure", aux_success=False),
        _sheet("unit-1", 2, verdict="invalid"),
        _sheet("unit-1", 3),  # present but never graded
    ]

    summary = unit_summary.summarize_unit(sheets, _spec(expected_attempts=4))

    assert summary.valid_count == 2
    assert summary.success_count == 1
    assert summary.status == "normal"
    assert summary.cause == ""
    assert summary.sheet_ids == ["sheet:unit-1:0", "sheet:unit-1:1"]
    assert summary.artifacts == {"sheet_ids": [
        "sheet:unit-1:0", "sheet:unit-1:1",
        "sheet:unit-1:2", "sheet:unit-1:3"]}
    assert summary.aux_success_count == 1
    assert summary.expected_attempts == 4
    assert summary.unit_id == "unit-1"


def test_all_accepted_holes_map_to_all_holes():
    sheets = [_gap("unit-1", 0, "accepted_hole"),
              _gap("unit-1", 1, "accepted_hole")]

    summary = unit_summary.summarize_unit(sheets, _spec())

    assert summary.valid_count == 0
    assert summary.success_count == 0
    assert summary.status == "not_testable"
    assert summary.cause == "all_holes"
    assert summary.sheet_ids == []


def test_a_real_missing_dispatch_maps_to_invalid_redispatch_exhausted():
    sheets = [_gap("unit-1", 0, "missing")]

    summary = unit_summary.summarize_unit(sheets, _spec())

    assert summary.status == "not_testable"
    assert summary.cause == "invalid_redispatch_exhausted"


def test_a_unit_with_no_sheets_at_all_is_not_a_zero_score():
    summary = unit_summary.summarize_unit([], _spec())

    assert summary.valid_count == 0
    assert summary.status == "not_testable"
    assert summary.cause == "invalid_redispatch_exhausted"


def test_a_control_perfect_skip_is_not_run_control_perfect():
    sheets = [_gap("unit-1", 0, "weak_already_knows")]

    summary = unit_summary.summarize_unit(sheets, _spec(expected_attempts=1))

    assert summary.valid_count == 0
    assert summary.status == "not_run_control_perfect"
    assert summary.cause == "weak_already_knows"


@pytest.mark.parametrize("causes, expected", [
    ({"leak", "zero_execution"}, "leak"),
    ({"injection_overflow", "exam_generation_failed"},
     "injection_overflow"),
    ({"exam_generation_failed", "invalid_redispatch_exhausted"},
     "exam_generation_failed"),
    ({"invalid_redispatch_exhausted", "all_holes"},
     "invalid_redispatch_exhausted"),
    ({"all_holes", "zero_execution"}, "all_holes"),
    ({"accepted_hole", "zero_execution"}, "zero_execution"),
])
def test_the_terminal_cause_priority_is_fixed(causes, expected):
    sheets = [_gap("unit-1", index, cause)
              for index, cause in enumerate(sorted(causes))]

    summary = unit_summary.summarize_unit(sheets, _spec())

    assert summary.status == "not_testable"
    assert summary.cause == expected


def test_aux_success_count_is_none_unless_every_graded_trial_reports_it():
    mixed = [
        _sheet("unit-1", 0, verdict="success", aux_success=True),
        _sheet("unit-1", 1, verdict="failure", aux_success=None),
    ]
    assert unit_summary.summarize_unit(
        mixed, _spec()).aux_success_count is None

    missing = [_gap("unit-1", 0, "accepted_hole")]
    assert unit_summary.summarize_unit(
        missing, _spec()).aux_success_count is None


def test_aux_success_count_counts_true_values_only():
    sheets = [
        _sheet("unit-1", 0, verdict="success", aux_success=True),
        _sheet("unit-1", 1, verdict="success", aux_success=False),
        _sheet("unit-1", 2, verdict="failure", aux_success=True),
    ]
    summary = unit_summary.summarize_unit(
        sheets, _spec(expected_attempts=3))
    assert summary.valid_count == 3
    assert summary.success_count == 2
    assert summary.aux_success_count == 2


# ---------------------------------------------------------------------------
# summarize_phase
# ---------------------------------------------------------------------------

def test_summarize_phase_returns_one_summary_per_spec_in_spec_order():
    specs = [_spec("unit-b", task_ref="tb:t2"),
             _spec("unit-a", task_ref="tb:t1")]
    sheets = [_sheet("unit-a", 0, verdict="success")]

    summaries = unit_summary.summarize_phase(sheets, specs)

    assert [summary.unit_id for summary in summaries] == \
        ["unit-b", "unit-a"]
    assert summaries[1].success_count == 1


def test_summarize_phase_rejects_a_sheet_for_an_unknown_unit():
    sheets = [_sheet("unit-ghost", 0, verdict="success")]

    with pytest.raises(CodeBugError, match="no UnitSpec"):
        unit_summary.summarize_phase(sheets, [_spec("unit-1")])


# ---------------------------------------------------------------------------
# task_metrics (the D3 shape)
# ---------------------------------------------------------------------------

def _summary(unit_id, *, task_ref="tb:t1", expected_attempts=2,
             valid_count=0, success_count=0, status="normal", cause="",
             aux_success_count=None):
    return UnitSummary(
        unit_id=unit_id,
        phase_label="p03:live",
        task_ref=task_ref,
        role="weak",
        arm=None,
        expected_attempts=expected_attempts,
        injected_skills=None,
        valid_count=valid_count,
        success_count=success_count,
        status=status,
        cause=cause,
        aux_success_count=aux_success_count,
    )


def test_task_metrics_groups_by_task_and_counts_every_column():
    summaries = [
        _summary("unit-1", expected_attempts=2, valid_count=1,
                 success_count=1, aux_success_count=1),
        _summary("unit-2", expected_attempts=2, valid_count=2,
                 success_count=0, aux_success_count=0),
        _summary("unit-3", task_ref="tb:t2", expected_attempts=1,
                 valid_count=1, success_count=1, status="normal"),
    ]

    metrics = unit_summary.task_metrics(summaries, n_attempts=2)

    assert metrics == {
        "tb:t1": {"attempts": 4, "valid": 3, "success": 1, "infra": 1,
                  "aux_success": 1},
        "tb:t2": {"attempts": 1, "valid": 1, "success": 1, "infra": 0},
    }


def test_task_metrics_counts_a_not_testable_unit_as_infra():
    summaries = [
        _summary("unit-1", expected_attempts=2, status="not_testable",
                 cause="all_holes"),
        _summary("unit-2", expected_attempts=1, valid_count=1,
                 success_count=1),
    ]

    metrics = unit_summary.task_metrics(summaries, n_attempts=2)

    assert metrics["tb:t1"] == {
        "attempts": 3, "valid": 1, "success": 1, "infra": 2}


def test_task_metrics_drops_a_partial_aux_column():
    """A task's aux_success column is kept only when every contributing
    unit supplied it; a partial column would fake a measured rate."""
    summaries = [
        _summary("unit-1", expected_attempts=1, valid_count=1,
                 success_count=1, aux_success_count=1),
        _summary("unit-2", expected_attempts=1, valid_count=1,
                 success_count=1, aux_success_count=None),
    ]

    metrics = unit_summary.task_metrics(summaries, n_attempts=2)

    assert "aux_success" not in metrics["tb:t1"]
