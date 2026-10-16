"""The add path's segmentation wiring: K2 over the task's trajectories.

Wave 3-S. In the old world stage 2 cut the whole failure set's
trajectories right after stage 1, so stage 3 always found subtasks in
the store. In the phase world no execute phase owns that cut: the add
path's evidence (boundaries doc section 2.5 -- the D-phase strong-model
segments; repair plan R6 -- "the evidence module reads the D phase's
strong trajectories and their segmentation results") would find an
empty store. This driver closes that gap, and
``stages/learn_tasks._land_add`` calls it lazily, right before the
evidence collector reads the store: only an add row pays for the cut,
so an exam-only or live-only sequence never segments, and the
collector itself stays a pure reader (no model calls, no store writes).

Alignment with the old stage 2 (git c6a9fb8), deliberately:

* the candidate set is ONE task's stored NON-EMPTY trajectories of
  BOTH roles -- the per-task shape the trajectory_select rule's
  contract fixes (one call per task, both roles mixed) -- and the
  run-level store holds exactly the D phase's bare trajectories;
* 0-step trajectories are excluded BEFORE the rule (segmenting nothing
  is undefined input for G1/D7) with the same "empty trajectory" skip
  records in K2's audit channel, once per run directory;
* the named trajectory_select rule decides WHICH trajectories; the
  driver keeps the STRONG entries of the selection and segments only
  those -- the add path's material is the strong model's segments, and
  the weak half has no phase-world consumer (the revise paths read
  exam sheets and live groups, not weak segments), so it is not paid
  for. For the registered rules this changes nothing about what the
  rule sees: it is handed the same both-roles set the old stage
  handed it;
* processing order is sorted trajectory_id -- the old stage's own
  rule, kept so a resume replays the same sequence;
* K2 (composites.segment_checked) owns everything per trajectory:
  deterministic ids, per-unit persistence, resume-by-stored-subtask,
  retry rounds with D7 validation, and the terminal-within-a-run
  exhaustion audit.

Resume is K2's, unchanged: a stored subtask means the unit is done and
the segmenter is not called again for it, so re-landing an add row
after a crash re-derives the same selection and makes zero model
calls. The batch is serial on purpose: the add row is the learn phase's
unit of infra accounting, and K2's per-trajectory resume makes serial
replay exact (the old stage's ``workers`` knob was a batch-level
setting of a stage that no longer exists; per-task batches are small).

Exhaustion is reported, not raised: the caller records the add row as
having no material and moves on -- the old stage 3's silence (a task
whose trajectories were all skipped simply produced no skill) with a
receipt instead.
"""

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, Union

from expedistill.blocks.rules import get_trajectory_select
from expedistill.composites.segment_checked import AUDIT_PATH, segment_checked
from expedistill.core.ids import parse_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.store import Store, append_jsonl, read_jsonl

from expedistill.methods.evidence import SegmentationParams

__all__ = ["segment_add_material"]


def _task_trajectories(store: Store,
                       task_id: str) -> Tuple[List[Trajectory], List[str]]:
    """One task's stored non-empty trajectories, plus its 0-step ids.

    Returns ``(trajectories, empty)``: the non-empty set sorted by
    trajectory_id -- the per-task shape the trajectory_select rule is
    called with (both roles mixed, the old stage 2's contract) -- and
    the sorted ids of the 0-step trajectories (an agent that died
    before acting: nothing to segment, whatever the rule).
    """
    trajectories: List[Trajectory] = []
    empty: List[str] = []
    for trajectory_id in store.ids("trajectories"):
        try:
            parsed_task, _role, _attempt = parse_trajectory_id(trajectory_id)
        except ValueError:
            continue  # not a trajectory id shape; nothing else lands here
        if parsed_task != task_id:
            continue
        trajectory = store.load("trajectories", trajectory_id, Trajectory)
        if trajectory.steps:
            trajectories.append(trajectory)
        else:
            empty.append(trajectory_id)
    trajectories.sort(key=lambda traj: traj.trajectory_id)
    empty.sort()
    return trajectories, empty


def _record_empty_skips(run_dir: Path, empty_ids: Sequence[str]) -> None:
    """Append one "empty trajectory" skip record per 0-step trajectory,
    once (the old stage 2's bookkeeping: same channel and same
    once-only discipline as K2's exhaustion records, so a re-run leaves
    the audit unchanged and every reason a trajectory was never
    segmented lives in one file). No model call: the exclusion is a fact
    about the stored data."""
    audit_path = Path(run_dir) / AUDIT_PATH
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


def segment_add_material(
    *,
    store: Store,
    task_id: str,
    params: SegmentationParams,
    trajectory_select: str,
    view_rules: Any,
    handle: Any,
    usage: Any = None,
    run_dir: Union[str, Path, None] = None,
) -> Dict[str, int]:
    """Segment the task's selected strong trajectories via K2.

    ``params`` is the learn phase's evidence method (the registry's
    ``(evidence, segment)`` entry: K and the re-segmentation budget);
    ``trajectory_select`` is the configured named rule;
    ``view_rules`` the run's benchmark plugin's. Returns the old stage
    2 summary shape for one task: ``segmented`` / ``skipped`` count the
    selected strong trajectories K2 returned subtasks for / skipped
    (exhaustion, or an earlier call's terminal skip), ``empty_skipped``
    the 0-step exclusions. Zero model calls when every selected
    trajectory is already done (K2's own resume guard).
    """
    base = Path(run_dir) if run_dir is not None else store.run_dir
    trajectories, empty = _task_trajectories(store, task_id)
    # Bookkeeping first, before any model call can interrupt it: the
    # empty exclusions are facts about the stored data and must stay
    # auditable even if this call later dies on infrastructure.
    _record_empty_skips(base, empty)

    selected = get_trajectory_select(trajectory_select)(trajectories)
    strong = sorted((traj for traj in selected
                     if traj.model_role == "strong"),
                    key=lambda traj: traj.trajectory_id)

    segmented = 0
    skipped = 0
    for trajectory in strong:
        subtasks = segment_checked(
            store=store,
            trajectory=trajectory,
            K=params.K,
            view_rules=view_rules,
            handle=handle,
            segment_retry_limit=params.retry_limit,
            usage=usage,
            run_dir=run_dir,
        )
        if subtasks is None:
            skipped += 1
        else:
            segmented += 1
    return {"segmented": segmented, "skipped": skipped,
            "empty_skipped": len(empty)}
