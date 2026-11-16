"""The frozen delivery and learn-operation interface shapes (layer 0).

These dataclasses are the contract between the loop's modules: delivery
hands execution a list of units, and a learn phase hands its writers one
row per skill operation. Every module that needs to hand one of these
things to another works with these fields and no others; changing a field
set means reopening the contract, not adding a side channel.

The execution -> acceptance handoff is the trial-level
``core.answer_sheet.ExecutionRecord``, so this module freezes only the
delivery and operation rows.

These are INTERFACE VIEWS, not persisted objects; nothing here grows a
to_dict/from_dict. The persisted objects are defined by core/models.py.

Who produces what:

* ``ExecutionUnitRow`` -- delivery, one row per unit (one task plus the
  skills injected into it, possibly none).
* ``TaskListRow`` -- a learn phase, one row per skill operation it lands
  (the receipt and version-guard ledgers key on it).

``unit_id`` is the join key carried by ``ExecutionUnitRow``;
``TaskListRow.basis_unit`` points back at one:

    f"{phase_label}:{benchmark}:{task_id}:{role}"
    f":{sha256(canonical(skills_injected)).hexdigest()[:8]}"

which phase, which benchmark, which task, which execution role, and the
first eight hex chars of the sha256 over the canonical form of
``skills_injected``. No builder lives here (core carries no business
logic): the canonicalization rule lives in ``composites/unit_ids.py``.

This module depends on the standard library only (layer 0).
"""

from dataclasses import dataclass
from typing import Any, Dict, List

__all__ = [
    "ExecutionUnitList",
    "ExecutionUnitRow",
    "TaskListRow",
]


@dataclass
class ExecutionUnitRow:
    """One row of the execution unit list (the delivery module's output:
    one task plus the skills injected into it)."""

    #: The unit's id (the join key; construction rule in the module
    #: docstring).
    unit_id: str

    #: The task the unit runs.
    task_ref: str

    #: The injected skills' raw value: ``None`` is no injection, a string
    #: is the whole-bank reference.
    skills_injected: Any

    #: The rendered injection block, byte-identical to what execution
    #: hands the model; the empty string on a control unit.
    skills_block: str

    #: Benchmark-supplied material the unit carries through to acceptance.
    aux: Dict[str, Any]


#: What every delivery driver resolves to (R5's unified signature).
ExecutionUnitList = List[ExecutionUnitRow]


@dataclass
class TaskListRow:
    """One skill operation a learn phase lands."""

    #: What the action targets: ``"skill"`` (a revision or a delete) or
    #: ``"type"`` (the subtask type a new card is written for).
    target_kind: str

    #: The target's id (a skill id or a type id, per target_kind).
    target_id: str

    #: One of ``"add"`` (gap evidence), ``"revise_trigger"`` (trigger
    #: evidence), ``"revise_content"`` (content evidence) or ``"delete"``
    #: (the deletion rule).
    action: str

    #: Why the action was decided (carries the discard_reason vocabulary
    #: when the action is delete).
    reason: str

    #: The execution unit whose failure the evidence came from.
    basis_unit: str
