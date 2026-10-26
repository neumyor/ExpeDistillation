"""The P02 add path: task rows -> segments -> new pending skills.

This is the original learn-phase add path, kept separate from the
P04/P06 per-skill decision path. The decision still comes from
``composites.decision``'s baseline-gap rule; this module owns only the
landing: segmentation, evidence collection, writing and the receipts.
"""

from typing import Any, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.core import ids
from expedistill.core.contracts import TaskListRow
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store
from expedistill.methods import registry as method_registry
from expedistill.methods import resolve_params
from expedistill.methods.evidence import add_path, segmentation
from expedistill.methods.evidence.package import EvidencePackage

from expedistill.stages import learn_ledger as ledger

__all__ = ["execute_add_rows"]


def execute_add_rows(ctx: Any, phase: Any,
                     rows: Sequence[TaskListRow]) -> Set[str]:
    """Land every task-level add row; return infra-failed task ids."""
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    recorder = UnitFailureRecorder(ctx.run_dir)
    ledger.write_task_list(phase_dir, phase.label, rows)
    targets = [row.target_id for row in rows]
    if len(set(targets)) != len(targets):
        raise ValueError(
            "the add task list carries duplicate task ids; each task must "
            "have exactly one row")

    if phase.spec.author.get("name") == "cluster_by_trigger":
        # New cross-task writing path: same decision rows and same
        # evidence collectors, a different writing landing. The existing
        # distill_diagnose path below is untouched.
        from expedistill.stages import learn_add_cluster
        _driver, author_params = method_registry.resolve(
            "author", phase.spec.author)
        return learn_add_cluster.execute_add_rows(
            ctx, phase, rows, phase_dir, author_params=author_params)

    def land(row: TaskListRow) -> Tuple[str, Optional[BaseException]]:
        try:
            _land_add(ctx, phase, row, phase_dir)
            return row.target_id, None
        except InfrastructureError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, exc
        except ModelOutputError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, None

    workers = max(1, int(getattr(ctx.run_settings, "workers", 1) or 1))
    if workers == 1 or len(rows) <= 1:
        results = [land(row) for row in rows]
    else:
        results = run_parallel(land, list(rows), workers, thread=True)
    return {target_id for target_id, failure in results
            if failure is not None}


def _has_outcome(phase: Any, row: TaskListRow, phase_dir) -> bool:
    return bool(ledger.rows_of(phase_dir / ledger.OUTCOMES_NAME,
                               phase.label, row.target_id))


def _land_add(ctx: Any, phase: Any, row: TaskListRow, phase_dir) -> None:
    """One task's add landing; a per-skill receipt is the completion proof.

    The task may name several source segments, so "one outcome row exists"
    is NOT "the task is done": a crash between two ``store.save`` calls
    leaves the first skill receipted and the rest unwritten. Re-entering
    therefore recomputes the fresh set and writes the missing skills;
    only a pass with nothing fresh (and an existing receipt) returns
    silently, keeping the replay idempotent.
    """
    store: Store = ctx.store
    evidence_params = resolve_params("evidence", phase.spec.evidence)
    author_driver, author_params = method_registry.resolve(
        "author", phase.spec.author)
    view_rules = get_plugin(ctx.config.benchmark).view_rules
    segmentation.segment_add_material(
        store=store,
        task_id=row.target_id,
        params=evidence_params,
        trajectory_select=ctx.config.rules["trajectory_select"],
        view_rules=view_rules,
        handle=ctx.handle("segmenter"),
        usage=ctx.usage,
        run_dir=ctx.run_dir)
    try:
        package = add_path.collect_add_evidence(
            task_row=row, store=store,
            rule_name=ctx.config.rules["segment_select"],
            write_lessons=evidence_params.write_lessons,
            view_rules=view_rules)
    except add_path.NoAddMaterial:
        if not _has_outcome(phase, row, phase_dir):
            ledger.append_outcome(phase, row, None, "skipped_no_material",
                                  None, phase_dir)
        return
    fresh = [experience for experience in package.source_experiences
             if not store.exists(
                 "skills",
                 ids.make_skill_id(experience["subtask"].subtask_id))]
    if not fresh:
        if not _has_outcome(phase, row, phase_dir):
            ledger.append_outcome(phase, row, None, "skipped_existing", None,
                                  phase_dir)
        return
    fresh_ids = {experience["subtask"].subtask_id for experience in fresh}
    filtered = EvidencePackage(
        target=package.target, facts=list(package.facts),
        segments=[segment for segment in package.segments
                  if segment["subtask_id"] in fresh_ids],
        source_experiences=fresh)
    skills = author_driver.write_new(
        task_row=row, package=filtered,
        author_handle=ctx.handle("author"),
        weak_profile=ctx.config.effective_weak_profile(),
        usage=ctx.usage, run_dir=ctx.run_dir, params=author_params)
    for skill in skills:
        store.save(skill, "skills")
        ledger.append_outcome(phase, row, skill.skill_id, "added",
                              skill.version, phase_dir)
