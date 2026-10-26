"""The frozen delivery/decision interface shapes (layer 0).

Decision doc: docs/decisions/execute_learn_repair_plan_20261016.md, item
R0 (freeze the interface shapes before any module work starts). These
dataclasses are the contract between the loop's seven modules -- delivery,
execution, acceptance, decision, evidence, authoring, curation
(docs/decisions/execute_learn_module_boundaries_20261016.md). Every module
that needs to hand one of these things to another works with these fields
and no others; changing a field set means reopening the contract, not
adding a side channel.

The execution -> acceptance handoff is the trial-level
``core.answer_sheet.ExecutionRecord`` (the answer-sheet refactor
superseded the old per-run view), so this module freezes only the
delivery and decision rows.

These are INTERFACE VIEWS, not new persisted objects; nothing here grows
a to_dict/from_dict. The five persisted objects stay defined -- and
persisted -- by core/models.py.

Who produces what:

* ``ExecutionUnitRow`` -- delivery, one row per unit (one task plus the
  skills injected into it, possibly none).
* ``TaskListRow`` -- decision, one row per decided operation.

``unit_id`` is the join key carried by ``ExecutionUnitRow``;
``TaskListRow.basis_unit`` points back at one:

    f"{phase_label}:{benchmark}:{task_or_exam_id}:{role}"
    f":{sha256(canonical(skills_injected)).hexdigest()[:8]}"

which phase, which benchmark, which task or exam, which execution role,
and the first eight hex chars of the sha256 over the canonical form of
``skills_injected``. No builder lives here (core carries no business
logic): the canonicalization rule lives in ``composites/unit_ids.py``.

This module depends on the standard library only (layer 0). The ``cause``
vocabulary is deliberately NOT defined here -- R0 places it next to the
discard_reason vocabulary in composites.decision; only its existence as a
fixed value set is frozen here.
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

    #: The task or exam the unit runs.
    task_ref: str

    #: The injected skills' raw value: ``None`` is no injection, ``[]`` is
    #: a retrieval that reached no threshold, a string is the whole-bank
    #: digest, a list is the per-skill or whole-group skill references.
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
    """One decided operation (the decision module's output)."""

    #: What the action targets: ``"skill"``, ``"group"`` or ``"task"``.
    target_kind: str

    #: The target's id (a skill id, a task id, ... per target_kind).
    target_id: str

    #: One of ``"keep"``, ``"revise"``, ``"delete"``, ``"add"``.
    action: str

    #: Why the action was decided (carries the discard_reason vocabulary
    #: when the action is delete).
    reason: str

    #: The unit whose acceptance the decision is based on.
    basis_unit: str
