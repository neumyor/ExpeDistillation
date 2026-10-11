"""The five persisted objects of the data model (README section 2).

Every object is a plain dataclass with ``to_dict`` / ``from_dict`` so it can be
written to and read back from the per-run JSON layout. Two directions, two
policies:

* ``to_dict`` is strict: it always emits every declared field, including
  ``schema_version``. What lands on disk is a complete, self-describing record.
* ``from_dict`` is lenient about *unknown* keys: records written by a newer
  schema version (extra fields) still load, dropping only the extras. Unknown
  keys are ignored so a schema bump never bricks older runs outright;
  per AGENTS.md section "修改数据对象", missing or wrong-typed *known* fields
  still fail loudly instead of silently becoming defaults.

IDs are not generated here: they follow the deterministic rules in
``expedistill.core.ids`` and are assigned by the caller. Objects reference each
other by ID only, never by holding a reference.

Deliberately no ``from __future__ import annotations`` (same reason as the
reference schema.py): it would turn annotations into strings for no gain on
3.9 and hides real types from any introspection-based tooling.

This module depends on the standard library only (layer 0).
"""

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple

# Per-object schema versions. Bump the affected object's constant whenever a
# field is added or its meaning changes (README section 2 is the contract).
TRAJECTORY_SCHEMA_VERSION = 1
SUBTASK_SCHEMA_VERSION = 1
SKILL_SCHEMA_VERSION = 1
EXAM_SCHEMA_VERSION = 1
ATTEMPT_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Vocabularies (README section 2; keep in sync)
# ---------------------------------------------------------------------------

#: Which executor produced a trajectory / which model a segment came from.
MODEL_ROLES = ("strong", "weak")

#: Outcome of one execution. ``infra_error`` is not a model failure: sandbox
#: timeouts, network errors and the like must not count as "the model failed".
TRAJECTORY_STATUSES = ("success", "failure", "infra_error")

#: Lifecycle of a skill. ``merged`` means its content lives on in another skill
#: (``merged_from`` on the survivor); the original text stays on disk.
SKILL_STATUSES = ("pending", "accepted", "discarded", "merged")

#: Why an attempt was made. ``bare`` is the no-skill control, ``acceptance``
#: is the stage-4 loop, ``regression`` re-checks merged skills in stage 5.
ATTEMPT_PURPOSES = ("bare", "acceptance", "regression")


def _check_vocab(value: str, allowed: Tuple[str, ...], owner: str,
                 field_name: str) -> None:
    if value not in allowed:
        raise ValueError(
            f'{owner}.{field_name} is {value!r}, expected one of {allowed}')


def _to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses and tuples into JSON-safe containers."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value


def _known_kwargs(cls, data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unknown keys (lenient read); missing keys surface as TypeError."""
    if not isinstance(data, dict):
        raise TypeError(
            f'{cls.__name__}.from_dict expects a mapping, got {type(data).__name__}')
    known = {f.name for f in fields(cls)}
    return {key: value for key, value in data.items() if key in known}


class _Record:
    """Shared strict serializer: every declared field, recursively JSON-safe."""

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: _to_jsonable(getattr(self, f.name)) for f in fields(self)}


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------

@dataclass
class Step(_Record):
    """One interaction step of a trajectory: what the model did, what it saw."""

    action: str
    observation: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Step":
        return cls(**_known_kwargs(cls, data))


@dataclass
class Trajectory(_Record):
    """One execution of one task by one model (E1 output).

    ``success`` is derived: it is true if and only if ``status == "success"``.
    Callers never set it; assigning it directly is always overwritten in
    ``__post_init__`` so a stored record cannot disagree with its status.
    """

    trajectory_id: str
    task_id: str
    benchmark: str
    model_role: str
    model_name: str
    attempt_index: int
    #: None = bare run; otherwise a list of {skill_id, version} entries, or the
    #: digest of the whole skill bank for stage-6 bank-injected runs.
    skills_injected: Optional[Any] = None
    steps: List[Step] = field(default_factory=list)
    status: str = "failure"
    success: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = TRAJECTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_vocab(self.model_role, MODEL_ROLES, "Trajectory", "model_role")
        _check_vocab(self.status, TRAJECTORY_STATUSES, "Trajectory", "status")
        self.steps = [Step(**step) if isinstance(step, dict) else step
                      for step in self.steps]
        self.success = self.status == "success"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trajectory":
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Subtask
# ---------------------------------------------------------------------------

@dataclass
class Subtask(_Record):
    """One semantically coherent segment of a trajectory (stage 2 output).

    The step texts are NOT copied here: they live only on the Trajectory and
    are fetched via ``step_range`` (single source of truth). Two success fields
    on purpose: ``success`` is about the segment's own goal, ``trajectory_success``
    about the run the segment was cut from.
    """

    subtask_id: str
    task_id: str
    trajectory_id: str
    #: "strong" segment / "weak" segment.
    source: str
    trajectory_success: bool
    segment_index: int
    #: [start, end) step indices into the source Trajectory.
    step_range: Tuple[int, int]
    label: str
    description: str
    success: bool
    context_summary: str = ""
    schema_version: int = SUBTASK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_vocab(self.source, MODEL_ROLES, "Subtask", "source")
        range_ = tuple(self.step_range)
        if len(range_) != 2:
            raise ValueError(
                f'Subtask.step_range must be [start, end), got {self.step_range!r}')
        self.step_range = (int(range_[0]), int(range_[1]))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Subtask":
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

@dataclass
class Skill(_Record):
    """A natural-language skill attached to one Subtask (G2 output).

    Rewrites (G6) do not create a new object: they bump ``version`` and append
    to ``rewrite_history``. ``merged_from`` is only set on curation products
    (G8), whose IDs come from ``ids.make_merged_skill_id``.
    """

    skill_id: str
    content: str
    subtask_id: str
    version: int = 1
    status: str = "pending"
    #: e.g. "weak_already_knows" / "retry_exhausted" / "leak_audit"; free-form
    #: so new discard reasons do not need a schema bump.
    discard_reason: Optional[str] = None
    #: Capability group label, written in stage 5.
    group: Optional[str] = None
    #: One entry per rewrite: the old content, the error category and the
    #: rewrite reason (the error categorisation is itself data about what the
    #: weak model cannot do).
    rewrite_history: List[Dict[str, Any]] = field(default_factory=list)
    merged_from: List[str] = field(default_factory=list)
    schema_version: int = SKILL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_vocab(self.status, SKILL_STATUSES, "Skill", "status")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Skill":
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Exam
# ---------------------------------------------------------------------------

@dataclass
class Exam(_Record):
    """One text exam question for a Subtask's capability (G3 output).

    Deliberately carries no skill reference: questions are written without
    seeing the skill, and both are linked only through ``subtask_id``.
    """

    exam_id: str
    question_text: str
    reference_answer: str
    subtask_id: str
    schema_version: int = EXAM_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Exam":
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Attempt
# ---------------------------------------------------------------------------

@dataclass
class Attempt(_Record):
    """One answering of one exam (K3 output), with or without skills.

    ``skill_id``/``skill_version`` are None for bare answers, so every record
    distinguishes "no skill" from "skill X version N".
    """

    attempt_id: str
    exam_id: str
    skill_id: Optional[str] = None
    skill_version: Optional[int] = None
    purpose: str = "bare"
    weak_answer: str = ""
    #: None until judged; True/False after G5.
    judge_verdict: Optional[bool] = None
    judge_reason: str = ""
    retry_round: int = 0
    schema_version: int = ATTEMPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_vocab(self.purpose, ATTEMPT_PURPOSES, "Attempt", "purpose")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attempt":
        return cls(**_known_kwargs(cls, data))
