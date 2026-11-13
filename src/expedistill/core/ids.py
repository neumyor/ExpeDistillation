"""Deterministic ID rules (design spec 3.1 rule 6 and section 4.1).

Every ID is built from its parent's ID or a program-assigned serial; nothing
here depends on randomness, wall-clock time or model output. Re-running the
same inputs yields the same IDs, which is what lets a resumed run decide
"this unit was already done" by ID lookup alone (B3).

Rules (each ``make_*`` implements exactly one line of this table)::

    task_id            = f"{benchmark}:{raw_task_id}"
    trajectory_id      = f"traj/{phase_tag}/{task_id}/{role}/{attempt:02d}"
    subtask_id         = f"{trajectory_id}/seg{index:02d}"
    type_id            = f"type/{serial:03d}"
    skill_id           = f"{type_id}/skill"

``phase_tag`` is the execute phase's label with the colon replaced by an
underscore (``p01:baseline`` -> ``p01_baseline``, see ``phase_tag``), so one
task's baseline run and its practice runs -- and the segments cut from
them -- never share an id. ``role`` is "strong" or "weak"
(models.MODEL_ROLES). Imported skills keep their own ``import/...`` ids
(stages/import_bank.py); they have no subtask type.

``attempt`` is the sampling index (0..n-1) and ``index`` the segment's
position in its trajectory. The type ``serial`` is a running number in
order of appearance in the run's append-only type table; it is reproducible
because the learn phase assigns new types in a fixed order.

Zero padding is a minimum width, not a cap: attempt 100 is ``100`` and the
parsers accept any canonical width at or above the padding. Consequence:
string order of IDs is not numeric order once a number outgrows its
padding, so anything that needs creation order must sort by the parsed
number.

The parse helpers are the inverse of the make functions; they raise ValueError
on malformed IDs so a typo'd ID fails at the call site instead of silently
matching nothing in the store.
"""

import re
from typing import NamedTuple, Tuple

from expedistill.core.models import MODEL_ROLES

_TRAJ_PREFIX = "traj/"
_TYPE_PREFIX = "type/"
_SKILL_SUFFIX = "/skill"
_SEG = "seg"

#: A phase label as core.config.phase_label writes it: ``p<NN>:<name>``,
#: the name over the colon-free phase-name charset.
_PHASE_LABEL_RE = re.compile(r"(p[0-9]{2,}):([A-Za-z0-9._-]+)")

#: The same label with the colon replaced (``phase_tag``'s output).
_PHASE_TAG_RE = re.compile(r"(p[0-9]{2,})_([A-Za-z0-9._-]+)")


def _parse_serial(text: str, width: int = 2) -> int:
    """Inverse of ``f"{n:0{width}d}"``; ValueError on anything that
    formatter could not have produced (non-ASCII digits, signs, fewer than
    ``width`` digits, or extra leading zeros such as "007" for width 2,
    which would alias "07")."""
    if not (len(text) >= width and text.isascii() and text.isdigit()):
        raise ValueError(text)
    value = int(text)
    if f"{value:0{width}d}" != text:
        raise ValueError(text)
    return value


class TrajectoryIdParts(NamedTuple):
    """What a trajectory id names, in id order."""

    phase_label: str
    task_id: str
    role: str
    attempt: int


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

def make_task_id(benchmark: str, raw_task_id: str) -> str:
    return f"{benchmark}:{raw_task_id}"


def phase_tag(phase_label: str) -> str:
    """The path-safe form of a phase label: ``p03:practice`` ->
    ``p03_practice``. ValueError on anything that is not a phase label."""
    if not isinstance(phase_label, str) \
            or not _PHASE_LABEL_RE.fullmatch(phase_label):
        raise ValueError(
            f"not a phase label (p<NN>:<name>): {phase_label!r}")
    return phase_label.replace(":", "_", 1)


def make_trajectory_id(phase_label: str, task_id: str, role: str,
                       attempt: int) -> str:
    """The id of one execution: the execute phase that ran it, the task,
    the executor role and the sampling index."""
    tag = phase_tag(phase_label)
    if not isinstance(task_id, str) or not task_id:
        raise ValueError(f"task_id must be a non-empty string, got {task_id!r}")
    if role not in MODEL_ROLES:
        raise ValueError(f'role must be one of {MODEL_ROLES}, got {role!r}')
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0, got {attempt}")
    return f"{_TRAJ_PREFIX}{tag}/{task_id}/{role}/{attempt:02d}"


def make_subtask_id(trajectory_id: str, index: int) -> str:
    if index < 0:
        raise ValueError(f"segment index must be >= 0, got {index}")
    return f"{trajectory_id}/{_SEG}{index:02d}"


def make_type_id(serial: int) -> str:
    """The id of the subtask type with this serial (order of appearance in
    the run's type table, from 1)."""
    if isinstance(serial, bool) or not isinstance(serial, int) or serial < 1:
        raise ValueError(f"type serial must be an int >= 1, got {serial!r}")
    return f"{_TYPE_PREFIX}{serial:03d}"


def make_skill_id(type_id: str) -> str:
    """The id of the one skill a subtask type may have."""
    parse_type_id(type_id)
    return f"{type_id}{_SKILL_SUFFIX}"


# ---------------------------------------------------------------------------
# Parse helpers (inverses)
# ---------------------------------------------------------------------------

def raw_task_id(task_id: str, benchmark: str) -> str:
    """Return the bare task id a prefixed one carries (the inverse of
    ``make_task_id`` for one known benchmark -- the prefix is the only
    place an id records its benchmark, so the caller names it). Raises
    ValueError on an id of another benchmark or without the prefix, and on
    a non-canonical shape: an empty remainder, or one that still contains
    a colon. The colon is this scheme's separator, so such a remainder
    means the id was built from an already-prefixed id
    (``make_task_id("tb", "tb:x")``); handing it on would only move the
    failure to load_tasks, whose plugins reject colons in names (TB's
    task-name regex), instead of naming it here at the call site.
    """
    prefix = f"{benchmark}:"
    if not task_id.startswith(prefix):
        raise ValueError(f"not a {benchmark!r} task id: {task_id!r}")
    raw = task_id[len(prefix):]
    if not raw or ":" in raw:
        raise ValueError(
            f"not a canonical {benchmark!r} task id: {task_id!r}")
    return raw


def parse_trajectory_id(trajectory_id: str) -> TrajectoryIdParts:
    """Return (phase_label, task_id, role, attempt)."""
    if not trajectory_id.startswith(_TRAJ_PREFIX):
        raise ValueError(f"not a trajectory id: {trajectory_id!r}")
    rest = trajectory_id[len(_TRAJ_PREFIX):]
    tag, _, rest = rest.partition("/")
    tag_match = _PHASE_TAG_RE.fullmatch(tag)
    # A raw task id may itself contain '/' (unlikely but not forbidden), so
    # split from the right: the last two components are always role/attempt.
    parts = rest.rsplit("/", 2)
    if tag_match is None or len(parts) != 3 or parts[1] not in MODEL_ROLES \
            or not parts[0]:
        raise ValueError(f"malformed trajectory id: {trajectory_id!r}")
    task_id, role, attempt_s = parts
    try:
        attempt = _parse_serial(attempt_s)
    except ValueError:
        raise ValueError(
            f"malformed trajectory id: {trajectory_id!r}") from None
    label = f"{tag_match.group(1)}:{tag_match.group(2)}"
    return TrajectoryIdParts(label, task_id, role, attempt)


def parse_subtask_id(subtask_id: str) -> Tuple[str, int]:
    """Return (trajectory_id, segment_index)."""
    trajectory_id, _, suffix = subtask_id.rpartition("/")
    if not trajectory_id or not suffix.startswith(_SEG):
        raise ValueError(f"malformed subtask id: {subtask_id!r}")
    try:
        return trajectory_id, _parse_serial(suffix[len(_SEG):])
    except ValueError:
        raise ValueError(f"malformed subtask id: {subtask_id!r}") from None


def parse_type_id(type_id: str) -> int:
    """Return the type's serial."""
    if not isinstance(type_id, str) or not type_id.startswith(_TYPE_PREFIX):
        raise ValueError(f"not a subtask type id: {type_id!r}")
    try:
        serial = _parse_serial(type_id[len(_TYPE_PREFIX):], width=3)
    except ValueError:
        raise ValueError(f"malformed subtask type id: {type_id!r}") from None
    if serial < 1:
        raise ValueError(f"malformed subtask type id: {type_id!r}")
    return serial
