"""B3: object storage over a run directory (README section 2 layout).

Every stage writes its units here as they finish, one JSON file per object,
named by the object ID. Atomic writes (a temp file in the destination
directory, then os.replace) mean a crash can leave a run without the unit it
was writing but never with a half-written one, so a resumed run can trust
everything it finds (outer AGENTS.md section 1: write per unit, resume by ID).

Adapted from the SkillExpand repo's persistence/io.py (ExpeToSkill-merged @
TB-eval 4f436af): same atomic-write and JSONL-ledger semantics. Not migrated
from there: the flock-based run lock (superseded by runlock.py's O_EXCL lock
file, which can name its holder) and the SkillLibrary version chain (this
design stores skills as flat files plus a bank JSONL, not a version chain).
"""

import fcntl
import json
import os
import tempfile
import threading
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from expedistill.core.models import Attempt, Exam, Skill, Subtask, Trajectory

#: Object kinds and their directories in the README run layout.
KIND_DIRS: Dict[str, str] = {
    "trajectories": "stage1_baseline/trajectories",
    "subtasks": "stage2_segments/subtasks",
    "skills": "stage3_generation/skills",
    "exams": "stage3_generation/exams",
    "attempts": "stage4_acceptance/attempts",
}

#: Every directory of the run layout, created by Store on demand. The five
#: kind directories plus the two stage-only ones and llm_requests/ (B1 writes
#: its raw request log there).
LAYOUT_DIRS: List[str] = list(KIND_DIRS.values()) + [
    "stage5_curation",
    "stage6_test",
    "llm_requests",
]

#: ID attribute of each object kind. Objects also carry parent ids as foreign
#: keys (Exam has subtask_id, Subtask has trajectory_id, Skill has subtask_id),
#: so the persisted ID must always be resolved through the kind, never by
#: scanning for the first present ``*_id`` field.
_KIND_ID_FIELD = {
    "trajectories": "trajectory_id",
    "subtasks": "subtask_id",
    "skills": "skill_id",
    "exams": "exam_id",
    "attempts": "attempt_id",
}

#: Every ``*_id`` field any object can carry (foreign keys included).
_ID_FIELDS = (
    "trajectory_id",
    "subtask_id",
    "skill_id",
    "exam_id",
    "attempt_id",
)

_TYPE_KINDS = (
    (Trajectory, "trajectories"),
    (Subtask, "subtasks"),
    (Skill, "skills"),
    (Exam, "exams"),
    (Attempt, "attempts"),
)


def object_id(obj: Any, kind: Optional[str] = None) -> str:
    """The persisted ID of ``obj``.

    With ``kind`` given, the ID field is the one that belongs to that kind
    (the only correct way for stored objects, which carry parent ids too).
    Without ``kind``, the first present ``*_id`` field wins -- only valid for
    objects that carry no foreign keys.
    """
    if kind is not None:
        field = _KIND_ID_FIELD.get(kind)
        if field is None:
            raise ValueError(f"unknown store kind {kind!r}")
        value = getattr(obj, field, None)
        if value is None:
            raise ValueError(
                f"object of type {type(obj).__name__} has no {field}; "
                f"it cannot be stored as {kind}")
        return str(value)
    for name in _ID_FIELDS:
        value = getattr(obj, name, None)
        if value is not None:
            return str(value)
    raise ValueError(
        f"object of type {type(obj).__name__} has none of the id fields "
        f"{_ID_FIELDS}; it cannot be stored")


def kind_for(obj: Any) -> str:
    """The store kind of ``obj`` by its type (cheap convenience for callers)."""
    for cls, kind in _TYPE_KINDS:
        if isinstance(obj, cls):
            return kind
    raise ValueError(f"no store kind for objects of type {type(obj).__name__}")


def write_json(path: Path, data: Any, indent: Optional[int] = None) -> None:
    """Atomically replace ``path`` with JSON ``data``.

    The temp file is created in the destination directory so os.replace is a
    same-filesystem rename (atomic), and is removed on any failure, so an
    interrupted write never leaves a partial file or debris behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=indent)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


#: Sidecar suffix where append_jsonl keeps torn tails it truncated away.
INTERRUPTED_TAIL_SUFFIX = ".interrupted-tail"

#: Sidecar suffix of the flock file that serialises appends to one ledger.
LOCK_SUFFIX = ".lock"

#: One threading.Lock per ledger (keyed by realpath), so threads of one
#: process serialise before they contend for the flock.
_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _reset_thread_locks_in_child() -> None:
    # A fork copies locks held by threads that do not exist in the child;
    # start the child with fresh, unheld ones.
    global _THREAD_LOCKS_GUARD
    _THREAD_LOCKS_GUARD = threading.Lock()
    _THREAD_LOCKS.clear()


os.register_at_fork(after_in_child=_reset_thread_locks_in_child)


@contextmanager
def _ledger_lock(path: Path) -> Iterator[None]:
    """Exclusive lock over one ledger, across threads and processes.

    A per-path threading.Lock for this process, then an flock on the
    ``<file>.lock`` sidecar for other processes (the ledger itself is not
    locked: it is truncated during repair, and a sidecar keeps the lock
    independent of the data file's lifetime). Keyed by realpath so two
    spellings of one file share a lock.
    """
    real = os.path.realpath(path)
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(real, threading.Lock())
    with thread_lock, open(real + LOCK_SUFFIX, "a") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_stream, fcntl.LOCK_UN)

_TAIL_BLOCK = 64 * 1024


def _repair_hook(path: Path) -> None:
    """Test seam: called after a torn tail is read, before it is repaired.
    Tests replace it to widen the read-then-truncate window."""


def _tail_start(stream: Any, size: int) -> int:
    """Offset just after the last b"\n" in the first ``size`` bytes (0 if
    there is none). Reads backwards in blocks so a large ledger is not read
    in full on every append."""
    position = size
    while position > 0:
        block_start = max(0, position - _TAIL_BLOCK)
        stream.seek(block_start)
        block = stream.read(position - block_start)
        newline = block.rfind(b"\n")
        if newline != -1:
            return block_start + newline + 1
        position = block_start
    return 0


def _is_complete_record(fragment: bytes) -> bool:
    """True if ``fragment`` is one whole JSON object (what append_jsonl
    writes). A torn prefix of a canonical line can never parse as a complete
    object: the object's closing brace is the line's last byte."""
    try:
        return isinstance(json.loads(fragment), dict)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """Append one canonical JSON line and fsync it before returning.

    Appending one line-sized buffer in append mode is atomic enough on a
    local filesystem: a crash mid-write can only leave a partial final line.
    Before appending, a file that does not end with a newline is repaired so
    a torn write can never turn into permanent mid-file corruption:

    * if the fragment after the last newline parses as a complete record (the
      record was written but its newline was lost), the newline is added and
      the record kept;
    * otherwise the fragment is a torn write -- never a committed record:
      append_jsonl only returns after the whole line is fsynced, and every
      append holds the ledger lock from the tail read to the fsync, so no
      other writer can commit a line between this writer's read of the tail
      and its truncate. It is copied to ``<file>.interrupted-tail``
      (appended, fsynced, as evidence) and the ledger is truncated back to
      just after the last newline. Committed records are therefore never
      truncated.

    Without the truncation the next append would add a newline after the
    fragment, making it a corrupt non-final line that read_jsonl rejects on
    every later read. A crash during the repair is itself safe: the evidence
    is written before the truncate, and the truncate before the new line, so
    the worst case is a duplicate evidence entry on the next append.

    Safe for concurrent writers: the whole repair + append runs under
    ``_ledger_lock`` (a per-path threading.Lock plus an flock on
    ``<file>.lock``), so callers -- UnitFailureRecorder, UsageRecorder, the
    K4/K5 ledgers, the skill bank -- need no locking of their own. Callers
    must not hold an flock on ``<file>.lock`` themselves around a call
    (flock is per open file description, so that would self-deadlock). The
    guarantee holds for writers that go through append_jsonl on a local
    filesystem; a writer that bypasses it is not serialised.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True)
            + "\n").encode("utf-8")
    with _ledger_lock(path), path.open("ab+") as stream:
        size = stream.seek(0, os.SEEK_END)
        if size:
            stream.seek(size - 1)
            if stream.read(1) != b"\n":
                start = _tail_start(stream, size)
                stream.seek(start)
                fragment = stream.read(size - start)
                _repair_hook(path)
                if _is_complete_record(fragment):
                    stream.write(b"\n")
                else:
                    _keep_interrupted_tail(path, fragment)
                    stream.truncate(start)
                    stream.flush()
                    os.fsync(stream.fileno())
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _keep_interrupted_tail(path: Path, fragment: bytes) -> None:
    """Append a truncated torn tail to the evidence sidecar, one per line."""
    if not fragment.strip():
        return
    evidence = path.with_name(path.name + INTERRUPTED_TAIL_SUFFIX)
    with evidence.open("ab") as stream:
        stream.write(fragment + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read JSONL records; tolerate one interrupted final record.

    Side-effect free: an interrupted final record is skipped here and
    repaired (truncated, kept as evidence) by the next append_jsonl. A
    corrupt record before the final one is real corruption (an interrupted
    append can only damage the tail, and append_jsonl never builds on a torn
    tail), so it raises instead of silently shrinking the ledger.
    """
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_bytes().splitlines()
    records: List[Dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            if index != len(lines) - 1:
                raise ValueError(
                    f"{path}: corrupt JSONL record {index + 1} before the "
                    "final line; the ledger is damaged, not interrupted") from exc
    return records


class Store:
    """B3: save/load/list the five objects in a run directory.

    Composites ask ``exists`` before doing a unit; a present file means the
    unit is done and is skipped on resume (IDs are deterministic, so the
    same input maps to the same filename).
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        for relative in LAYOUT_DIRS:
            (self.run_dir / relative).mkdir(parents=True, exist_ok=True)

    def dir_for(self, kind: str) -> Path:
        try:
            return self.run_dir / KIND_DIRS[kind]
        except KeyError:
            known = ", ".join(sorted(KIND_DIRS))
            raise ValueError(
                f"unknown object kind {kind!r}; known kinds: {known}") from None

    def path_for(self, kind: str, obj_id: str) -> Path:
        """Filename is the percent-encoded ID: IDs contain '/' and ':' which
        are fine in JSON but not as path segments."""
        return self.dir_for(kind) / (urllib.parse.quote(obj_id, safe="")
                                     + ".json")

    def save(self, obj: Any, kind: str) -> Path:
        """Atomically write ``obj``; returns the path written."""
        path = self.path_for(kind, object_id(obj, kind))
        write_json(path, obj.to_dict(), indent=2)
        return path

    def exists(self, kind: str, obj_id: str) -> bool:
        return self.path_for(kind, obj_id).exists()

    def load(self, kind: str, obj_id: str, cls: Any) -> Any:
        path = self.path_for(kind, obj_id)
        if not path.exists():
            raise FileNotFoundError(
                f"no stored {kind[:-1]} with id {obj_id!r} in {self.run_dir}")
        return cls.from_dict(read_json(path))

    def ids(self, kind: str) -> List[str]:
        """All stored object IDs of ``kind``, sorted (decoded, not encoded)."""
        directory = self.dir_for(kind)
        return sorted(
            urllib.parse.unquote(path.stem)
            for path in directory.glob("*.json"))

    def append_bank(self, skill_dict: Dict[str, Any]) -> None:
        """Append one accepted skill to skill_bank.jsonl (stage 5 output).

        The bank is a JSONL ledger, not a file rewritten in full, so a crash
        loses at most the line being written (see append_jsonl).
        """
        append_jsonl(self.run_dir / "skill_bank.jsonl", skill_dict)

    def bank(self) -> List[Dict[str, Any]]:
        """The curated skill bank, in append order (stage 6 injects this)."""
        return read_jsonl(self.run_dir / "skill_bank.jsonl")
