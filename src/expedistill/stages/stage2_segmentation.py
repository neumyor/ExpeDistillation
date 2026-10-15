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

Dispatch (B5 run_units): the selected trajectories are processed as units
through ``run_units`` with the run's ``workers`` setting (the same lookup
stages 1/6 use; default 1 is the serial path, i.e. the historical
behavior). Everything before dispatch -- loading, grouping, the per-task
rule calls, the empty-skip exclusion and its audit records -- stays serial
and deterministic, and the summary is aggregated from the returned
outcomes in input order, so it is identical for any ``workers``. Only the
order of concurrent appends varies (K2's exhaustion audit records, ledger
lines): append_jsonl serializes the writers themselves, and readers must
not depend on that order.

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
segmented; after the batch the summary is written (``infra_failed`` lists
the ids) and the stage raises ``InfrastructureError`` so the pipeline never
advances past a stage with holes. A resume re-offers exactly those
trajectories. Every other exception is fatal for the batch (run_units
semantics): units in flight run to completion, no further unit starts, and
the first failure by unit order is re-raised unwrapped with its original
type and message, whatever its kind (a ParallelError carrying every
failure stays chained as its __cause__) -- so a run that died on a fatal
error writes no summary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Sequence, Tuple

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.blocks.rules import get_trajectory_select
from expedistill.composites.segment_checked import segment_checked
from expedistill.core.ids import parse_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    CodeBugError, InfrastructureError, UnitFailureRecorder,
)
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


class _UnitOutcome(NamedTuple):
    """What one selected trajectory's unit produced.

    ``kind`` is the unit's disposition: "segmented" (``subtasks`` built or
    reloaded from the store), "skipped" (K2 exhausted, or an earlier call
    already skipped it) or "infra" (recorded in the unit-failure ledger by
    the worker). The stage aggregates the outcomes in input order, so the
    summary does not depend on the dispatch interleaving.
    """

    trajectory_id: str
    kind: str
    subtasks: int


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

    The selected trajectories are dispatched as units through run_units with
    the run's ``workers`` setting (default 1 = serial); the summary is
    aggregated from the returned outcomes in input order, so it is identical
    whatever the dispatch interleaving was.

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
    plugin = resolve_plugin(config.benchmark)
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

    def segment_unit(trajectory: Trajectory) -> _UnitOutcome:
        # One selected trajectory: the K2 call plus its expected-failure
        # disposition. Expected failures are handled HERE, not outside the
        # batch; only a fatal error escapes the worker (run_units contract).
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
            recorder.record(trajectory.trajectory_id, _FAILURE_STAGE,
                            exc, exc)
            return _UnitOutcome(trajectory.trajectory_id, "infra", 0)
        if subtasks is None:
            return _UnitOutcome(trajectory.trajectory_id, "skipped", 0)
        return _UnitOutcome(trajectory.trajectory_id, "segmented",
                            len(subtasks))

    # run_units returns only when no unit failed fatally (a fatal error
    # raises instead), so every outcome below is a real _UnitOutcome.
    outcomes = run_units(segment_unit, selected, workers=ctx.run_settings.workers)

    segmented = 0
    skipped = 0
    total_subtasks = 0
    infra_failed: List[str] = []
    for outcome in outcomes:
        if outcome.kind == "segmented":
            segmented += 1
            total_subtasks += outcome.subtasks
        elif outcome.kind == "skipped":
            skipped += 1
        elif outcome.kind == "infra":
            infra_failed.append(outcome.trajectory_id)
        else:  # segment_unit only ever returns the three kinds above
            raise CodeBugError(
                f"unknown per-unit segmentation outcome {outcome!r}")

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
