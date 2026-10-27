"""The review driver: decide, then collect evidence only where needed.

The baseline-gap path emits task-level ``add`` rows.  The live and exam
paths emit per-skill ``keep`` / ``revise`` / ``delete`` rows.  Evidence is
collected with the existing collectors, now owned by this package:

* add rows use segmentation plus the add-evidence collector;
* live revise rows use the per-attempt attribution collector;
* exam revise rows use the exam answer-sheet collector.

The driver never writes the phase ledgers.  The stage that calls it keeps
the task list, revision plan, outcomes and version guard.
"""

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set

from expedistill.benchmarks.registry import get_plugin
from expedistill.composites import decision, skill_usage
from expedistill.core.answer_sheet import PhaseReport, UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store
from expedistill.methods.review import ReviewParams
from expedistill.methods.review import add_evidence, attribution, exam_evidence
from expedistill.methods.review import segmentation, strategies
from expedistill.methods.review.contracts import (
    ReviewContext,
    ReviewItem,
    ReviewResult,
)

__all__ = ["review"]


def review(
    *,
    current: PhaseReport,
    control: Optional[PhaseReport],
    bank: Sequence[Skill],
    context: ReviewContext,
    params: ReviewParams,
    store: Store,
    handles: Mapping[str, Any],
    usage: Any = None,
    run_dir: Any = None,
    skip_evidence: Optional[
        Callable[[Sequence[TaskListRow]], Sequence[str]]] = None,
    existing_findings: Sequence[Any] = (),
) -> ReviewResult:
    """Run one learn phase's review; returns per-row items and failures."""
    if params.decision == "trial_level":
        from expedistill.methods.review.trial_review import run_trial_review
        return run_trial_review(
            current=current, bank=bank, context=context,
            handles=handles, usage=usage, run_dir=run_dir,
            existing_findings=existing_findings,
            skip_evidence=skip_evidence)
    rows = assess(
        current=current, control=control, bank=bank,
        context=context, params=params, store=store)
    superseded: List[str] = []
    if context.phase_kind == "exam":
        rows, superseded = strategies.merge_exam_rows(rows, current.units)
    already = set(skip_evidence(rows)) if skip_evidence is not None else set()
    result = diagnose(
        rows=rows, current=current, context=context, params=params,
        store=store, handles=handles, usage=usage, run_dir=run_dir,
        skip_evidence=already)
    result.superseded_units = superseded
    return result


def assess(
    *,
    current: PhaseReport,
    control: Optional[PhaseReport],
    bank: Sequence[Skill],
    context: ReviewContext,
    params: ReviewParams,
    store: Store,
) -> List[TaskListRow]:
    """The pure action decision for one learn phase."""
    if context.phase_kind == "baseline":
        return decision.decide_task_list(
            current_kind=None,
            current_report=[],
            control_kind="baseline",
            control_report=current.units,
            bank=bank,
            checks_left=context.checks_left,
            compare_rule=context.compare_rule,
            n_attempts_weak=context.n_attempts_weak,
            task_select_rule=context.task_select_rule)
    if context.phase_kind == "exam":
        return decision.decide_task_list(
            current_kind="exam",
            current_report=current.units,
            control_kind="baseline" if control is not None else None,
            control_report=control.units if control is not None else None,
            bank=bank,
            checks_left=context.checks_left,
            compare_rule=context.compare_rule,
            n_attempts_weak=context.n_attempts_weak,
            task_select_rule=context.task_select_rule)
    if context.phase_kind != "live":
        raise CodeBugError(
            f"review received unknown phase kind {context.phase_kind!r}; "
            "expected baseline / live / exam")
    usage_by_task: Dict[str, List[dict]] = {}
    for row in skill_usage.build_usage_rows_from_sheets(
            current.sheets, round_=context.round):
        task_id = row.get("task_id")
        if task_id:
            usage_by_task.setdefault(task_id, []).append(row)
    return strategies.decide(
        name=params.decision,
        current_kind="live",
        current_report=current.units,
        control_kind="baseline" if control is not None else None,
        control_report=control.units if control is not None else None,
        bank=bank,
        checks_left=context.checks_left,
        compare_rule=context.compare_rule,
        n_attempts_weak=context.n_attempts_weak,
        task_select_rule=context.task_select_rule,
        delivery=context.delivery,
        usage_by_task=usage_by_task,
        group_better_by_task=current.summary.get("better_by_task") or {},
        skill_status_by_id=_skill_statuses(store, current.units),
        unread_in_won_group=params.unread_in_won_group)


def _skill_statuses(store: Store,
                    units: Sequence[UnitSummary]) -> Dict[str, str]:
    """The current store status of every measured skill."""
    out: Dict[str, str] = {}
    for unit in units:
        refs = unit.injected_skills
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            skill_id = ref.get("skill_id")
            if not isinstance(skill_id, str) or skill_id in out:
                continue
            out[skill_id] = store.load("skills", skill_id, Skill).status
    return out


def diagnose(
    *,
    rows: Sequence[TaskListRow],
    current: PhaseReport,
    context: ReviewContext,
    params: ReviewParams,
    store: Store,
    handles: Mapping[str, Any],
    usage: Any,
    run_dir: Any,
    skip_evidence: Set[str] = frozenset(),
) -> ReviewResult:
    """Collect evidence for the rows that need it."""
    items = [ReviewItem(row=row) for row in rows]
    by_target = {item.row.target_id: item for item in items}
    result = ReviewResult(items=items)
    recorder = UnitFailureRecorder(run_dir) if run_dir is not None else None
    view_rules = get_plugin(context.benchmark).view_rules

    add_rows = [row for row in rows if row.action == decision.ADD]
    if add_rows:
        _diagnose_add(
            add_rows, by_target, store=store, handles=handles,
            context=context, params=params, usage=usage, run_dir=run_dir,
            view_rules=view_rules, recorder=recorder, result=result)

    revise_rows = [row for row in rows if row.action == decision.REVISE]
    if revise_rows and context.phase_kind == "live":
        revise_rows = [row for row in revise_rows
                       if row.target_id not in skip_evidence]
        _diagnose_live(
            revise_rows, by_target, current=current, store=store,
            handles=handles, context=context, params=params, usage=usage,
            run_dir=run_dir, recorder=recorder, result=result)
    elif revise_rows and context.phase_kind == "exam":
        revise_rows = [row for row in revise_rows
                       if row.target_id not in skip_evidence]
        _diagnose_exam(
            revise_rows, by_target, current=current, store=store,
            context=context, recorder=recorder, result=result)
    return result


def _diagnose_add(
    rows: Sequence[TaskListRow],
    by_target: Mapping[str, ReviewItem],
    *,
    store: Store,
    handles: Mapping[str, Any],
    context: ReviewContext,
    params: ReviewParams,
    usage: Any,
    run_dir: Any,
    view_rules: Any,
    recorder: Optional[UnitFailureRecorder],
    result: ReviewResult,
) -> None:
    def one(row: TaskListRow):
        try:
            segmentation.segment_add_material(
                store=store,
                task_id=row.target_id,
                params=params,
                trajectory_select=context.trajectory_select,
                view_rules=view_rules,
                handle=handles["segmenter"],
                usage=usage,
                run_dir=run_dir)
            package = add_evidence.collect_add_evidence(
                task_row=row,
                store=store,
                rule_name=context.segment_select,
                write_lessons=params.write_lessons,
                view_rules=view_rules)
            return row, package, None, None
        except add_evidence.NoAddMaterial:
            return row, None, None, None
        except InfrastructureError as exc:
            return row, None, exc, "infra"
        except ModelOutputError as exc:
            return row, None, exc, "model"

    workers = max(1, min(int(context.workers or 1), len(rows))) \
        if len(rows) > 1 else 1
    if workers <= 1:
        results = [one(row) for row in rows]
    else:
        results = run_parallel(one, list(rows), workers, thread=True)
    for row, package, exc, kind in results:
        if package is not None:
            by_target[row.target_id].evidence = package
        if exc is not None:
            _record(recorder, row.target_id, context.phase_label, exc)
            if kind == "infra":
                result.failed_targets.add(row.target_id)
            else:
                result.model_errors.add(row.target_id)


def _diagnose_live(
    rows: Sequence[TaskListRow],
    by_target: Mapping[str, ReviewItem],
    *,
    current: PhaseReport,
    store: Store,
    handles: Mapping[str, Any],
    context: ReviewContext,
    params: ReviewParams,
    usage: Any,
    run_dir: Any,
    recorder: Optional[UnitFailureRecorder],
    result: ReviewResult,
) -> None:
    grouped: Dict[str, List[TaskListRow]] = {}
    for row in rows:
        task_id, _ = decision.parse_unit_id(row.basis_unit)
        grouped.setdefault(task_id, []).append(row)
    usage_by_task: Dict[str, List[dict]] = {}
    for row in skill_usage.build_usage_rows_from_sheets(
            current.sheets, round_=context.round):
        task_id = row.get("task_id")
        if task_id:
            usage_by_task.setdefault(task_id, []).append(row)

    def one(item):
        task_id, task_rows = item
        try:
            skills = [store.load("skills", row.target_id, Skill)
                      for row in task_rows]
            evidence = attribution.collect_skill_evidence(
                task_id=task_id,
                skills=skills,
                sheets=current.sheets,
                round_=context.round,
                task_instruction=attribution.task_instruction_for(
                    context.benchmark, task_id),
                delivery=context.delivery,
                usage_rows=usage_by_task.get(task_id, []),
                weak_profile=context.weak_profile,
                author_handle=handles["author"],
                view_rules=get_plugin(context.benchmark).view_rules,
                params=params,
                usage=usage,
                run_dir=run_dir)
            return task_id, task_rows, evidence, None, None
        except InfrastructureError as exc:
            return task_id, task_rows, {}, exc, "infra"
        except ModelOutputError as exc:
            return task_id, task_rows, {}, exc, "model"

    workers = max(1, min(int(context.workers or 1), len(grouped))) \
        if len(grouped) > 1 else 1
    items = sorted(grouped.items())
    if workers <= 1:
        results = [one(item) for item in items]
    else:
        results = run_parallel(one, items, workers, thread=True)
    for task_id, task_rows, evidence, exc, kind in results:
        if exc is not None:
            _record(recorder, task_id, context.phase_label, exc)
            for row in task_rows:
                if kind == "infra":
                    result.failed_targets.add(row.target_id)
                else:
                    result.model_errors.add(row.target_id)
            continue
        for row in task_rows:
            package = evidence.get(row.target_id)
            if package is None:
                raise CodeBugError(
                    f"attribution returned no evidence for "
                    f"{row.target_id!r}")
            by_target[row.target_id].evidence = package


def _diagnose_exam(
    rows: Sequence[TaskListRow],
    by_target: Mapping[str, ReviewItem],
    *,
    current: PhaseReport,
    store: Store,
    context: ReviewContext,
    recorder: Optional[UnitFailureRecorder],
    result: ReviewResult,
) -> None:
    units = {unit.unit_id: unit for unit in current.units}

    def one(row: TaskListRow):
        try:
            unit = units.get(row.basis_unit)
            if unit is None:
                raise CodeBugError(
                    f"task-list row names basis unit {row.basis_unit!r}, "
                    "which the current report does not carry")
            skill = store.load("skills", row.target_id, Skill)
            package = exam_evidence.collect_exam_evidence(store, unit, skill)
            return row, package, None, None
        except InfrastructureError as exc:
            return row, None, exc, "infra"
        except ModelOutputError as exc:
            return row, None, exc, "model"

    workers = max(1, min(int(context.workers or 1), len(rows))) \
        if len(rows) > 1 else 1
    if workers <= 1:
        results = [one(row) for row in rows]
    else:
        results = run_parallel(one, list(rows), workers, thread=True)
    for row, package, exc, kind in results:
        if package is not None:
            by_target[row.target_id].evidence = package
        if exc is not None:
            _record(recorder, row.target_id, context.phase_label, exc)
            if kind == "infra":
                result.failed_targets.add(row.target_id)
            else:
                result.model_errors.add(row.target_id)


def _record(recorder: Optional[UnitFailureRecorder], target_id: str,
            phase_label: str, exc: BaseException) -> None:
    if recorder is not None:
        recorder.record(target_id, phase_label, exc, exc)
