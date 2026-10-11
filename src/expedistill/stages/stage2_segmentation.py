"""Stage 2: segment the failure set's trajectories (README 3, stage 2).

Wiring only (layer 6): the stage finds the stored stage-1 trajectories of
every task in ``F`` (both roles), applies the frozen trajectory_select rule,
and hands each surviving trajectory to K2, which owns per-trajectory
persistence and resume -- a stored subtask means the unit is done and the
segmenter model is not called again for it, so rerunning this stage on a
populated store issues zero model calls.

Trajectories are processed in sorted trajectory_id order (core.ids: serial-
based IDs stay reproducible only under a fixed creation order; here nothing
is serial-numbered, but the fixed order keeps the run's audit log and the
model-call sequence deterministic across resumes).

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
from typing import Any, Dict, List, Sequence

from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks.rules import get_trajectory_select
from expedistill.composites.segment_checked import segment_checked
from expedistill.core.ids import parse_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import InfrastructureError, UnitFailureRecorder
from expedistill.infra.store import write_json
from expedistill.stages.context import StageContext

__all__ = ["run"]

_SUMMARY_NAME = "stage2_summary.json"

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


def _failed_trajectories(store: Any,
                         failed_task_ids: Sequence[str]) -> List[Trajectory]:
    """Stored trajectories of the failed tasks, both roles, sorted by id."""
    failed = set(failed_task_ids)
    trajectories: List[Trajectory] = []
    for trajectory_id in store.ids("trajectories"):
        try:
            task_id, _role, _attempt = parse_trajectory_id(trajectory_id)
        except ValueError:
            continue  # not a trajectory id shape; nothing else lands here
        if task_id in failed:
            trajectories.append(
                store.load("trajectories", trajectory_id, Trajectory))
    trajectories.sort(key=lambda traj: traj.trajectory_id)
    return trajectories


def run(ctx: StageContext, *, failed_task_ids: Sequence[str]) -> Dict[str, Any]:
    """Segment every selected trajectory of the failed tasks via K2.

    Returns and writes ``stage2_summary.json`` with ``segmented`` /
    ``skipped`` (per-trajectory K2 outcomes, cumulative over the run),
    ``total_subtasks`` (subtasks of the segmented trajectories, stored ones
    included) and ``infra_failed`` (trajectory ids lost to infrastructure
    errors; non-empty means the stage raises after writing the summary).
    """
    config = ctx.config
    plugin = _plugin(config.benchmark)
    # config.rules always carries every rule key after ExperimentConfig
    # validation, and stage 0 checked the name: no local default here.
    select_trajectory = get_trajectory_select(config.rules["trajectory_select"])

    selected = [
        trajectory
        for trajectory in _failed_trajectories(ctx.store, failed_task_ids)
        if select_trajectory(trajectory)
    ]

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
    }
    write_json(ctx.run_dir / _SUMMARY_NAME, result, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"stage 2: {len(infra_failed)} unit(s) (trajectories) failed on "
            f"infrastructure errors, recorded in {recorder.path}; resume the "
            f"run to retry them")
    return result
