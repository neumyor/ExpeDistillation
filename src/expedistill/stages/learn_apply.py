"""The P04/P06 per-skill apply path.

The decision module emits one ``TaskListRow`` per measured skill. This
module lands those rows:

* keep / delete rows are independent and run per skill;
* revise rows are grouped by the measured task, because attribution is
  one question per group, not one per skill. The group's attribution
  packages are then handed to the shared writer one skill at a time.

No per-skill policy lives here. The row already says keep, revise or
delete; the attribution driver only explains, and the author only writes.
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import tasks as tasks_block
from expedistill.composites import decision
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.core import ids
from expedistill.core.contracts import AcceptanceReportRow, TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store
from expedistill.methods.attribution.group import collect_group_attribution
from expedistill.methods.author.revise import revise_one
from expedistill.prompts import get as get_prompt

from expedistill.stages import learn_ledger as ledger

__all__ = ["execute_decision_rows"]


def execute_decision_rows(ctx: Any, phase: Any,
                          rows: Sequence[TaskListRow],
                          current_rows: Optional[List[AcceptanceReportRow]],
                          current_spec: Any, current_round: int) -> Set[str]:
    """Land per-skill decisions; return infra-failed target ids."""
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    recorder = UnitFailureRecorder(ctx.run_dir)
    ledger.write_task_list(phase_dir, phase.label, rows)
    _assert_unique(rows)

    simple = [row for row in rows
              if row.action in (decision.KEEP, decision.DELETE)]
    grouped: Dict[str, List[TaskListRow]] = {}
    for row in rows:
        if row.action != decision.REVISE:
            continue
        task_id, _ = decision.parse_unit_id(row.basis_unit)
        grouped.setdefault(task_id, []).append(row)

    def land_simple(row: TaskListRow) -> Tuple[str, Optional[BaseException]]:
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
        try:
            _execute_revise_group(ctx, phase, task_id, task_rows,
                                  current_rows, current_spec,
                                  current_round, phase_dir, recorder)
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


def _execute_revise_group(ctx: Any, phase: Any, task_id: str,
                          rows: List[TaskListRow],
                          current_rows: Optional[List[AcceptanceReportRow]],
                          current_spec: Any, current_round: int,
                          phase_dir, recorder: UnitFailureRecorder) -> None:
    """One task: one attribution call, then one writer call per skill."""
    if not rows:
        return
    basis = rows[0].basis_unit
    if any(row.basis_unit != basis for row in rows):
        raise CodeBugError(
            f"revise rows for task {task_id!r} name different basis units: "
            f"{sorted({row.basis_unit for row in rows})}")
    report_row = _report_row_of(current_rows, basis)
    store: Store = ctx.store
    members = _group_members(store, report_row, task_id)
    by_id = {skill.skill_id: skill for skill in members}

    todo: List[TaskListRow] = []
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
            todo.append(row)
        elif skill.version == from_version + 1:
            ledger.append_outcome(phase, row, skill.skill_id, "revised",
                                  skill.version, phase_dir)
        elif skill.version != from_version:
            raise CodeBugError(
                f"skill {skill.skill_id!r} is at version {skill.version} "
                f"but this phase's revision ledger recorded "
                f"{from_version}: the store drifted from the ledger")
        else:
            todo.append(row)
    if not todo:
        return

    task_instruction = _task_instruction(ctx, task_id)
    packages = collect_group_attribution(
        task_id=task_id,
        members=members,
        report_row=report_row,
        store=store,
        round_=current_round,
        n_attempts=_n_attempts(ctx, current_spec),
        task_instruction=task_instruction,
        weak_profile=ctx.config.effective_weak_profile(),
        author_handle=ctx.handle("author"),
        view_rules=get_plugin(ctx.config.benchmark).view_rules,
        usage=ctx.usage,
        run_dir=ctx.run_dir)
    package_by_id = {package.target: package for package in packages}

    for row in todo:
        skill = by_id.get(row.target_id)
        if skill is None:
            raise CodeBugError(
                f"revise row targets skill {row.target_id!r}, which is not "
                "a measured member of the group")
        package = package_by_id.get(skill.skill_id)
        if package is None:
            raise CodeBugError(
                f"attribution returned no package for {skill.skill_id!r}")
        ledger.write_revision_plan(phase, row, skill.skill_id,
                                   skill.version, phase_dir)
        try:
            outcome = revise_one(
                package,
                weak_profile=ctx.config.effective_weak_profile(),
                handle=ctx.handle("rewriter"),
                usage=ctx.usage, run_dir=ctx.run_dir)
        except ModelOutputError as exc:
            recorder.record(skill.skill_id, phase.label, exc, exc)
            continue
        _land_revision(ctx, phase, row, package, outcome, phase_dir)


def _land_revision(ctx: Any, phase: Any, row: TaskListRow, package: Any,
                   outcome: Any, phase_dir) -> None:
    """Persist one writer outcome, then its receipt (the store is truth)."""
    store: Store = ctx.store
    skill_id = outcome.skill_id
    old = package.old_skill
    if outcome.outcome == "unchanged":
        ledger.append_outcome(phase, row, skill_id, "unchanged",
                              old.version, phase_dir)
        return
    if outcome.outcome == "infra_failed":
        ledger.append_outcome(phase, row, skill_id, "infra_failed",
                              old.version, phase_dir)
        raise InfrastructureError(
            f"the revision of {skill_id!r} failed on infrastructure")
    if outcome.outcome not in ("revised", "retriggered"):
        raise CodeBugError(
            f"the writer reported outcome {outcome.outcome!r} for "
            f"{skill_id!r}; expected revised / retriggered / unchanged / "
            "infra_failed")
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
        store.save(stored, "skills")
    elif not (stored.version == expected
              and stored.content == outcome.content
              and stored.summary == outcome.summary):
        raise CodeBugError(
            f"skill {skill_id!r} is at version {stored.version} with "
            "content the author did not produce; the store drifted from "
            "the revision ledger")
    ledger.append_outcome(phase, row, skill_id, outcome.outcome, expected,
                          phase_dir)


def _group_members(store: Store, report_row: AcceptanceReportRow,
                   task_id: str) -> List[Skill]:
    refs = report_row.injected_skills
    if isinstance(refs, list) and refs:
        members = []
        for ref in refs:
            if not isinstance(ref, dict) or not ref.get("skill_id"):
                raise CodeBugError(
                    f"report row {report_row.unit_id!r} carries a malformed "
                    f"injected skill reference: {ref!r}")
            members.append(
                store.load("skills", ref["skill_id"], Skill))
        return members
    from expedistill.composites.live_measurement import pending_groups
    return list(pending_groups(store).get(task_id, []))


def _report_row_of(current_rows: Optional[List[AcceptanceReportRow]],
                   basis_unit: str) -> AcceptanceReportRow:
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


def _task_instruction(ctx: Any, task_id: str) -> str:
    benchmark = ctx.config.benchmark
    plugin = get_plugin(benchmark)
    raw_id = ids.raw_task_id(task_id, benchmark)
    loaded = tasks_block.load_tasks(benchmark, [raw_id])
    match = [task for task in loaded if task.task_id == raw_id]
    if not match:
        raise CodeBugError(
            f"benchmark {benchmark!r} did not load task {raw_id!r} for "
            f"group {task_id!r}: the arms must never run a different "
            "set than the groups")
    return get_prompt(plugin.task_prompt_name).format(
        instruction=match[0].instruction, skills_block="", history="")


def _n_attempts(ctx: Any, spec: Any) -> int:
    return spec.execution.get("n_attempts") or ctx.config.n_attempts_weak


def _assert_unique(rows: Sequence[TaskListRow]) -> None:
    seen = set()
    for row in rows:
        if row.target_id in seen:
            raise CodeBugError(
                f"the decision carries two rows for target "
                f"{row.target_id!r}; each target must have exactly one")
        seen.add(row.target_id)
