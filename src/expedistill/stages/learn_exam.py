"""The exam learn landing: review items -> keep / delete / revise.

The review driver merges the exam report's multi-version rows and
collects the answer-sheet evidence.  This module only lands the rows and
keeps the revision ledger's version guard.
"""

import json
from typing import Any, List, Optional, Sequence, Set, Tuple

from expedistill.composites import decision
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.skill_origins import append_origin, source_ref_for_unit
from expedistill.core.answer_sheet import UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Exam, Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store
from expedistill.methods.author import write as author
from expedistill.methods.review.contracts import ReviewItem

from expedistill.stages import learn_ledger as ledger

__all__ = ["execute_exam_rows"]


def execute_exam_rows(ctx: Any, phase: Any, items: Sequence[ReviewItem],
                      current_rows: Optional[List[UnitSummary]],
                      superseded: Sequence[str], *,
                      skip_targets: Sequence[str] = (),
                      source_phase_label: str = "") -> Set[str]:
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    recorder = UnitFailureRecorder(ctx.run_dir)
    rows = [item.row for item in items]
    ledger.write_task_list(phase_dir, phase.label, rows, superseded)
    _assert_unique(rows)
    skipped = set(skip_targets)

    def land(item: ReviewItem) -> Tuple[str, Optional[BaseException]]:
        if item.row.target_id in skipped:
            return item.row.target_id, None
        try:
            _execute_row(ctx, phase, item, current_rows, phase_dir)
            return item.row.target_id, None
        except InfrastructureError as exc:
            recorder.record(item.row.target_id, phase.label, exc, exc)
            return item.row.target_id, exc
        except ModelOutputError as exc:
            recorder.record(item.row.target_id, phase.label, exc, exc)
            return item.row.target_id, None

    workers = max(1, int(getattr(ctx.run_settings, "workers", 1) or 1))
    if workers == 1 or len(items) <= 1:
        results = [land(item) for item in items]
    else:
        results = run_parallel(land, list(items), workers, thread=True)
    return {target_id for target_id, failure in results
            if failure is not None}


def _execute_row(ctx: Any, phase: Any, item: ReviewItem,
                 current_rows: Optional[List[UnitSummary]],
                 phase_dir) -> None:
    row = item.row
    if row.target_kind != "skill":
        raise CodeBugError(
            f"the exam path expects skill rows, got "
            f"{row.target_kind!r} {row.target_id!r}")
    if row.action == decision.KEEP:
        ledger.land_keep(ctx.store, phase, row, phase_dir)
    elif row.action == decision.DELETE:
        ledger.land_delete(ctx.store, phase, row, phase_dir)
    elif row.action == decision.REVISE:
        _land_revise(ctx, phase, item, current_rows, phase_dir)
    else:
        raise CodeBugError(
            f"the exam task list carries action {row.action!r} on target "
            f"{row.target_id!r}; expected keep / revise / delete")


def _land_revise(ctx: Any, phase: Any, item: ReviewItem,
                 current_rows: Optional[List[UnitSummary]],
                 phase_dir) -> None:
    store: Store = ctx.store
    row = item.row
    skill = store.load("skills", row.target_id, Skill)
    if skill.status not in ledger.ACTABLE_STATUSES:
        ledger.append_outcome(phase, row, skill.skill_id,
                              "skipped_terminal", skill.version, phase_dir)
        return
    receipts = ledger.receipted_skills(phase, row, phase_dir)
    if skill.skill_id in receipts:
        return
    revisions = ledger.revision_rows(phase, row, phase_dir)
    from_version = revisions.get(skill.skill_id)
    if from_version is not None:
        if skill.version == from_version + 1:
            ledger.append_outcome(phase, row, skill.skill_id, "revised",
                                  skill.version, phase_dir)
            return
        if skill.version != from_version:
            raise CodeBugError(
                f"skill {skill.skill_id!r} is at version {skill.version} "
                f"but this phase's revision ledger recorded "
                f"{from_version}: the store drifted from the ledger")
    _report_row_of(current_rows, row.basis_unit)
    package = item.evidence
    if package is None:
        raise CodeBugError(
            f"review returned no exam evidence for {skill.skill_id!r}")
    ledger.write_revision_plan(phase, row, skill.skill_id, skill.version,
                               phase_dir)
    outcomes = author.revise_group(
        task_row=row, packages=[package],
        rewriter_handle=ctx.handle("rewriter"),
        weak_profile=ctx.config.effective_weak_profile(),
        decide=_always_revise, usage=ctx.usage, run_dir=ctx.run_dir)
    if not outcomes:
        return
    _land_revision(ctx, phase, row, package, outcomes[0], phase_dir,
                   current_rows)


def _always_revise(package: Any) -> str:
    return "revise"


def _land_revision(ctx: Any, phase: Any, row: TaskListRow, package: Any,
                   outcome: Any, phase_dir,
                   current_rows: Optional[List[UnitSummary]] = None) -> None:
    store: Store = ctx.store
    skill_id = outcome.skill_id
    if outcome.outcome == "unchanged":
        ledger.append_outcome(phase, row, skill_id, "unchanged",
                              package.old_skill.version, phase_dir)
        return
    if outcome.outcome == "infra_failed":
        ledger.append_outcome(phase, row, skill_id, "infra_failed",
                              package.old_skill.version, phase_dir)
        raise InfrastructureError(
            f"the revision of {skill_id!r} failed on infrastructure")
    if outcome.outcome != "revised":
        raise CodeBugError(
            f"the writer reported outcome {outcome.outcome!r} for "
            f"{skill_id!r}; expected revised / unchanged / infra_failed")
    stored = store.load("skills", skill_id, Skill)
    expected = package.old_skill.version + 1
    if stored.version == package.old_skill.version:
        stored.rewrite_history.append({
            "content": stored.content,
            "summary": stored.summary,
            "error_category": outcome.failure_category,
            "reason": outcome.revision_reason,
        })
        stored.content = outcome.content
        stored.summary = outcome.summary
        stored.version = expected
        store.save(stored, "skills")
    elif not (stored.version == expected
              and stored.content == outcome.content
              and stored.summary == outcome.summary):
        raise CodeBugError(
            f"skill {skill_id!r} is at version {stored.version} with "
            "content the author did not produce: the store drifted from "
            "the revision ledger")
    summary = _report_row_of(current_rows, row.basis_unit)
    exam = None
    if summary.task_ref:
        try:
            exam = store.load("exams", summary.task_ref, Exam)
        except Exception:
            exam = None
    ref = (exam.source_ref if exam is not None
           else source_ref_for_unit(
               summary.unit_id,
               sheet_id=(summary.sheet_ids[0] if summary.sheet_ids else None)))
    append_origin(
        phase_dir, skill_id=skill_id, version=expected,
        phase_label=phase.label,
        change_note=(outcome.revision_reason
                     or package.fact("judge_reason")
                     or "exam revision"),
        context_text=json.dumps(package.facts, ensure_ascii=False),
        source_ref=ref)
    ledger.append_outcome(phase, row, skill_id, outcome.outcome, expected,
                          phase_dir)


def _report_row_of(current_rows: Optional[List[UnitSummary]],
                   basis_unit: str) -> UnitSummary:
    if not current_rows:
        raise CodeBugError(
            f"task-list row names basis unit {basis_unit!r} but the "
            "current report is empty: the decision and the executor "
            "disagree")
    for row in current_rows:
        if row.unit_id == basis_unit:
            return row
    raise CodeBugError(
        f"task-list row names basis unit {basis_unit!r}, which the "
        "current report does not carry")


def _assert_unique(rows: Sequence[TaskListRow]) -> None:
    seen = set()
    for row in rows:
        if row.target_id in seen:
            raise CodeBugError(
                f"the exam task list carries two rows for target "
                f"{row.target_id!r}; each target must have exactly one")
        seen.add(row.target_id)
