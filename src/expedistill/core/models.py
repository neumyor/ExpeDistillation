"""The persisted objects of the data model (design spec section 4.1).

Every object is a plain dataclass with ``to_dict`` / ``from_dict`` so it can be
written to and read back from the per-run JSON layout. Two directions, two
policies:

* ``to_dict`` is strict: it always emits every declared field, including
  ``schema_version``. What lands on disk is a complete, self-describing record.
* ``from_dict`` is lenient about *unknown* keys only: they are dropped.
  Missing or wrong-typed *known* fields fail loudly instead of silently
  becoming defaults (AGENTS.md section "修改数据对象"), and every versioned
  object -- ``Trajectory``, ``Subtask``, ``SubtaskType``, ``Skill`` -- reads
  only its current schema and raises on any other.

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
# field is added or its meaning changes (design spec 4.1 is the contract).
# Trajectory v3: the id carries the execute phase that produced it
# (``traj/<phase_tag>/<task_id>/<role>/<attempt>``), so one task's baseline
# and practice runs never share an id.
# Subtask v2: ``phase_label`` names the learn phase that cut the segment.
# Skill v5: one skill per subtask type (``subtask_type``), the status is
# ``active`` / ``discarded``.
TRAJECTORY_SCHEMA_VERSION = 3
SUBTASK_SCHEMA_VERSION = 2
SUBTASK_TYPE_SCHEMA_VERSION = 1
SKILL_SCHEMA_VERSION = 5

# ---------------------------------------------------------------------------
# Vocabularies (design spec 4.1; keep in sync)
# ---------------------------------------------------------------------------

#: Which executor produced a trajectory / which model a segment came from.
MODEL_ROLES = ("strong", "weak")

#: Lifecycle of a skill: delivery hands out ``active`` skills only; a
#: ``discarded`` skill stays on disk with its ``discard_reason``.
SKILL_STATUSES = ("active", "discarded")

#: The fields of a skill's trigger (what the catalog shows) and of its
#: content (what opening the skill adds). Each rewrite changes one side.
TRIGGER_FIELDS = ("summary", "when_to_use")
CONTENT_FIELDS = ("steps", "not_applicable_when")
SKILL_SIDES = {"trigger": TRIGGER_FIELDS, "content": CONTENT_FIELDS}

#: The two global texts every learn phase evolves.
EVOLVING_TEXT_KINDS = ("profile", "playbook")


def _check_vocab(value: str, allowed: Tuple[str, ...], owner: str,
                 field_name: str) -> None:
    if value not in allowed:
        raise ValueError(
            f'{owner}.{field_name} is {value!r}, expected one of {allowed}')


def _check_text(value: Any, owner: str, field_name: str) -> None:
    """A required one-piece text field: a non-blank string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{owner}.{field_name} must be a non-empty string, got {value!r}")


def _check_schema(cls, data: Dict[str, Any], current: int,
                  id_field: str) -> None:
    """Refuse a record of any schema version but ``current``."""
    if not isinstance(data, dict):
        raise TypeError(
            f'{cls.__name__}.from_dict expects a mapping, got '
            f'{type(data).__name__}')
    version = data.get("schema_version")
    if version != current or isinstance(version, bool):
        raise ValueError(
            f"{cls.__name__.lower()} {data.get(id_field)!r} is a "
            f"schema-v{version} record; this code reads schema v{current} "
            "only")


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

    ``trajectory_id`` names the execute phase that produced the run
    (``ids.make_trajectory_id``); the record has no other phase field.

    ``correctness`` is the benchmark's own scorer verdict, passed through
    verbatim: ``True`` the scorer judged the run right, ``False`` wrong,
    ``None`` no scorer verdict came back (a no-scorer benchmark, or a run
    that completed without a measurable result). It is never an acceptance
    conclusion -- drawing one is acceptance's job, and an infrastructure
    failure never reaches this record at all (it raises and K1 retries the
    unit; nothing is stored for the failed attempt).
    """

    trajectory_id: str
    task_id: str
    benchmark: str
    model_role: str
    model_name: str
    attempt_index: int
    #: None = bare run; otherwise the reference of the whole skill bank for
    #: bank-injected runs.
    skills_injected: Optional[Any] = None
    steps: List[Step] = field(default_factory=list)
    #: The scorer verdict (class docstring); True / False / None.
    correctness: Optional[bool] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = TRAJECTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_vocab(self.model_role, MODEL_ROLES, "Trajectory", "model_role")
        if self.correctness is not None \
                and not isinstance(self.correctness, bool):
            raise ValueError(
                f"Trajectory.correctness must be True, False or None, got "
                f"{self.correctness!r}")
        self.steps = [Step(**step) if isinstance(step, dict) else step
                      for step in self.steps]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trajectory":
        _check_schema(cls, data, TRAJECTORY_SCHEMA_VERSION, "trajectory_id")
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Subtask
# ---------------------------------------------------------------------------

@dataclass
class Subtask(_Record):
    """One semantically coherent segment of a trajectory (K2 output).

    The step texts are NOT copied here: they live only on the Trajectory and
    are fetched via ``step_range`` (single source of truth). Two success fields
    on purpose: ``success`` is about the segment's own goal, ``trajectory_success``
    about the run the segment was cut from. ``phase_label`` is the learn
    phase that cut the segment (``fork-run`` filters shared objects by it).
    """

    subtask_id: str
    phase_label: str
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
        _check_text(self.phase_label, "Subtask", "phase_label")
        range_ = tuple(self.step_range)
        if len(range_) != 2:
            raise ValueError(
                f'Subtask.step_range must be [start, end), got {self.step_range!r}')
        self.step_range = (int(range_[0]), int(range_[1]))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Subtask":
        _check_schema(cls, data, SUBTASK_SCHEMA_VERSION, "subtask_id")
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# SubtaskType
# ---------------------------------------------------------------------------

@dataclass
class SubtaskType(_Record):
    """One row of the run-level subtask type table (append-only).

    ``type_id`` is ``ids.make_type_id(serial)`` in order of appearance;
    ``entry_state`` is the observable state the subtask starts from (what a
    card's ``when_to_use`` describes); ``phase_label`` is the learn phase
    that added the type. At most one skill exists per type.
    """

    type_id: str
    name: str
    entry_state: str
    phase_label: str
    schema_version: int = SUBTASK_TYPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("type_id", "name", "entry_state", "phase_label"):
            _check_text(getattr(self, name), "SubtaskType", name)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubtaskType":
        _check_schema(cls, data, SUBTASK_TYPE_SCHEMA_VERSION, "type_id")
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

@dataclass
class Skill(_Record):
    """A natural-language skill card for one subtask type.

    The trigger is ``summary`` (one line: what the skill is) plus
    ``when_to_use``; the content is ``steps`` (decision nodes, stored
    without numbers) plus ``not_applicable_when``. ``inject.skill_text``
    renders them; nothing stores the rendered card.

    ``subtask_type`` is the ``type_id`` the card is written for (its id is
    ``ids.make_skill_id(subtask_type)``). ``verbatim_text`` is set only on
    skills imported from another method's document (import-bank): the
    document is shown as is, ``subtask_type`` is empty, and the four
    structured fields may be empty except ``summary``.

    Rewrites do not create a new object: they bump ``version`` and append
    the replaced fields to ``rewrite_history``. ``source_subtasks`` names
    the material segments the card was written from, computed once at
    write time.
    """

    skill_id: str
    subtask_type: str
    source_subtasks: List[str]
    summary: str
    when_to_use: str
    steps: List[str]
    not_applicable_when: str
    verbatim_text: str = ""
    version: int = 1
    status: str = "active"
    #: e.g. "content_failed_twice"; free-form so new discard reasons do
    #: not need a schema bump.
    discard_reason: Optional[str] = None
    #: One entry per rewrite: ``side`` (trigger / content), ``old`` (the
    #: replaced fields with their previous values) and the reason.
    rewrite_history: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = SKILL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _check_vocab(self.status, SKILL_STATUSES, "Skill", "status")
        if not isinstance(self.subtask_type, str):
            raise ValueError(
                f"Skill {self.skill_id!r}: subtask_type must be a string, "
                f"got {self.subtask_type!r}")
        if not isinstance(self.steps, list) or not all(
                isinstance(step, str) for step in self.steps):
            raise ValueError(
                f"Skill.steps must be a list of strings, got {self.steps!r}")
        if self.verbatim_text:
            return
        # A written card always belongs to a subtask type; only an
        # imported document has none.
        if not self.subtask_type.strip():
            raise ValueError(
                f"Skill {self.skill_id!r}: subtask_type must name a type "
                "unless the skill is an imported (verbatim) document")
        for name in ("summary", "when_to_use", "not_applicable_when"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Skill {self.skill_id!r}: {name} must be a non-empty "
                    "string")
        if not self.steps:
            raise ValueError(
                f"Skill {self.skill_id!r}: steps must not be empty")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Skill":
        _check_schema(cls, data, SKILL_SCHEMA_VERSION, "skill_id")
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# EvolvingText
# ---------------------------------------------------------------------------

@dataclass
class EvolvingText(_Record):
    """One version of a global text a learn phase evolves: the weak-model
    profile or the playbook. ``source_sheet_ids`` names the answer sheets
    whose diagnoses fed this version."""

    kind: str
    text: str
    version: int
    phase_label: str
    source_sheet_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _check_vocab(self.kind, EVOLVING_TEXT_KINDS, "EvolvingText", "kind")
        if not isinstance(self.text, str):
            raise ValueError(
                f"EvolvingText.text must be a string, got {self.text!r}")
        if isinstance(self.version, bool) or not isinstance(
                self.version, int) or self.version < 1:
            raise ValueError(
                f"EvolvingText.version must be an int >= 1, got "
                f"{self.version!r}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvolvingText":
        return cls(**_known_kwargs(cls, data))
