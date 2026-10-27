"""The P02 add landing: review add items -> new pending skills.

The review driver has already decided which tasks need an add row and
has collected the source experiences.  This module only writes new
skills, filters already-covered segments, and records outcomes, so a
resume never calls the segmenter again for a task whose review result is
already on disk.
"""

from typing import Any, Sequence, Set, Tuple

from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store
from expedistill.methods import registry as method_registry
from expedistill.methods.review.contracts import ReviewItem

from expedistill.stages import learn_ledger as ledger

__all__ = ["execute_add_rows"]


def execute_add_rows(ctx: Any, phase: Any,
                     items: Sequence[ReviewItem],
                     *, skip_targets: Sequence[str] = ()) -> Set[str]:
    """Land every add item; return infra-failed task ids."""
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    rows = [item.row for item in items]
    ledger.write_task_list(phase_dir, phase.label, rows)
    targets = [row.target_id for row in rows]
    if len(set(targets)) != len(targets):
        raise ValueError(
            "the add task list carries duplicate task ids; each task must "
            "have exactly one row")

    if phase.spec.author.get("name") == "cluster_by_trigger":
        from expedistill.stages import learn_add_cluster
        _driver, author_params = method_registry.resolve(
            "author", phase.spec.author)
        return learn_add_cluster.execute_add_rows(
            ctx, phase, items, phase_dir, author_params=author_params,
            skip_targets=skip_targets)

    skipped = set(skip_targets)
    recorder = UnitFailureRecorder(ctx.run_dir)

    def land(item: ReviewItem) -> Tuple[str, Any]:
        row = item.row
        if row.target_id in skipped:
            return row.target_id, None
        try:
            _land_add(ctx, phase, item, phase_dir)
            return row.target_id, None
        except InfrastructureError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, exc
        except ModelOutputError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, None

    workers = max(1, int(getattr(ctx.run_settings, "workers", 1) or 1))
    if workers == 1 or len(items) <= 1:
        results = [land(item) for item in items]
    else:
        results = run_parallel(land, list(items), workers, thread=True)
    return {target_id for target_id, failure in results
            if failure is not None}


def _has_outcome(phase: Any, row: Any, phase_dir) -> bool:
    return bool(ledger.rows_of(phase_dir / ledger.OUTCOMES_NAME,
                               phase.label, row.target_id))


def _land_add(ctx: Any, phase: Any, item: ReviewItem, phase_dir) -> None:
    """One task's add landing; a per-skill receipt is the completion proof."""
    store: Store = ctx.store
    row = item.row
    package = item.evidence
    if package is None:
        if not _has_outcome(phase, row, phase_dir):
            ledger.append_outcome(phase, row, None, "skipped_no_material",
                                  None, phase_dir)
        return
    fresh = [experience for experience in package.source_experiences
             if not store.exists(
                 "skills",
                 _skill_id_for(experience))]
    if not fresh:
        if not _has_outcome(phase, row, phase_dir):
            ledger.append_outcome(phase, row, None, "skipped_existing", None,
                                  phase_dir)
        return
    fresh_ids = {experience["subtask"].subtask_id for experience in fresh}
    filtered = type(package)(
        target=package.target, facts=list(package.facts),
        segments=[segment for segment in package.segments
                  if segment["subtask_id"] in fresh_ids],
        source_experiences=fresh)
    author_driver, author_params = method_registry.resolve(
        "author", phase.spec.author)
    skills = author_driver.write_new(
        task_row=row, package=filtered,
        author_handle=ctx.handle("author"),
        weak_profile=ctx.config.effective_weak_profile(),
        usage=ctx.usage, run_dir=ctx.run_dir, params=author_params)
    for skill in skills:
        store.save(skill, "skills")
        ledger.append_outcome(phase, row, skill.skill_id, "added",
                              skill.version, phase_dir)


def _skill_id_for(experience: Any) -> str:
    from expedistill.core.ids import make_skill_id
    return make_skill_id(experience["subtask"].subtask_id)
