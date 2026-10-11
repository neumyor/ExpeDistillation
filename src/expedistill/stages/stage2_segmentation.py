"""Stage 2: segment the failure set's trajectories (README 3, stage 2).

Wiring only (layer 6): the stage finds the stored stage-1 trajectories of
every task in ``F`` (both roles), groups them by task, and applies the
frozen trajectory_select rule per task -- the rule sees all of one task's
NON-EMPTY trajectories (both roles mixed) and returns the sublist to
segment, because the policy question is which of a task's samples to
segment (README stage 2). 0-step trajectories (an agent that died before
acting) are excluded BEFORE the rule: segmenting nothing is undefined
input for G1/D7, so each one gets a "skipped" record in K2's audit channel
(reason "empty trajectory", no model call) and is listed in the summary's
``empty_skipped``. The surviving trajectories are handed to K2, which owns
per-trajectory persistence and resume -- a stored subtask means the unit is
done and the segmenter model is not called again for it, so rerunning this
stage on a populated store issues zero model calls.

The rule decides WHICH trajectories, the stage decides the ORDER: rules are
applied to the tasks in sorted task_id order and the union of the selected
trajectories is processed in sorted trajectory_id order (core.ids: serial-
based IDs stay reproducible only under a fixed creation order; here nothing
is serial-numbered, but the fixed order keeps the run's audit log and the
model-call sequence deterministic across resumes whatever order a rule
returned).

The summary is cumulative over the run directory, not over this call: K2
returns stored subtasks for finished trajectories and None, without a model
call, for trajectories an earlier call already skipped (exhaustion is
terminal within a run), so re-running a finished stage reproduces the same
summary with zero model calls. ``infra_failed`` lists the holes still open
after this call (infrastructure failures are the only re-offered units).

Per-trajectory failure disposition (AGENTS.md section 6 rule 6): G1 output
errors stay inside K2 (retry rounds, then a "skipped" audit record). An
``InfrastructureError`` is not a segmentation failure: the trajectory is
recorded in the unit-failure ledger (unit id = trajectory_id), nothing is
stored or audited for it, and the remaining trajectories are still
segmented; after the loop the summary is written (``infra_failed`` lists the
ids) and the stage raises ``InfrastructureError`` so the pipeline never
advances past a stage with holes. A resume re-offers exactly those
trajectories. Every other exception propagates at once.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks.rules import get_trajectory_select
from expedistill.composites.segment_checked import segment_checked
from expedistill.core.ids import parse_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import InfrastructureError, UnitFailureRecorder
from expedistill.infra.store import append_jsonl, read_jsonl, write_json
from expedistill.stages.context import StageContext

__all__ = ["run"]

_SUMMARY_NAME = "stage2_summary.json"

#: K2's audit channel (composites.segment_checked._AUDIT_PATH), so every
#: reason a trajectory of this run was never segmented lives in one file.
#: Duplicated here because the composite owns it privately.
_AUDIT_PATH = Path("stage2_segments") / "segmentation_audit.jsonl"

#: Stage label written into the unit-failure ledger.
_FAILURE_STAGE = "stage2_segmentation"


def _plugin(benchmark: str) -> Any:
    """The benchmark plugin, imported so its registration has run.

    Same lazy-import contract as E1: a plugin registers at import of its
    package, and a stage-2-only resume must not depend on stage 0 having
    run in this process to have triggered it.
    """
    importlib.import_module("expedistill.benchmarks." + benchmark)
    return get_plugin(benchmark)


def _failed_trajectories_by_task(
        store: Any,
        failed_task_ids: Sequence[str],
) -> Tuple[Dict[str, List[Trajectory]], List[str]]:
    """Failed tasks' stored trajectories grouped by task, plus the empties.

    Returns ``(by_task, empty)``: each group holds one task's NON-EMPTY
    trajectories of both roles, sorted by trajectory_id -- the exact shape
    the per-task trajectory_select rule is called with (the rule's contract
    is "which non-empty trajectories to segment") -- and ``empty`` the ids
    of the 0-step trajectories, sorted. A failed task with nothing stored
    has no group (and so no rule call): there is nothing for any rule to
    select.
    """
    failed = set(failed_task_ids)
    by_task: Dict[str, List[Trajectory]] = {}
    empty: List[str] = []
    for trajectory_id in store.ids("trajectories"):
        try:
            task_id, _role, _attempt = parse_trajectory_id(trajectory_id)
        except ValueError:
            continue  # not a trajectory id shape; nothing else lands here
        if task_id not in failed:
            continue
        trajectory = store.load("trajectories", trajectory_id, Trajectory)
        if trajectory.steps:
            by_task.setdefault(task_id, []).append(trajectory)
        else:
            # Died before acting: nothing to segment, whatever the rule.
            empty.append(trajectory_id)
    for group in by_task.values():
        group.sort(key=lambda traj: traj.trajectory_id)
    empty.sort()
    return by_task, empty


def _record_empty_skips(run_dir: Path, empty_ids: Sequence[str]) -> None:
    """Append one "skipped" audit record per empty trajectory, once.

    Same channel and same terminal-within-a-run discipline as K2's
    exhaustion records: a trajectory that already has a "skipped" record is
    not recorded again, so re-running a finished stage leaves the audit
    unchanged (zero model calls either way). No model call: the exclusion
    is a fact about the stored data.
    """
    audit_path = Path(run_dir) / _AUDIT_PATH
    already = {record.get("trajectory_id")
               for record in read_jsonl(audit_path)
               if record.get("status") == "skipped"}
    for trajectory_id in empty_ids:
        if trajectory_id in already:
            continue
        append_jsonl(audit_path, {
            "trajectory_id": trajectory_id,
            "status": "skipped",
            "reason": "empty trajectory",
        })


def run(ctx: StageContext, *, failed_task_ids: Sequence[str]) -> Dict[str, Any]:
    """Segment every selected trajectory of the failed tasks via K2.

    Returns and writes ``stage2_summary.json`` with ``segmented`` /
    ``skipped`` (per-trajectory K2 outcomes, cumulative over the run),
    ``total_subtasks`` (subtasks of the segmented trajectories, stored ones
    included), ``infra_failed`` (trajectory ids lost to infrastructure
    errors; non-empty means the stage raises after writing the summary) and
    ``empty_skipped`` (sorted trajectory ids excluded before the rule because
    they have no steps; recomputed from the store on every call, so a rerun
    reproduces it).
    """
    config = ctx.config
    plugin = _plugin(config.benchmark)
    # config.rules always carries every rule key after ExperimentConfig
    # validation, and stage 0 checked the name: no local default here.
    select_trajectory = get_trajectory_select(config.rules["trajectory_select"])

    by_task, empty = _failed_trajectories_by_task(ctx.store, failed_task_ids)
    # Bookkeeping first, before any model call can interrupt it: the empty
    # exclusions are facts about the stored data and must be auditable even
    # if this call later dies on infrastructure.
    _record_empty_skips(ctx.run_dir, empty)

    selected: List[Trajectory] = []
    for task_id in sorted(by_task):
        # One rule call per task, on that task's non-empty stored set.
        selected.extend(select_trajectory(by_task[task_id]))
    # Processing order is the stage's own, not the rule's: sorted by
    # trajectory_id, identical to what a resumed run replays.
    selected.sort(key=lambda traj: traj.trajectory_id)

    recorder = UnitFailureRecorder(ctx.run_dir)
    segmented = 0
    skipped = 0
    total_subtasks = 0
    infra_failed: List[str] = []
    for trajectory in selected:
        try:
            subtasks = segment_checked(
                store=ctx.store,
                trajectory=trajectory,
                K=config.K,
                view_rules=plugin.view_rules,
                handle=ctx.handle("segmenter"),
                segment_retry_limit=config.segment_retry_limit,
                usage=ctx.usage,
                run_dir=ctx.run_dir,
            )
        except InfrastructureError as exc:
            recorder.record(trajectory.trajectory_id, _FAILURE_STAGE, exc, exc)
            infra_failed.append(trajectory.trajectory_id)
            continue
        if subtasks is None:
            skipped += 1
        else:
            segmented += 1
            total_subtasks += len(subtasks)

    result = {
        "segmented": segmented,
        "skipped": skipped,
        "total_subtasks": total_subtasks,
        "infra_failed": infra_failed,
        "empty_skipped": empty,
    }
    write_json(ctx.run_dir / _SUMMARY_NAME, result, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"stage 2: {len(infra_failed)} unit(s) (trajectories) failed on "
            f"infrastructure errors, recorded in {recorder.path}; resume the "
            f"run to retry them")
    return result
