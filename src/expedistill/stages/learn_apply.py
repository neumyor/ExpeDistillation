"""The P04/P06 per-skill apply path.

The review driver has already decided each skill's action and collected
the evidence for the revise rows.  This module only lands those rows:

* keep / delete are independent and run per skill;
* revise rows are grouped by task only to share the stored trajectories;
  the evidence for each skill comes from the review item.

No policy lives here: the row says keep/revise/delete; the writer only
writes.
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.composites import decision, trace
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.trace import render_context
from expedistill.core.answer_sheet import UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.core.trace import Provenance
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store
from expedistill.methods.author.revise import revise_one
from expedistill.methods.review import attribution
from expedistill.methods.review.contracts import ReviewItem

from expedistill.stages import learn_ledger as ledger

__all__ = ["execute_decision_rows"]


def execute_decision_rows(
    ctx: Any, phase: Any, items: Sequence[ReviewItem],
    current_rows: Optional[List[UnitSummary]], *,
    skip_targets: Sequence[str] = (),
    weak_profile: Optional[str] = None,
    source_phase_label: str = "",
) -> Set[str]:
    """Land per-skill decisions; return infra-failed target ids."""
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    rows = [item.row for item in items]
    recorder = UnitFailureRecorder(ctx.run_dir)
    ledger.write_task_list(phase_dir, phase.label, rows)
    _assert_unique(rows)
    skipped = set(skip_targets)
    evidence_by_target = {
        item.row.target_id: item.evidence
        for item in items
        if item.row.action == decision.REVISE}

    simple = [row for row in rows
              if row.action in (decision.KEEP, decision.DELETE)]
    grouped: Dict[str, List[TaskListRow]] = {}
    for row in rows:
        if row.action != decision.REVISE:
            continue
        task_id, _ = decision.parse_unit_id(row.basis_unit)
        grouped.setdefault(task_id, []).append(row)

    def land_simple(row: TaskListRow) -> Tuple[str, Optional[BaseException]]:
        if row.target_id in skipped:
            return row.target_id, None
        try:
            ledger.land_keep_or_delete(ctx.store, phase, row, phase_dir)
            return row.target_id, None
        except InfrastructureError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, exc
        except ModelOutputError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, None

    def land_group(item: Tuple[str, List[TaskListRow]]
                   ) -> Tuple[str, Optional[BaseException]]:
        task_id, task_rows = item
        if task_id in skipped or any(
                row.target_id in skipped for row in task_rows):
            return task_id, None
        try:
            _execute_revise_group(
                ctx, phase, task_id, task_rows, current_rows, phase_dir,
                recorder, evidence_by_target, weak_profile)
            return task_id, None
        except InfrastructureError as exc:
            recorder.record(task_id, phase.label, exc, exc)
            return task_id, exc
        except ModelOutputError as exc:
            recorder.record(task_id, phase.label, exc, exc)
            return task_id, None

    work: List[Tuple[str, Any]] = [("simple", row) for row in simple]
    work.extend(("group", item) for item in sorted(grouped.items()))

    def land(item: Tuple[str, Any]) -> Tuple[str, Optional[BaseException]]:
        kind, payload = item
        if kind == "simple":
            return land_simple(payload)
        return land_group(payload)

    workers = max(1, int(getattr(ctx.run_settings, "workers", 1) or 1))
    if workers == 1 or len(work) <= 1:
        results = [land(item) for item in work]
    else:
        results = run_parallel(land, work, workers, thread=True)
    return {target_id for target_id, failure in results
            if failure is not None}


def _execute_revise_group(
    ctx: Any, phase: Any, task_id: str, rows: List[TaskListRow],
    current_rows: Optional[List[UnitSummary]], phase_dir,
    recorder: UnitFailureRecorder,
    evidence_by_target: Dict[str, Any],
    weak_profile: Optional[str] = None,
) -> None:
    """One task: one writer call per skill from the review evidence."""
    if not rows:
        return
    basis = rows[0].basis_unit
    if any(row.basis_unit != basis for row in rows):
        raise CodeBugError(
            f"revise rows for task {task_id!r} name different basis units: "
            f"{sorted({row.basis_unit for row in rows})}")
    _report_row_of(current_rows, basis)
    store: Store = ctx.store
    todo: List[Tuple[TaskListRow, Skill]] = []
    for row in rows:
        skill = store.load("skills", row.target_id, Skill)
        if skill.status not in ledger.ACTABLE_STATUSES:
            ledger.append_outcome(phase, row, skill.skill_id,
                                  "skipped_terminal", skill.version,
                                  phase_dir)
            continue
        if skill.skill_id in ledger.receipted_skills(phase, row, phase_dir):
            continue
        revisions = ledger.revision_rows(phase, row, phase_dir)
        from_version = revisions.get(skill.skill_id)
        if from_version is None:
            todo.append((row, skill))
        elif skill.version == from_version + 1:
            ledger.append_outcome(phase, row, skill.skill_id, "revised",
                                  skill.version, phase_dir)
        elif skill.version != from_version:
            raise CodeBugError(
                f"skill {skill.skill_id!r} is at version {skill.version} "
                f"but this phase's revision ledger recorded "
                f"{from_version}: the store drifted from the ledger")
        else:
            todo.append((row, skill))
    if not todo:
        return

    for row, skill in todo:
        package = evidence_by_target.get(skill.skill_id)
        if package is None:
            raise CodeBugError(
                f"review returned no evidence for {skill.skill_id!r}")
        ledger.write_revision_plan(phase, row, skill.skill_id,
                                   skill.version, phase_dir)
        try:
            outcome = revise_one(
                skill, package,
                weak_profile=(weak_profile
                              if weak_profile is not None
                              else ctx.config.effective_weak_profile()),
                handle=ctx.handle("rewriter"),
                context_of=_context_renderer(ctx),
                usage=ctx.usage, run_dir=ctx.run_dir)
        except ModelOutputError as exc:
            recorder.record(skill.skill_id, phase.label, exc, exc)
            continue
        _land_revision(ctx, phase, row, skill, outcome, phase_dir,
                       current_rows, package)


def _context_renderer(ctx: Any):
    """``TraceRef`` -> the rendered real steps around it (one lookup)."""
    plugin = get_plugin(ctx.config.benchmark)

    def render(ref):
        return render_context(trace.resolve(
            ctx.run_dir, ref,
            instruction_for=lambda task: attribution.task_instruction_for(
                ctx.config.benchmark, task),
            view_rules=plugin.view_rules))

    return render


def _land_revision(ctx: Any, phase: Any, row: TaskListRow, old: Skill,
                   outcome: Any, phase_dir,
                   current_rows: Optional[List[UnitSummary]] = None,
                   package: Any = None) -> None:
    """Persist one writer outcome, then its receipt (the store is truth)."""
    store: Store = ctx.store
    skill_id = outcome.skill_id
    if outcome.outcome == "unchanged":
        ledger.append_outcome(phase, row, skill_id, "unchanged",
                              old.version, phase_dir)
        return
    if outcome.outcome == "infra_failed":
        ledger.append_outcome(phase, row, skill_id, "infra_failed",
                              old.version, phase_dir)
        raise InfrastructureError(
            f"the revision of {skill_id!r} failed on infrastructure")
    if outcome.outcome != "revised":
        raise CodeBugError(
            f"the writer reported outcome {outcome.outcome!r} for "
            f"{skill_id!r}; expected revised / unchanged / infra_failed")
    stored = store.load("skills", skill_id, Skill)
    expected = old.version + 1
    if stored.version == old.version:
        stored.rewrite_history.append({
            "content": stored.content,
            "summary": stored.summary,
            "error_category": outcome.failure_category,
            "reason": outcome.revision_reason,
        })
        stored.content = outcome.content
        stored.summary = outcome.summary
        stored.version = expected
    elif not (stored.version == expected
              and stored.content == outcome.content
              and stored.summary == outcome.summary):
        raise CodeBugError(
            f"skill {skill_id!r} is at version {stored.version} with "
            "content the author did not produce; the store drifted from "
            "the revision ledger")
    _report_row_of(current_rows, row.basis_unit)
    windows = list(getattr(package, "windows", None) or [])
    if not windows:
        raise CodeBugError(
            f"revision evidence for {skill_id!r} carries no window")
    # The first window is the one the writer is shown first; its own
    # attempt and step are the scene, so the next exam is cut from the very
    # place the rewrite was learned from.
    ledger.record_version(
        store, phase_dir, ctx.run_dir, stored,
        Provenance(
            skill_id=skill_id, version=expected, phase_label=phase.label,
            cause="revise", scene=windows[0].ref,
            sources=[window.ref for window in windows],
            note=outcome.revision_reason or "revised"))
    ledger.append_outcome(phase, row, skill_id, "revised", expected,
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
                f"the decision carries two rows for target "
                f"{row.target_id!r}; each target must have exactly one")
        seen.add(row.target_id)
