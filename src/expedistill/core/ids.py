"""Deterministic ID rules (README 1.1 rule 6 and section 2).

Every child ID is the parent ID plus a fixed suffix; nothing here depends on
randomness, wall-clock time or model output. Re-running the same inputs yields
the same IDs, which is what lets a resumed run decide "this unit was already
done" by ID lookup alone (B3).

Rules (each ``make_*`` implements exactly one line of this table)::

    task_id            = f"{benchmark}:{raw_task_id}"
    trajectory_id      = f"traj/{task_id}/{role}/{attempt:02d}"
    subtask_id         = f"{trajectory_id}/seg{index:02d}"
    skill_id           = f"{subtask_id}/skill"
    exam_id            = f"{skill_id}/v{version:02d}/exam"
    attempt_id         = f"{exam_id}/att{serial:02d}"
    merged skill_id    = f"bank/merge/{serial:04d}"

``role`` is "strong" or "weak" (models.MODEL_ROLES).

Determinism caveat for the serial numbers: ``attempt`` is the sampling index
(0..n-1), ``index`` the segment's position in its trajectory, and ``serial``
is a *running number in creation order*. The serial-based IDs are therefore
only reproducible if the run replays the same creation order -- which the
stages guarantee by always processing units in a fixed (sorted-by-parent-ID)
order. A re-run that creates attempts in a different order produces different
IDs for the same logical attempts; that is a property of the scheme, not a
bug, and it is why creation order is fixed in the stages.

Zero padding is a minimum width, not a cap: serial 100 is ``att100`` and the
parsers accept any canonical width >= 2. Consequence: string order of IDs is
not numeric order once a serial outgrows its padding (``att100`` < ``att99``),
so anything that needs creation order must sort by the parsed number.

The parse helpers are the inverse of the make functions; they raise ValueError
on malformed IDs so a typo'd ID fails at the call site instead of silently
matching nothing in the store.
"""

import re
from typing import Any, Tuple

from expedistill.core.models import MODEL_ROLES

_TRAJ_PREFIX = "traj/"
_SEG = "seg"
_ATT = "att"

#: A zero-padded serial as the make_* functions write it: at least two ASCII
#: digits. ``{n:02d}`` pads but does not cap, so the 100th attempt is
#: ``att100`` and the parser must accept any width >= 2.
_SERIAL_RE = re.compile(r"[0-9]{2,}")


def _parse_serial(text: str) -> int:
    """Inverse of ``f"{n:02d}"``; ValueError on anything that formatter could
    not have produced (non-ASCII digits, signs, fewer than two digits, or
    extra leading zeros such as "007", which would alias "07")."""
    if not _SERIAL_RE.fullmatch(text):
        raise ValueError(text)
    value = int(text)
    if f"{value:02d}" != text:
        raise ValueError(text)
    return value


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

def make_task_id(benchmark: str, raw_task_id: str) -> str:
    return f"{benchmark}:{raw_task_id}"


def make_trajectory_id(task_id: str, role: str, attempt: int) -> str:
    if role not in MODEL_ROLES:
        raise ValueError(f'role must be one of {MODEL_ROLES}, got {role!r}')
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0, got {attempt}")
    return f"traj/{task_id}/{role}/{attempt:02d}"


def make_subtask_id(trajectory_id: str, index: int) -> str:
    if index < 0:
        raise ValueError(f"segment index must be >= 0, got {index}")
    return f"{trajectory_id}/{_SEG}{index:02d}"


def make_skill_id(subtask_id: str) -> str:
    return f"{subtask_id}/skill"


def make_exam_id(skill_id: str, version: int) -> str:
    """One exam per skill version (generalization exam, decision doc
    generalization_exam_20261027). The version is the ``Skill.version`` the
    exam tests, zero-padded like the other serials."""
    if version < 0:
        raise ValueError(f"skill version must be >= 0, got {version}")
    return f"{skill_id}/v{version:02d}/exam"


def make_attempt_id(exam_id: str, serial: int) -> str:
    if serial < 0:
        raise ValueError(f"attempt serial must be >= 0, got {serial}")
    return f"{exam_id}/{_ATT}{serial:02d}"


def make_merged_skill_id(serial: int) -> str:
    """Merged-skill ID; the serial is assigned by curation (stage 5) in merge
    order, so like attempt serials it is reproducible only under the same
    creation order."""
    if serial < 0:
        raise ValueError(f"merge serial must be >= 0, got {serial}")
    return f"bank/merge/{serial:04d}"


def is_stage3_candidate(skill: Any) -> bool:
    """True when ``skill`` is a stage-3 candidate: its id is exactly the
    stage-3 skill id of its own subtask. Merged skills (``bank/merge/NNNN``,
    stage 5) fail by construction -- their id is the merge's, not the
    anchor subtask's. Duck-typed on purpose (``.skill_id`` /
    ``.subtask_id``), so core stays free of model imports."""
    return skill.skill_id == make_skill_id(skill.subtask_id)


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


def parse_trajectory_id(trajectory_id: str) -> Tuple[str, str, int]:
    """Return (task_id, role, attempt)."""
    if not trajectory_id.startswith(_TRAJ_PREFIX):
        raise ValueError(f"not a trajectory id: {trajectory_id!r}")
    rest = trajectory_id[len(_TRAJ_PREFIX):]
    # A raw task id may itself contain '/' (unlikely but not forbidden), so
    # split from the right: the last two components are always role/attempt.
    parts = rest.rsplit("/", 2)
    if len(parts) != 3 or parts[1] not in MODEL_ROLES:
        raise ValueError(f"malformed trajectory id: {trajectory_id!r}")
    task_id, role, attempt_s = parts
    if not task_id:
        raise ValueError(f"malformed trajectory id: {trajectory_id!r}")
    try:
        return task_id, role, _parse_serial(attempt_s)
    except ValueError:
        raise ValueError(
            f"malformed trajectory id: {trajectory_id!r}") from None


def parse_subtask_id(subtask_id: str) -> Tuple[str, int]:
    """Return (trajectory_id, segment_index)."""
    trajectory_id, _, suffix = subtask_id.rpartition("/")
    if not trajectory_id or not suffix.startswith(_SEG):
        raise ValueError(f"malformed subtask id: {subtask_id!r}")
    try:
        return trajectory_id, _parse_serial(suffix[len(_SEG):])
    except ValueError:
        raise ValueError(f"malformed subtask id: {subtask_id!r}") from None


def parse_exam_id(exam_id: str) -> Tuple[str, int]:
    """Return (skill_id, version) from a generalization exam id."""
    skill_id, sep, tail = exam_id.rpartition("/v")
    if not sep or not tail.endswith("/exam"):
        raise ValueError(f"malformed exam id: {exam_id!r}")
    version = tail[:-len("/exam")]
    try:
        return skill_id, _parse_serial(version)
    except ValueError:
        raise ValueError(f"malformed exam id: {exam_id!r}") from None


def parse_attempt_id(attempt_id: str) -> Tuple[str, int]:
    """Return (exam_id, serial)."""
    exam_id, _, suffix = attempt_id.rpartition("/")
    if not exam_id or not suffix.startswith(_ATT):
        raise ValueError(f"malformed attempt id: {attempt_id!r}")
    try:
        return exam_id, _parse_serial(suffix[len(_ATT):])
    except ValueError:
        raise ValueError(f"malformed attempt id: {attempt_id!r}") from None


def task_id_of(trajectory_id: str) -> str:
    """Convenience: the task id embedded in a trajectory id."""
    return parse_trajectory_id(trajectory_id)[0]
