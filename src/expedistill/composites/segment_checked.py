"""K2: segment one trajectory into Subtasks, with D7 validation and retries.

Composite wiring (README 1.4): D5 renders the view -> G1 segments it -> D7
validates the result against the real trajectory. Unlike the blocks it wires,
this function owns disk IO, resume and retries (AGENTS.md block rule 3):

* RESUME: subtask ids are deterministic (``make_subtask_id``), so a stored
  subtask means the unit is done. If ANY subtask of this trajectory is in the
  store, all stored ones are loaded and returned and the model is not called.
* RETRY: up to ``segment_retry_limit`` re-segmentation attempts. Each retry
  re-renders the view (D5) and prepends a clearly-marked note carrying the
  previous errors. The SEGMENT prompt's ``{trajectory_view}`` placeholder is
  free-form text (``prompts/segment.py``), so the note rides inside the view
  variable without touching the prompt template -- the template digest is
  unaffected, which matters because prompt text is frozen per run.
* EXHAUSTION: the trajectory is skipped and one record is appended to
  ``<run_dir>/stage2_segments/segmentation_audit.jsonl`` (trajectory_id,
  attempts, last_errors, status "skipped"); the function returns None so the
  stage can move on (README 1.4 K2 row). When ``run_dir`` is None the audit
  still must not be lost, so it falls back to ``store.run_dir`` -- the same
  run directory the store already writes to.
* EXHAUSTION IS TERMINAL within a run directory: a run is one frozen
  experiment, so a trajectory that already has a "skipped" audit record is
  not re-offered on resume -- K2 returns None again without a model call and
  without a second record. Re-running a finished run therefore makes zero
  model calls; retrying with a resampled model means a new run directory.
* G1's ``ModelOutputError`` (schema/format, raised after call_model's own
  bounded repair retries) is NOT a crash: it counts as one failed attempt and
  the next round carries a generic "previous output was invalid" note instead
  of specific errors. The stage keeps going either way; only exhaustion ends
  the unit.
* INFRASTRUCTURE: an ``InfrastructureError`` from G1 (already retried at the
  call level) is NOT a failed attempt and NOT a segmentation failure: it
  propagates unchanged, with no audit record and nothing stored, so the
  stage can put the trajectory in the unit-failure ledger and a resume
  re-offers it. Every other exception propagates the same way.
* OUT-OF-ORDER SEGMENTS: D7 judges coverage after sorting by start, and a
  valid list may arrive unsorted. ``segment_index`` follows step order (sorted
  by ``start``), so ids, ``step_range`` and ``context_summary`` all agree with
  the trajectory's own ordering.
* PARTIAL SAVE: each Subtask is saved the moment it is built. A crash between
  saves leaves a prefix in the store, and resume -- per spec -- treats any
  stored subtask as "done" and returns that prefix. Accepting this is cheaper
  than a completion marker file outside the store, and the audit trail plus
  the deterministic ids make the state inspectable.

``context_summary`` is a deterministic join of the PREVIOUS segments'
"label: description" entries ("; "-joined, capped at 500 chars, empty for the
first segment). No model call: it is bookkeeping, not generation.

Layer 5; wires blocks.view (D5), blocks.segment (G1 + D7), core.ids, and the
store. No disk IO outside the store and the audit file above.
"""

from pathlib import Path
from typing import Any, List, Optional, Union

from expedistill.blocks.segment import segment, validate_segments
from expedistill.blocks.view import render_trajectory_view
from expedistill.core.ids import make_subtask_id, parse_subtask_id
from expedistill.core.models import Subtask, Trajectory
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store, append_jsonl, read_jsonl

__all__ = ["segment_checked"]

#: Relative to the run directory (README section 2 layout).
_AUDIT_PATH = Path("stage2_segments") / "segmentation_audit.jsonl"

#: Prepended to the view on retry attempts. Wording is fixed so downstream
#: log analysis can recognise it.
_RETRY_NOTE = (
    "NOTE: your previous segmentation attempt failed validation with these "
    "errors: {errors}. Re-segment the full trajectory."
)

#: Generic note for a G1 ModelOutputError: there are no D7 errors to quote,
#: and the raw format error would only invite the model to fix JSON syntax
#: call_model already retried.
_GENERIC_OUTPUT_ERROR = "previous output was not a valid segmentation response"

#: Cap for the deterministic context join.
_CONTEXT_SUMMARY_LIMIT = 500


def _stored_subtasks(store: Store, trajectory_id: str) -> List[Subtask]:
    """This trajectory's stored subtasks, in segment-index order."""
    pairs = []
    for subtask_id in store.ids("subtasks"):
        try:
            parent, index = parse_subtask_id(subtask_id)
        except ValueError:
            continue  # not a subtask id shape; other kinds never land here
        if parent == trajectory_id:
            pairs.append((index, store.load("subtasks", subtask_id, Subtask)))
    pairs.sort(key=lambda pair: pair[0])
    return [subtask for _, subtask in pairs]


def _audit_path(store: Store,
                run_dir: Optional[Union[str, Path]]) -> Path:
    # run_dir wins when given (it is the same run directory in practice);
    # store.run_dir keeps the audit from being silently dropped otherwise.
    base = Path(run_dir) if run_dir is not None else store.run_dir
    return base / _AUDIT_PATH


def _already_skipped(audit_path: Path, trajectory_id: str) -> bool:
    """True if the audit holds a terminal "skipped" record for the unit."""
    return any(
        record.get("trajectory_id") == trajectory_id
        and record.get("status") == "skipped"
        for record in read_jsonl(audit_path))


def segment_checked(
    *,
    store: Store,
    trajectory: Trajectory,
    K: int,
    view_rules: Any,
    handle: Any,
    segment_retry_limit: int,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Optional[List[Subtask]]:
    """Segment ``trajectory`` into validated Subtasks, or None if unrecoverable.

    Returns the built (or, on resume, stored) Subtasks in step order, or None
    after ``segment_retry_limit + 1`` attempts have all failed -- with one
    "skipped" audit record appended per the K2 contract -- or None at once,
    with no model call, when an earlier call already skipped it.
    """
    stored = _stored_subtasks(store, trajectory.trajectory_id)
    if stored:
        return stored
    audit_path = _audit_path(store, run_dir)
    if _already_skipped(audit_path, trajectory.trajectory_id):
        return None

    attempts_allowed = segment_retry_limit + 1
    errors: List[str] = []
    raw: Optional[List[dict]] = None
    for attempt in range(attempts_allowed):
        view = render_trajectory_view(trajectory, view_rules)
        if attempt > 0:
            view = _RETRY_NOTE.format(errors="; ".join(errors)) + "\n\n" + view
        try:
            raw = segment(view, K, handle, usage=usage, run_dir=run_dir)
        except ModelOutputError as exc:
            errors = [f"{_GENERIC_OUTPUT_ERROR} ({exc})"]
            continue
        errors = validate_segments(len(trajectory.steps), raw, K)
        if not errors:
            break
    else:
        append_jsonl(audit_path, {
            "trajectory_id": trajectory.trajectory_id,
            "attempts": attempts_allowed,
            "last_errors": errors,
            "status": "skipped",
        })
        return None

    # segment() guarantees schema-valid entries; D7 guaranteed coverage.
    # Sort by start so indexes follow step order even for shuffled output.
    raw_sorted = sorted(raw, key=lambda entry: entry["start"])
    source = "strong" if trajectory.model_role == "strong" else "weak"
    context_parts: List[str] = []
    subtasks: List[Subtask] = []
    for index, entry in enumerate(raw_sorted):
        subtask = Subtask(
            subtask_id=make_subtask_id(trajectory.trajectory_id, index),
            task_id=trajectory.task_id,
            trajectory_id=trajectory.trajectory_id,
            source=source,
            # The run the segment was cut from was a scorer-confirmed
            # success only when its verdict came back True; an unscored
            # run (correctness None) is not one.
            trajectory_success=trajectory.correctness is True,
            segment_index=index,
            step_range=(entry["start"], entry["end"]),
            label=entry["label"],
            description=entry["description"],
            success=entry["success"],
            context_summary="; ".join(context_parts)[:_CONTEXT_SUMMARY_LIMIT],
        )
        context_parts.append(f"{entry['label']}: {entry['description']}")
        store.save(subtask, "subtasks")
        subtasks.append(subtask)
    return subtasks
