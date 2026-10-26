"""Unit/task aggregation over trial-level answer sheets (layer 5, pure).

This is the ONLY place trial records become unit counts.  It is shared by
decision, audit and the final test; the acceptance module never calls it and
never writes its result as a report card.
"""

from typing import Any, Dict, Iterable, List, Optional, Sequence

from expedistill.core.answer_sheet import (
    AnswerSheet,
    UnitSpec,
    UnitSummary,
)
from expedistill.infra.reliability import CodeBugError
from expedistill.core import vocabulary

__all__ = [
    "summarize_metrics",
    "summarize_phase",
    "summarize_unit",
    "task_metrics",
]


_CAUSE_PRIORITY = (
    vocabulary.CAUSE_LEAK,
    vocabulary.CAUSE_INJECTION_OVERFLOW,
    vocabulary.CAUSE_EXAM_GENERATION_FAILED,
    vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED,
    vocabulary.CAUSE_ALL_HOLES,
    vocabulary.CAUSE_ZERO_EXECUTION,
)


def summarize_unit(sheets: Sequence[AnswerSheet],
                   spec: UnitSpec) -> UnitSummary:
    """Aggregate one unit's trial sheets into a UnitSummary.

    A present graded trial is success/failure; invalid trials are excluded
    from ``valid_count``.  A unit with no valid trial takes its cause from
    the coverage records: control-perfect is a skip (not a zero), accepted
    holes alone are ``all_holes``, a real missing dispatch is
    ``invalid_redispatch_exhausted``.
    """
    unit_sheets = [sheet for sheet in sheets
                   if sheet.coverage.unit_id == spec.unit_id]
    graded = [sheet for sheet in unit_sheets
              if sheet.grading is not None
              and sheet.grading.verdict in (vocabulary.RUN_SUCCESS,
                                            vocabulary.RUN_FAILURE)]
    valid_count = len(graded)
    success_count = sum(
        1 for sheet in graded
        if sheet.grading.verdict == vocabulary.RUN_SUCCESS)

    causes = {sheet.coverage.cause for sheet in unit_sheets
              if sheet.coverage.cause}
    if valid_count:
        status, cause = vocabulary.STATUS_NORMAL, ""
    elif vocabulary.CAUSE_WEAK_ALREADY_KNOWS in causes:
        status = vocabulary.STATUS_NOT_RUN_CONTROL_PERFECT
        cause = vocabulary.CAUSE_WEAK_ALREADY_KNOWS
    else:
        status = vocabulary.STATUS_NOT_TESTABLE
        cause = _unit_cause(
            causes, spec.expected_attempts, unit_sheets,
            no_valid_cause=spec.no_valid_cause)

    aux_values = [sheet.execution.aux_success for sheet in graded
                  if sheet.execution is not None]
    aux_success_count: Optional[int]
    if aux_values and all(value is not None for value in aux_values):
        aux_success_count = sum(1 for value in aux_values if value)
    else:
        aux_success_count = None

    return UnitSummary(
        unit_id=spec.unit_id,
        phase_label=spec.phase_label,
        task_ref=spec.task_ref,
        role=spec.role,
        arm=spec.arm,
        expected_attempts=spec.expected_attempts,
        injected_skills=spec.injected_skills,
        valid_count=valid_count,
        success_count=success_count,
        status=status,
        cause=cause,
        sheet_ids=[sheet.sheet_id for sheet in graded],
        artifacts={"sheet_ids": [sheet.sheet_id for sheet in unit_sheets]},
        aux_success_count=aux_success_count,
    )


def summarize_phase(sheets: Sequence[AnswerSheet],
                    specs: Sequence[UnitSpec]) -> List[UnitSummary]:
    """Summarize every unit spec; a sheet for an unknown unit is a bug."""
    known = {spec.unit_id for spec in specs}
    if len(known) != len(specs):
        raise CodeBugError(
            "UnitSpec list carries duplicate unit ids; one unit must have "
            "exactly one spec (include the arm in the identity if two arms "
            "are otherwise identical)")
    unknown = sorted({sheet.coverage.unit_id for sheet in sheets} - known)
    if unknown:
        raise CodeBugError(
            "answer sheets name units with no UnitSpec: "
            f"{unknown[:10]}{'...' if len(unknown) > 10 else ''}")
    return [summarize_unit(sheets, spec) for spec in specs]


def task_metrics(summaries: Iterable[UnitSummary],
                 n_attempts: int) -> Dict[str, Dict[str, int]]:
    """D3-shaped task metrics derived from unit summaries.

    One task's summaries are summed (exam versions share a task only in
    the exam path; the test path has one summary per task/arm).
    """
    metrics: Dict[str, Dict[str, int]] = {}
    aux_seen: Dict[str, bool] = {}
    for summary in summaries:
        per = metrics.setdefault(summary.task_ref, {
            "attempts": 0, "valid": 0, "success": 0, "infra": 0})
        # Exam retries can produce more judged sheets than the unit's
        # expected_attempts (expected is the planned depth, not a cap).
        attempts = max(summary.expected_attempts, summary.valid_count)
        per["attempts"] += attempts
        per["valid"] += summary.valid_count
        per["success"] += summary.success_count
        per["infra"] += max(attempts - summary.valid_count, 0)
        if summary.aux_success_count is not None:
            per["aux_success"] = per.get("aux_success", 0) \
                + summary.aux_success_count
            aux_seen[summary.task_ref] = \
                aux_seen.get(summary.task_ref, True)
        elif summary.valid_count == 0:
            # The old metrics block initialized aux_success=0 for every
            # task; a zero-valid unit must keep the column present so a
            # declared aux metric does not crash summarize().
            per.setdefault("aux_success", 0)
            aux_seen[summary.task_ref] = aux_seen.get(summary.task_ref, True)
        else:
            aux_seen[summary.task_ref] = False
    # Only keep aux_success when every contributing summary supplied it;
    # a partial column would make summarize() raise (or silently fake it).
    for task_id, complete in aux_seen.items():
        if not complete:
            metrics[task_id].pop("aux_success", None)
    return metrics


def summarize_metrics(metrics: Dict[str, Dict[str, int]], n_attempts: int,
                      aux_metric: Optional[str] = None) -> Dict[str, Any]:
    """The D3 summary over ``task_metrics`` (same definitions as before)."""
    from expedistill.blocks import metrics as metrics_block
    return metrics_block.summarize(
        metrics, n_attempts, aux_metric=aux_metric)


def _unit_cause(causes: set, expected_attempts: int,
                unit_sheets: Sequence[AnswerSheet],
                *, no_valid_cause: str) -> str:
    if not causes:
        # Every expected trial is present but none has a valid sample:
        # the role decides whether that is an all-hole baseline or an
        # unscored live/exam run.  A unit with no sheet at all is a
        # missing dispatch.
        if any(sheet.coverage.present for sheet in unit_sheets):
            return no_valid_cause
        return (vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED
                if expected_attempts else vocabulary.CAUSE_ALL_HOLES)
    for cause in _CAUSE_PRIORITY:
        if cause in causes:
            return cause
    if causes == {vocabulary.CAUSE_ACCEPTED_HOLE}:
        return vocabulary.CAUSE_ALL_HOLES
    if vocabulary.CAUSE_MISSING in causes:
        return vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED
    if vocabulary.CAUSE_ACCEPTED_HOLE in causes:
        # A mix of accepted holes and other gaps: if any real missing
        # dispatch exists it was handled above; otherwise the unit is a
        # settled zero-measurement.
        return vocabulary.CAUSE_ALL_HOLES
    # Unknown coverage cause: surface it loudly rather than inventing a
    # zero.  The fixed vocabulary is part of the protocol.
    raise CodeBugError(
        f"unknown coverage causes {sorted(causes)!r} on a unit with "
        f"expected_attempts={expected_attempts} and "
        f"{len(unit_sheets)} sheet(s)")
