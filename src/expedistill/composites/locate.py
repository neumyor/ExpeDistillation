"""K3: locate failures in the run's subtask type table (layer 5).

Two parts, run by the learn phase in different parallel groups:

* :func:`segment_one` -- K2 over one trajectory (a failed weak trajectory
  of the input phase, or a strong baseline trajectory the first learn phase
  cuts for card material), cut under the learn phase's label. A 0-step
  trajectory is never offered to K2: it gets one "empty trajectory" skip
  record in K2's audit channel. :func:`strong_trajectories` names the
  strong baseline trajectories the ``trajectory_select`` rule keeps.
* :func:`type_pending` -- G2 over every segment this learn phase cut that
  has no type yet, in subtask-id order, in chunks of ``typing_chunk_size``;
  every chunk carries the whole table, including the types the earlier
  chunks added. The table and the assignments are append-only run-level
  ledgers:

      stage2_segments/subtask_types.jsonl   one SubtaskType per row
      stage2_segments/segment_types.jsonl   {subtask_id, type_id, phase_label}

A chunk's new types are appended before its assignments. A crash between
the two leaves types no segment uses; the next call drops them before it
types anything, so the resumed table and every later prompt equal those of
an uninterrupted run. A chunk whose reply stays unusable after its repairs
is split in half and each half typed again, down to single segments, so one
bad reply loses at most one segment. A single segment that still fails is
recorded in ``failed_units.jsonl``, once per call; it is given up once it
has failed in ``runs`` calls, and until then a later call types it again.
An infrastructure failure is recorded too and raised, and a resume types
the chunk again.
"""

from collections import Counter
from pathlib import Path
from typing import (
    Any, Dict, List, Mapping, Optional, Sequence, Set, Union)

from expedistill import prompts
from expedistill.blocks.rules import get_trajectory_select
from expedistill.blocks.subtask_types import (
    TypingResult,
    segment_card,
    type_segments,
)
from expedistill.blocks.view import render_steps_view
from expedistill.composites.segment_checked import AUDIT_PATH, segment_checked
from expedistill.core.ids import parse_trajectory_id
from expedistill.core.models import Subtask, SubtaskType, Trajectory
from expedistill.infra.reliability import (
    InfrastructureError, ModelOutputError, UnitFailureRecorder)
from expedistill.infra.store import (
    Store, append_jsonl, read_jsonl, write_jsonl)

__all__ = [
    "SEGMENT_TYPES_PATH",
    "SUBTASK_TYPES_PATH",
    "TYPING_REPAIRS",
    "load_assignments",
    "load_type_table",
    "model_failure_counts",
    "segment_one",
    "strong_trajectories",
    "type_pending",
]

SUBTASK_TYPES_PATH = Path("stage2_segments") / "subtask_types.jsonl"
SEGMENT_TYPES_PATH = Path("stage2_segments") / "segment_types.jsonl"

#: Business repairs of a typing reply the checks reject, on top of
#: call_model's own JSON-format budget.
TYPING_REPAIRS = 2


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

def _record_empty_skip(run_dir: Path, trajectory_id: str) -> None:
    """One "empty trajectory" skip record per 0-step trajectory, once."""
    audit_path = Path(run_dir) / AUDIT_PATH
    if any(record.get("trajectory_id") == trajectory_id
           and record.get("status") == "skipped"
           for record in read_jsonl(audit_path)):
        return
    append_jsonl(audit_path, {"trajectory_id": trajectory_id,
                              "status": "skipped",
                              "reason": "empty trajectory"})


def segment_one(*, store: Store, trajectory: Trajectory, phase_label: str,
                K: int, retry_limit: int, view_rules: Any, handle: Any,
                usage: Any = None,
                run_dir: Union[str, Path, None] = None,
                give_up: bool = True) -> Optional[List[Subtask]]:
    """The trajectory's segments (stored ones on resume), or None when it
    has no step or K2 gave it up (both are terminal audit records). With
    ``give_up=False`` a failed segmentation raises ModelOutputError and
    stays retryable (``segment_checked``)."""
    if not trajectory.steps:
        _record_empty_skip(Path(run_dir) if run_dir is not None
                           else store.run_dir, trajectory.trajectory_id)
        return None
    return segment_checked(
        store=store, trajectory=trajectory, phase_label=phase_label, K=K,
        view_rules=view_rules, handle=handle,
        segment_retry_limit=retry_limit, usage=usage, run_dir=run_dir,
        give_up=give_up)


def strong_trajectories(store: Store, baseline_label: str,
                        rule_name: str) -> List[Trajectory]:
    """The strong trajectories of the baseline phase ``baseline_label``
    that the named ``trajectory_select`` rule keeps, task by task (the rule
    sees each task's non-empty baseline trajectories of both roles), in
    trajectory-id order."""
    by_task: Dict[str, List[Trajectory]] = {}
    for trajectory_id in store.ids("trajectories"):
        parts = parse_trajectory_id(trajectory_id)
        if parts.phase_label != baseline_label:
            continue
        trajectory = store.load("trajectories", trajectory_id, Trajectory)
        if trajectory.steps:
            by_task.setdefault(parts.task_id, []).append(trajectory)
    rule = get_trajectory_select(rule_name)
    selected = [trajectory for task_id in sorted(by_task)
                for trajectory in rule(sorted(
                    by_task[task_id],
                    key=lambda item: item.trajectory_id))
                if trajectory.model_role == "strong"]
    return sorted(selected, key=lambda item: item.trajectory_id)


# ---------------------------------------------------------------------------
# The type table
# ---------------------------------------------------------------------------

def load_type_table(run_dir: Union[str, Path]) -> List[SubtaskType]:
    """The run's subtask type table, in table order."""
    return [SubtaskType.from_dict(row)
            for row in read_jsonl(Path(run_dir) / SUBTASK_TYPES_PATH)]


def load_assignments(run_dir: Union[str, Path]) -> Dict[str, str]:
    """Every typed segment's type id."""
    return {row["subtask_id"]: row["type_id"]
            for row in read_jsonl(Path(run_dir) / SEGMENT_TYPES_PATH)}


def _drop_unused_types(run_dir: Path, phase_label: str) -> None:
    """Remove this phase's types no segment uses (a crash between a chunk's
    two appends); earlier phases' rows are never touched."""
    table = load_type_table(run_dir)
    used = set(load_assignments(run_dir).values())
    kept = [row for row in table
            if row.phase_label != phase_label or row.type_id in used]
    if len(kept) != len(table):
        write_jsonl(run_dir / SUBTASK_TYPES_PATH,
                    [row.to_dict() for row in kept])


def model_failure_counts(run_dir: Union[str, Path],
                         phase_label: str) -> Counter:
    """How many times each unit's model output failed in this phase (one
    ledger row per failed call of the unit)."""
    return Counter(row["unit_id"]
                   for row in UnitFailureRecorder(run_dir).list_failures()
                   if row.get("stage") == phase_label
                   and row.get("error_class") == ModelOutputError.__name__)


def _type_chunk(cards: Mapping[str, str], table: Sequence[SubtaskType], *,
                phase_label: str, handle: Any, usage: Any,
                run_dir: Path) -> TypingResult:
    note = ""
    problem = "gave no usable reply"
    for _ in range(TYPING_REPAIRS + 1):
        try:
            return type_segments(cards, table, phase_label=phase_label,
                                 handle=handle, correction_note=note,
                                 usage=usage, run_dir=run_dir)
        except ModelOutputError as exc:
            problem = str(exc)
            note = prompts.get("CARDS_CORRECTION").format(problem=problem)
    raise ModelOutputError(
        f"SUBTASK_TYPING: no usable reply after {TYPING_REPAIRS} repairs: "
        f"{problem}")


def type_pending(*, store: Store, run_dir: Union[str, Path],
                 phase_label: str, chunk_size: int, max_chars: int,
                 view_rules: Any, handle: Any, usage: Any = None,
                 runs: int = 1) -> Dict[str, Any]:
    """Type every segment this phase cut that has no type yet; returns
    ``typed`` (segments typed by this call), ``new_types`` (table rows
    this call added), ``given_up`` (segments untyped for good: failed in
    ``runs`` calls, this one included) and ``retry`` (the ids of the
    segments that failed in this call and are typed again by the next)."""
    run_dir = Path(run_dir)
    _drop_unused_types(run_dir, phase_label)
    assigned = load_assignments(run_dir)
    ours = [subtask for subtask in (
        store.load("subtasks", subtask_id, Subtask)
        for subtask_id in store.ids("subtasks")
        if subtask_id not in assigned)
        if subtask.phase_label == phase_label]
    failures = model_failure_counts(run_dir, phase_label)
    given_up = {subtask.subtask_id for subtask in ours
                if failures[subtask.subtask_id] >= runs}
    pending = [subtask for subtask in ours
               if subtask.subtask_id not in given_up]
    retry: Set[str] = set()
    recorder = UnitFailureRecorder(run_dir)
    trajectories: Dict[str, Trajectory] = {}

    def card(subtask: Subtask) -> str:
        if subtask.trajectory_id not in trajectories:
            trajectories[subtask.trajectory_id] = store.load(
                "trajectories", subtask.trajectory_id, Trajectory)
        steps = trajectories[subtask.trajectory_id].steps
        start, end = subtask.step_range
        return segment_card(
            subtask, render_steps_view(steps[start:end], view_rules,
                                       start_index=start), max_chars)

    counts = {"typed": 0, "new_types": 0}

    def type_chunk(chunk: List[Subtask]) -> None:
        cards = {subtask.subtask_id: card(subtask) for subtask in chunk}
        try:
            result = _type_chunk(cards, load_type_table(run_dir),
                                 phase_label=phase_label, handle=handle,
                                 usage=usage, run_dir=run_dir)
        except ModelOutputError as exc:
            if len(chunk) > 1:
                middle = len(chunk) // 2
                type_chunk(chunk[:middle])
                type_chunk(chunk[middle:])
                return
            subtask_id = chunk[0].subtask_id
            recorder.record(subtask_id, phase_label, exc, exc)
            if failures[subtask_id] + 1 >= runs:
                given_up.add(subtask_id)
            else:
                retry.add(subtask_id)
            return
        except InfrastructureError as exc:
            recorder.record(chunk[0].subtask_id, phase_label, exc, exc)
            raise
        for row in result.new_types:
            append_jsonl(run_dir / SUBTASK_TYPES_PATH, row.to_dict())
        for subtask_id in sorted(result.assignments):
            append_jsonl(run_dir / SEGMENT_TYPES_PATH, {
                "subtask_id": subtask_id,
                "type_id": result.assignments[subtask_id],
                "phase_label": phase_label,
            })
        counts["typed"] += len(result.assignments)
        counts["new_types"] += len(result.new_types)

    size = max(1, int(chunk_size))
    for offset in range(0, len(pending), size):
        type_chunk(pending[offset:offset + size])
    return {"typed": counts["typed"], "new_types": counts["new_types"],
            "given_up": len(given_up), "retry": sorted(retry)}
