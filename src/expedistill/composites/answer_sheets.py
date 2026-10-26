"""Trial-level answer-sheet builders and derived export (layer 5).

Execution owns the canonical ``Trajectory`` / ``Attempt`` records.  This
module only turns them into ``ExecutionRecord`` views, joins them with
coverage records, grades each present trial, and optionally writes the
derived JSON + Markdown answer sheets under a phase directory.

It deliberately knows nothing about expected units, missing trials, leaks,
injection overflow or unit aggregation: those belong to ``coverage`` and
``unit_summary``.
"""

from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from expedistill.composites import verdicts
from expedistill.composites.unit_ids import make_gap_sheet_id
from expedistill.core import ids
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
)
from expedistill.core.models import Attempt, Trajectory
from expedistill.core.vocabulary import COVERAGE_PRESENT
from expedistill.infra.store import object_filename, write_json, write_jsonl

__all__ = [
    "assemble_answer_sheets",
    "execution_from_attempt",
    "execution_from_external",
    "execution_from_trajectory",
    "grade_answer_sheets",
    "write_answer_sheets",
    "write_coverage",
]


def execution_from_trajectory(
        trajectory: Trajectory, *, unit_id: str, phase_label: str,
        arm: Optional[str] = None,
        aux_success: Optional[bool] = None,
        folder: Optional[Mapping[str, object]] = None) -> ExecutionRecord:
    """The trial-level view of one stored trajectory.

    ``folder`` is the delivery manifest that made this trajectory's skill
    files visible; carrying it on the execution record lets the usage and
    attribution extractors work from the AnswerSheet alone.
    """
    artifacts = {"trajectory_id": trajectory.trajectory_id}
    if folder is not None:
        artifacts["skill_folder"] = dict(folder)
    return ExecutionRecord(
        # A test phase runs the with-bank and control arms over the same
        # task/attempt trajectory ids; the unit id (which carries the arm's
        # injection shape) keeps the two answer sheets distinct.
        sheet_id=f"{unit_id}::{trajectory.trajectory_id}",
        unit_id=unit_id,
        phase_label=phase_label,
        task_ref=trajectory.task_id,
        role=trajectory.model_role,
        arm=arm,
        model_role=trajectory.model_role,
        model_name=trajectory.model_name,
        sample_index=trajectory.attempt_index,
        skills_injected=trajectory.skills_injected,
        steps=list(trajectory.steps),
        raw_correctness=trajectory.correctness,
        metrics=dict(trajectory.meta or {}),
        artifacts=artifacts,
        aux_success=aux_success,
        kind="trajectory",
    )


def execution_from_attempt(
        attempt: Attempt, *, unit_id: str, phase_label: str,
        arm: Optional[str] = None) -> ExecutionRecord:
    """The trial-level view of one exam answer (no scorer correctness)."""
    skills = None
    if attempt.skill_id is not None:
        skills = [{"skill_id": attempt.skill_id,
                   "version": attempt.skill_version}]
    return ExecutionRecord(
        sheet_id=attempt.attempt_id,
        unit_id=unit_id,
        phase_label=phase_label,
        task_ref=attempt.exam_id,
        role="weak",
        arm=arm,
        model_role="weak",
        model_name="",
        sample_index=ids.parse_attempt_id(attempt.attempt_id)[1],
        skills_injected=skills,
        answer=attempt.weak_answer,
        raw_correctness=None,
        metrics={},
        aux={"judge_verdict": attempt.judge_verdict,
             "judge_reason": attempt.judge_reason},
        artifacts={"attempt_id": attempt.attempt_id},
        kind="attempt",
    )


def execution_from_external(
        *, task_ref: str, unit_id: str, phase_label: str, reward: float,
        sample_index: int, source: str, sha256: str,
        arm: str = "control") -> ExecutionRecord:
    """One external-campaign reward as an explicit external trial.

    There is no local trajectory or verifier evidence; ``source`` and
    ``sha256`` are mandatory so the provenance cannot be mistaken for a
    local run.
    """
    return ExecutionRecord(
        sheet_id=f"external:{unit_id}:{sample_index}",
        unit_id=unit_id,
        phase_label=phase_label,
        task_ref=task_ref,
        role="weak",
        arm=arm,
        model_role="weak",
        model_name="external_campaign",
        sample_index=sample_index,
        rewards=[reward],
        raw_correctness=bool(reward > 0.0),
        metrics={"reward": reward},
        artifacts={"source": source, "sha256": sha256},
        source="external",
        kind="external",
    )


def assemble_answer_sheets(
        executions: Sequence[ExecutionRecord],
        coverage: Sequence[CoverageRecord], *, phase_label: str
        ) -> List[AnswerSheet]:
    """Join trial executions with coverage by ``(unit_id, attempt_index)``.

    An execution without an explicit coverage row gets the default
    ``present`` row.  A non-present coverage row without an execution
    becomes a gap sheet (``execution=None``).  The result is sorted by
    sheet id so a resume writes the same order.
    """
    by_key: Dict[tuple, CoverageRecord] = {
        (record.unit_id, record.attempt_index): record
        for record in coverage}
    sheets: List[AnswerSheet] = []
    used = set()
    for execution in executions:
        key = (execution.unit_id, execution.sample_index)
        record = by_key.get(key)
        if record is None:
            record = CoverageRecord(
                unit_id=execution.unit_id,
                attempt_index=execution.sample_index,
                present=True,
                status=COVERAGE_PRESENT,
                cause="",
                artifacts={},
            )
        used.add(key)
        sheets.append(AnswerSheet(
            sheet_id=execution.sheet_id,
            phase_label=execution.phase_label,
            coverage=record,
            execution=execution,
        ))
    for key, record in by_key.items():
        if key in used or record.present:
            continue
        sheets.append(AnswerSheet(
            sheet_id=make_gap_sheet_id(record.unit_id,
                                       record.attempt_index),
            phase_label=phase_label,
            coverage=record,
            execution=None,
        ))
    sheets.sort(key=lambda sheet: sheet.sheet_id)
    return sheets


def grade_answer_sheets(sheets: Sequence[AnswerSheet],
                        rule_name: str) -> List[AnswerSheet]:
    """Fill each present trial's grading record under one named rule."""
    for sheet in sheets:
        if sheet.execution is not None:
            sheet.grading = verdicts.grade_sheet(sheet.execution, rule_name)
    return list(sheets)


def write_answer_sheets(sheets: Iterable[AnswerSheet], directory: Path,
                        *, render_markdown: bool = True) -> None:
    """Write the derived answer sheets atomically (JSON, optionally .md)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    keep = set()
    for sheet in sheets:
        stem = object_filename(sheet.sheet_id)[:-len(".json")]
        keep.add(stem)
        write_json(directory / f"{stem}.json", sheet.to_dict(), indent=2)
        if render_markdown:
            from expedistill.blocks.view import render_answer_sheet
            text = render_answer_sheet(sheet)
            _write_text(directory / f"{stem}.md", text)
    # The directory is a derived view of THIS pass; a resumed pass that
    # measures fewer units must not leave an old sheet behind for the
    # summarizer to mistake for a current trial.
    for path in list(directory.glob("*.json")) + list(directory.glob("*.md")):
        if path.stem not in keep:
            path.unlink()


def write_coverage(records: Iterable[CoverageRecord], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(path, [record.to_dict() for record in records])


def _write_text(path: Path, text: str) -> None:
    """Atomic text write (same-directory temp + replace)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
