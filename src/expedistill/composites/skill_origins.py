"""Per-phase skill-origin ledger: where each skill version came from.

Decision doc: ``docs/decisions/generalization_exam_20261027.md`` (item 8).
A learn phase that writes or revises a skill records one row per skill
version in its own ``skill_origins.jsonl``:

    <run_dir>/phases/<phase_label>/skill_origins.jsonl

    {"skill_id": ..., "version": ..., "phase_label": ...,
     "change_note": ..., "context_text": ..., "source_ref": {...}}

``change_note`` is the natural-language reason the skill was written or
revised and ``context_text`` the evidence the writer saw; ``source_ref``
is the pointer produced by :func:`source_ref` (execution phase, unit,
sheet, scene step). The exam generator reads the ledger back through
:func:`find_origin` to rebuild the scene the skill was written for.

Input / output
--------------
* :func:`append_origin` -- input: a phase directory and one origin record
  (explicit keyword arguments). Output: ``None``; one canonical JSON line
  appended (the first row for a ``(skill_id, version)`` wins, later calls
  are no-ops).
* :func:`find_origin` -- input: a run directory, a skill id and a
  version. Output: the first matching row dict, scanning phase directories
  from the highest ``pNN`` down (the newest learning phase first), or
  ``None`` when no phase recorded the version.
* :func:`source_ref` -- input: a phase label plus optional unit/sheet ids
  and a scene step. Output: the pointer dict ``Exam.source_ref`` carries,
  with absent (``None``) optional keys omitted.

Writes go through ``infra.store.append_jsonl`` (atomic-ish append, torn
tail repaired), so a crash loses at most the line being written and a
resumed phase appends on top of what it already has.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

from expedistill.composites.unit_ids import phase_label_of_unit_id
from expedistill.infra.store import append_jsonl, read_jsonl

__all__ = [
    "PHASES_DIR",
    "SKILL_ORIGINS_NAME",
    "append_origin",
    "find_origin",
    "source_ref",
    "source_ref_for_unit",
]

#: The run-level phase tree (same layout as ``composites.phase_bank``).
PHASES_DIR = "phases"

#: The ledger's file name inside each phase directory.
SKILL_ORIGINS_NAME = "skill_origins.jsonl"

#: A phase directory is ``pNN_<name>`` (the label's colon mapped to "_");
#: only the number is needed to order phases newest-first.
_PHASE_DIR_NUMBER_RE = re.compile(r"^p([0-9]+)_")


def _phase_dir_number(path: Path) -> int:
    """The ``NN`` of a phase directory; -1 for a name outside ``pNN_*``
    (sorted last, never a crash)."""
    match = _PHASE_DIR_NUMBER_RE.match(path.parent.name)
    return int(match.group(1)) if match else -1


def append_origin(phase_dir: Union[str, Path], *, skill_id: str,
                  version: int, phase_label: str, change_note: str,
                  context_text: str,
                  source_ref: Dict[str, Any]) -> None:
    """Append one skill-origin row to a phase's ledger.

    Idempotent per ``(skill_id, version)``: the first row recorded for a
    version is the one kept, so a resumed or re-run phase that walks its
    units again does not grow duplicate rows (and the first write is the
    evidence of why the version was born). The directory is created when
    missing (``append_jsonl`` also creates parents, spelled out here so
    the contract is visible).
    """
    phase_dir = Path(phase_dir)
    path = phase_dir / SKILL_ORIGINS_NAME
    for row in read_jsonl(path):
        if row.get("skill_id") == skill_id and row.get("version") == version:
            return
    phase_dir.mkdir(parents=True, exist_ok=True)
    append_jsonl(path, {
        "skill_id": skill_id,
        "version": version,
        "phase_label": phase_label,
        "change_note": change_note,
        "context_text": context_text,
        "source_ref": source_ref,
    })


def find_origin(run_dir: Union[str, Path], skill_id: str,
                version: int) -> Optional[Dict[str, Any]]:
    """The first recorded origin of ``(skill_id, version)``, newest phase
    first; ``None`` when no phase recorded it.

    Phase directories are ordered by their ``NN`` descending, so a skill
    version rewritten in a later learn phase resolves to the phase that
    most recently wrote it (the ledger keeps one row per version anyway;
    the order matters when a stale row and a fresh one disagree). A
    directory name that is not ``pNN_*`` sorts last. The ``phases`` tree
    not existing is "nothing recorded", not an error.
    """
    run_dir = Path(run_dir)
    paths = sorted(
        (run_dir / PHASES_DIR).glob(f"*/{SKILL_ORIGINS_NAME}"),
        key=_phase_dir_number, reverse=True)
    for path in paths:
        for row in read_jsonl(path):
            if (row.get("skill_id") == skill_id
                    and row.get("version") == version):
                return row
    return None


def source_ref(phase_label: str, *, unit_id: Optional[str] = None,
               sheet_id: Optional[str] = None,
               scene_step: int = 0) -> Dict[str, Any]:
    """The origin pointer an ``Exam.source_ref`` carries.

    ``phase_label`` names the execution phase whose evidence the scene
    came from; ``unit_id`` / ``sheet_id`` narrow it to one unit or answer
    sheet. Keys whose value is ``None`` are omitted (the pointer names
    only what it actually points at). ``scene_step`` is the 0-based step
    index the scene was cut at and must be a non-negative int -- a bool
    is not a step number, and a negative index names no step at all.
    """
    if isinstance(scene_step, bool) or not isinstance(scene_step, int) \
            or scene_step < 0:
        raise ValueError(
            f"source_ref scene_step must be a non-negative int, got "
            f"{scene_step!r}")
    ref: Dict[str, Any] = {"phase_label": phase_label}
    if unit_id is not None:
        ref["unit_id"] = unit_id
    if sheet_id is not None:
        ref["sheet_id"] = sheet_id
    ref["scene_step"] = scene_step
    return ref


def source_ref_for_unit(unit_id: str, *, sheet_id: Optional[str] = None,
                        scene_step: int = 0) -> Dict[str, Any]:
    """The origin pointer for an execute-phase unit id.

    The phase label is parsed from the unit id, so callers that hold a
    ``UnitSummary`` or a ``TaskListRow.basis_unit`` do not need to thread
    the phase label separately.
    """
    return source_ref(
        phase_label_of_unit_id(unit_id), unit_id=unit_id,
        sheet_id=sheet_id, scene_step=scene_step)
