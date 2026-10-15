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
  still fail loudly instead of silently becoming defaults. Records written
  by an OLDER schema version migrate explicitly when the derivation is
  local to the record (a v1 Skill segment row derives ``source_subtasks``
  from its id, a v1 Exam row derives ``skill_id``); a v1 Skill merge
  product needs its sibling records and raises with a pointer to
  ``migrate_skill_record`` -- never a silent default either way.

IDs are not generated here: they follow the deterministic rules in
``expedistill.core.ids`` and are assigned by the caller. Objects reference each
other by ID only, never by holding a reference.

Deliberately no ``from __future__ import annotations`` (same reason as the
reference schema.py): it would turn annotations into strings for no gain on
3.9 and hides real types from any introspection-based tooling.

This module depends on the standard library only (layer 0).
"""

from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# Per-object schema versions. Bump the affected object's constant whenever a
# field is added or its meaning changes (README section 2 is the contract).
TRAJECTORY_SCHEMA_VERSION = 1
SUBTASK_SCHEMA_VERSION = 1
SKILL_SCHEMA_VERSION = 2
EXAM_SCHEMA_VERSION = 2
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

# ---------------------------------------------------------------------------
# Skill content section markers (stage 2/3 protocol v2, revision 3)
# ---------------------------------------------------------------------------

# A content-structure constant of the Skill object, placed by the
# field-value vocabularies on purpose: NOT a _check_vocab vocabulary itself
# (no field ever holds a marker as a value). Why it lives in core and not
# next to its first consumer (blocks/author.py): blocks never import each
# other, and a second block consumer exists (blocks/retrieve.py), so core
# is the only legal home both can import. The REWRITE/MERGE/SIMILARITY
# prompts embed these markers literally (their outputs must reproduce
# them); the G2 prompts carry the JSON field names that map 1:1 onto the
# four sections (blocks/author.py's renderer adds the markers);
# blocks/retrieve.py weights them for retrieval.

#: The four sections of a stored PROCEDURE skill's content.
PROCEDURE_MARKERS = ("[When to use]", "[Steps]", "[Verify]", "[Do not use when]")

#: The four sections of a stored FAILURE REFERENCE's content. The last
#: marker is shared by both kinds; the other three identify the kind.
FAILURE_REFERENCE_MARKERS = ("[Trigger]", "[Why it fails]", "[Do instead]",
                             "[Do not use when]")


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

    ``source_subtasks`` (schema v2) names the subtasks the skill's material
    came from: ``[own subtask_id]`` for a stage-3 skill, the union of its
    sources' lists for a merge product. It is computed once at write time
    (K5 for merges); readers never re-derive it.
    """

    skill_id: str
    content: str
    subtask_id: str
    #: Which subtasks this skill distills; see the class docstring.
    source_subtasks: List[str]
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
        if data.get("schema_version") == 1:
            data = _migrate_v1_skill_row(data)
        return cls(**_known_kwargs(cls, data))


# ---------------------------------------------------------------------------
# Exam
# ---------------------------------------------------------------------------

@dataclass
class Exam(_Record):
    """One text exam question for a Subtask's capability (G3 output).

    ``skill_id`` (schema v2) names the skill this exam tests. Questions
    are still written without seeing the skill; the link used to live
    only implicitly in the shared ``subtask_id`` and is now stored
    explicitly so consumers read the field instead of re-deriving it.
    """

    exam_id: str
    question_text: str
    reference_answer: str
    subtask_id: str
    #: The tested skill (ids.make_skill_id(subtask_id)).
    skill_id: str
    schema_version: int = EXAM_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Exam":
        if data.get("schema_version") == 1:
            data = _migrate_v1_exam_row(data)
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


# ---------------------------------------------------------------------------
# v1 -> v2 migration (schema bump 2026-10-15; AGENTS "修改数据对象" rule 3:
# explicit migration or a loud error, never a silent default)
# ---------------------------------------------------------------------------

#: The stage-3 skill id suffix, mirroring ``ids.make_skill_id`` (models must
#: not import ids: ids imports models). The migration unit tests pin the
#: equivalence, so the mirror cannot drift unnoticed.
_SKILL_ID_SUFFIX = "/skill"

#: The exam id suffix, mirroring ``ids.make_exam_id`` (same mirroring rule).
_EXAM_ID_SUFFIX = "/exam"


def _v1_segment_source_subtasks(
        record: Mapping[str, Any]) -> Optional[List[str]]:
    """``[own subtask_id]`` when the v1 row's id is its subtask's skill id
    (the stage-3 shape), else None."""
    subtask_id = record.get("subtask_id")
    if record.get("skill_id") == f"{subtask_id}{_SKILL_ID_SUFFIX}":
        return [subtask_id]
    return None


def _migrate_v1_skill_row(data: Dict[str, Any]) -> Dict[str, Any]:
    """Derive ``source_subtasks`` for one schema-v1 skill row (v2 row out).

    Segment-born rows -- every skill a pre-v2 run's stage 3 wrote -- carry
    exactly one source, their own subtask. A v1 merge product cannot be
    migrated from the row alone: its ``source_subtasks`` is the union of
    its sources' lists and the sources are separate records, so it raises
    with a pointer to ``migrate_skill_record`` instead.
    """
    skill_id = data.get("skill_id")
    if data.get("merged_from"):
        raise ValueError(
            f"skill {skill_id!r} is a schema-v1 merge product: its "
            "source_subtasks is the union of its sources' lists and "
            "cannot be derived from the record alone; migrate the run's "
            "records with expedistill.core.models.migrate_skill_record("
            "record, skills_by_id) instead of from_dict. Resuming a v1 "
            "run directly on this code is not supported: a formal run "
            "stays on the old code of its own maintenance branch "
            "(AGENTS.md section 6 rule 1)")
    derived = _v1_segment_source_subtasks(data)
    if derived is None:
        raise ValueError(
            f"skill {skill_id!r} is a schema-v1 record whose id is not its "
            f"subtask's skill id (subtask {data.get('subtask_id')!r}); "
            "source_subtasks cannot be derived -- a human must look at "
            "this run")
    return {**data, "source_subtasks": derived,
            "schema_version": SKILL_SCHEMA_VERSION}


def _migrate_v1_exam_row(data: Dict[str, Any]) -> Dict[str, Any]:
    """Derive ``skill_id`` for one schema-v1 exam row (v2 row out)."""
    exam_id = data.get("exam_id")
    subtask_id = data.get("subtask_id")
    if exam_id != f"{subtask_id}{_EXAM_ID_SUFFIX}":
        raise ValueError(
            f"exam {exam_id!r} is a schema-v1 record whose id is not its "
            f"subtask's exam id (subtask {subtask_id!r}); skill_id cannot "
            "be derived -- a human must look at this run")
    return {**data, "skill_id": f"{subtask_id}{_SKILL_ID_SUFFIX}",
            "schema_version": EXAM_SCHEMA_VERSION}


def merged_source_subtasks(
        source_lists: Iterable[Iterable[str]]) -> List[str]:
    """The union of the given ``source_subtasks`` lists, first-seen order.

    The value a merge product stores (schema v2): computed once at merge
    time by K5 from its sources' lists, so no reader ever re-derives it.
    The v1 migration is the one deliberate exception -- a v1 record
    predates the field, so ``migrate_skill_record`` rebuilds its value
    with this same function.
    """
    union: Dict[str, None] = {}
    for one in source_lists:
        for subtask_id in one:
            union.setdefault(subtask_id, None)
    return list(union)


def migrate_skill_record(
        record: Dict[str, Any],
        skills_by_id: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Migrate one stored skill row to the current schema; v2 row out.

    ``skills_by_id`` maps skill_id to the SAME run's stored rows (raw
    dicts, not objects). A v1 merge product resolves its source_subtasks
    recursively along ``merged_from`` -- nested merges included -- by
    unioning the sources' lists; a v1 segment row derives its single
    source from its id. A v2 row is returned unchanged. Raises ValueError
    when the lineage cannot be derived: an unknown v1 id shape, a
    merged_from id missing from ``skills_by_id``, or a merged_from cycle
    (each names a corrupted run). ``Skill.from_dict`` cannot do this job
    -- it sees one record at a time -- which is why v1 merge products
    raise there with a pointer to this function.
    """
    return _migrate_skill_record(record, skills_by_id, ())


def migrate_skill_rows(
        rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Migrate every stored skill row of one run to the current schema.

    The batch seam for full-store readers (the reuse assembler, the audit
    tool): a v1 merge product needs its sibling records, and a reader
    that already holds the whole store should not have to build the map
    itself, so this builds the id map once and migrates each row in input
    order. A v2 row is returned unchanged (the same object). Raises
    exactly what ``migrate_skill_record`` raises (a corrupted lineage).
    """
    skills_by_id = {str(row["skill_id"]): row for row in rows}
    return [migrate_skill_record(row, skills_by_id) for row in rows]


def _migrate_skill_record(
        record: Dict[str, Any],
        skills_by_id: Mapping[str, Dict[str, Any]],
        seen: Tuple[str, ...]) -> Dict[str, Any]:
    if record.get("schema_version") != 1:
        return record  # current (or newer) schema: nothing to derive
    skill_id = record.get("skill_id")
    if skill_id in seen:
        raise ValueError(
            f"skill {skill_id!r} appears twice in one merged_from chain; "
            "a corrupted run -- a human must look at it")
    merged_from = record.get("merged_from") or []
    if merged_from:
        source_lists: List[List[str]] = []
        for source_id in merged_from:
            source = skills_by_id.get(source_id)
            if source is None:
                raise ValueError(
                    f"skill {skill_id!r} merges from {source_id!r}, which "
                    "is not among the sibling records passed in "
                    "(skills_by_id); the run's stored skills and its merge "
                    "records disagree -- a human must look at this run")
            migrated = _migrate_skill_record(
                source, skills_by_id, seen + (skill_id,))
            source_subtasks = migrated.get("source_subtasks")
            if source_subtasks is None:
                raise ValueError(
                    f"skill {source_id!r} (a merge source of {skill_id!r}) "
                    "carries no source_subtasks; only a corrupt record can")
            source_lists.append(source_subtasks)
        return {**record,
                "source_subtasks": merged_source_subtasks(source_lists),
                "schema_version": SKILL_SCHEMA_VERSION}
    derived = _v1_segment_source_subtasks(record)
    if derived is None:
        raise ValueError(
            f"skill {skill_id!r} is a schema-v1 record whose id is neither "
            "its subtask's skill id nor a merge with sources; "
            "source_subtasks cannot be derived -- a human must look at "
            "this run")
    return {**record, "source_subtasks": derived,
            "schema_version": SKILL_SCHEMA_VERSION}
