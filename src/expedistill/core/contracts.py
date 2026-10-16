"""The four frozen interface shapes of the execute-learn loop (layer 0).

Decision doc: docs/decisions/execute_learn_repair_plan_20261016.md, item
R0 (freeze the interface shapes before any module work starts). The four
dataclasses here are the contract between the loop's seven modules --
delivery, execution, acceptance, decision, evidence, authoring, curation
(docs/decisions/execute_learn_module_boundaries_20261016.md). Every module
that needs to hand one of these things to another works with these fields
and no others; changing a field set means reopening the contract, not
adding a side channel.

These are INTERFACE VIEWS, not new persisted objects. The run record is
the interface view of a Trajectory (an Attempt in an exam phase, with the
answer as the artifact); nothing here grows a to_dict/from_dict. The five
persisted objects stay defined -- and persisted -- by core/models.py.

Who produces what:

* ``ExecutionUnitRow`` -- delivery, one row per unit (one task plus the
  skills injected into it, possibly none).
* ``RunRecord`` -- execution, one per execution of a unit.
* ``AcceptanceReportRow`` -- acceptance, one row per unit.
* ``TaskListRow`` -- decision, one row per decided operation.

``unit_id`` is the join key of the first three shapes:

    f"{phase_label}:{benchmark}:{task_or_exam_id}:{role}"
    f":{sha256(canonical(skills_injected)).hexdigest()[:8]}"

which phase, which benchmark, which task or exam, which execution role,
and the first eight hex chars of the sha256 over the canonical form of
``skills_injected``. No builder lives here (core carries no business
logic): the canonicalization rule and the mapping onto today's three skip
keys are frozen with the acceptance module (R1).

This module depends on the standard library only (layer 0). The ``cause``
vocabulary is deliberately NOT defined here -- R0 places it next to the
discard_reason vocabulary in composites.decision; only its existence as a
fixed value set is frozen here.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

__all__ = [
    "AcceptanceReportRow",
    "ExecutionUnitList",
    "ExecutionUnitRow",
    "RunRecord",
    "TaskListRow",
]


@dataclass
class RunRecord:
    """One execution of one unit (the execution module's output).

    The interface view of a Trajectory -- or of an Attempt in an exam
    phase, where the answer sheet is the artifact.
    """

    #: The executed unit's id (the join key; construction rule in the
    #: module docstring).
    unit_id: str

    #: The task or exam the unit runs (a train-task id or an exam id).
    task_ref: str

    #: The injected skills' raw value: ``None`` is no injection, ``[]`` is
    #: a retrieval that reached no threshold, a string is the whole-bank
    #: digest, a list is the per-skill or whole-group skill references.
    skills_injected: Any

    #: The rendered injection block, byte-identical to what is written
    #: into the Trajectory; the empty string on a control unit.
    skills_block: str

    #: Which model role executed the unit.
    model_role: str

    #: Which model executed the unit.
    model_name: str

    #: The sample index (which sampling of the same unit this is).
    sample_index: int

    #: The redispatch index (how many times the unit was re-dispatched
    #: after an invalid run; 0 on the first dispatch).
    dispatch_index: int

    #: The benchmark's own scorer verdict. ``None`` ONLY means the
    #: benchmark has no scorer; True/False is the scorer's answer, never
    #: an acceptance conclusion (that is acceptance's job).
    correctness: Optional[bool]

    #: Run metrics: retry counts, each raw error text, durations, exit
    #: statuses.
    metrics: Dict[str, Any]

    #: Benchmark-supplied material (an exam's reference answer travels
    #: here, for acceptance's judge).
    aux: Dict[str, Any]

    #: Where the trajectory and the other artifacts live.
    artifacts: Dict[str, Any]


@dataclass
class AcceptanceReportRow:
    """One unit's row of the acceptance report (the acceptance module's
    output; one report, one row per unit -- D phases split by role)."""

    #: The unit this row judges (the join key shared with the execution
    #: unit list).
    unit_id: str

    #: The phase this row belongs to.
    phase_label: str

    #: The execution role this row judges.
    role: str

    #: The skills injected into this unit (same raw value as the unit's
    #: ``skills_injected``).
    injected_skills: Any

    #: Per-run results: the run key, the verdict (success / failure /
    #: invalid) and its basis (the named rule plus explanation text).
    runs: List[Dict[str, Any]]

    #: How many runs were valid.
    valid_count: int

    #: How many of the valid runs succeeded.
    success_count: int

    #: One of ``"normal"``, ``"not_testable"``, ``"not_run_control_perfect"``.
    #: A normal unit's score is its counts alone.
    status: str

    #: Why ``status`` is ``"not_testable"`` or ``"not_run_control_perfect"``;
    #: a value from the fixed set R0 places in composites.decision (the
    #: empty string on a normal row).
    cause: str

    #: Where the trajectories and the other artifacts live.
    artifacts: Dict[str, Any]


@dataclass
class ExecutionUnitRow:
    """One row of the execution unit list (the delivery module's output:
    one task plus the skills injected into it)."""

    #: The unit's id (the join key; construction rule in the module
    #: docstring).
    unit_id: str

    #: The task or exam the unit runs.
    task_ref: str

    #: The skills to inject (raw value; see RunRecord.skills_injected).
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
