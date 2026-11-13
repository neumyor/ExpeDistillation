"""The learn phase's card ledgers and landing primitives (layer 5).

Every learn phase keeps two JSONL ledgers in its phase directory beside
the provenance ledger:

* ``revisions.jsonl`` -- one plan row per rewrite, written before the
  rewrite call: the version it starts from is the resume proof;
* ``outcomes.jsonl`` -- one receipt per card operation (``TaskListRow``:
  a new card for a type, one side's rewrite of a card, or a deletion).

The store is the truth and a receipt its echo: a resumed phase skips a
receipted operation, and a rewrite whose card already carries the planned
version gets its missing receipt without a new model call. Rows are keyed
by (phase, target, action), so one card's content and trigger rewrites in
the same phase are two operations. Parallel units append in completion
order; :func:`commit_order` rewrites the three ledgers in target order once
the units are done, so the files do not depend on the worker count.
"""

from typing import Any, List, Optional

from expedistill.composites import provenance as _provenance
from expedistill.composites.skill_status import discard_skill
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.core.trace import Provenance
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_jsonl,
    write_jsonl,
)

__all__ = [
    "ACTABLE_STATUSES",
    "OUTCOMES_NAME",
    "REVISIONS_NAME",
    "append_outcome",
    "commit_order",
    "land_delete",
    "receipt_of",
    "record_version",
    "revision_plan",
    "rows_of",
    "write_revision_plan",
]

REVISIONS_NAME = "revisions.jsonl"
OUTCOMES_NAME = "outcomes.jsonl"

_REVISION_SCHEMA = "learn-revision-v3"
_OUTCOME_SCHEMA = "learn-outcome-v3"

#: The statuses a learn phase may still act on: a discarded card is
#: terminal.
ACTABLE_STATUSES = ("active",)


def rows_of(path, phase_label: str, target_id: str,
            action: Optional[str] = None) -> List[dict]:
    """One target's rows (of one action, when given) from a ledger."""
    if not path.exists():
        return []
    return [row for row in read_jsonl(path)
            if row.get("phase_label") == phase_label
            and row.get("target_id") == target_id
            and (action is None or row.get("action") == action)]


def append_outcome(phase: Any, row: TaskListRow,
                   skill_id: Optional[str], outcome: str,
                   version: Optional[int], phase_dir,
                   reason: str = "") -> None:
    """One receipt row (the store is the truth, the receipt its echo)."""
    append_jsonl(phase_dir / OUTCOMES_NAME, {
        "schema": _OUTCOME_SCHEMA,
        "phase_label": phase.label,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "action": row.action,
        "skill_id": skill_id,
        "outcome": outcome,
        "version": version,
        "reason": reason,
        "basis_unit": row.basis_unit,
    })


def receipt_of(phase: Any, row: TaskListRow, phase_dir) -> Optional[dict]:
    """This operation's receipt in this phase, or None."""
    rows = rows_of(phase_dir / OUTCOMES_NAME, phase.label, row.target_id,
                   row.action)
    return rows[0] if rows else None


def write_revision_plan(phase: Any, row: TaskListRow, skill_id: str,
                        from_version: int, phase_dir) -> None:
    """Record the pre-revision version before the rewrite call; written
    exactly once per operation."""
    if rows_of(phase_dir / REVISIONS_NAME, phase.label, row.target_id,
               row.action):
        return
    append_jsonl(phase_dir / REVISIONS_NAME, {
        "schema": _REVISION_SCHEMA,
        "phase_label": phase.label,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "action": row.action,
        "skill_id": skill_id,
        "from_version": from_version,
    })


def revision_plan(phase: Any, row: TaskListRow,
                  phase_dir) -> Optional[int]:
    """The version this operation's rewrite started from (the resume
    proof), or None when no rewrite was planned yet."""
    rows = rows_of(phase_dir / REVISIONS_NAME, phase.label, row.target_id,
                   row.action)
    return rows[0]["from_version"] if rows else None


def land_delete(store: Store, phase: Any, row: TaskListRow,
                phase_dir) -> str:
    """The delete rule's writer: discard the card with the row's reason
    (``content_failed_twice``) and write the receipt; returns the outcome.
    A receipted operation is a no-op, and a card a crash left discarded
    before its receipt only gets the receipt."""
    if receipt_of(phase, row, phase_dir) is not None:
        return "receipted"
    skill = store.load("skills", row.target_id, Skill)
    if skill.status in ACTABLE_STATUSES:
        skill = discard_skill(store, skill, row.reason)
    append_outcome(phase, row, skill.skill_id, "discarded", skill.version,
                   phase_dir, row.reason)
    return "discarded"


def record_version(store: Store, phase_dir, run_dir, skill: Skill,
                   provenance: Provenance) -> None:
    """Save a new or revised skill version together with its provenance
    (``composites.provenance.record_version``, the one door)."""
    _provenance.record_version(store, phase_dir, run_dir, skill, provenance)


def commit_order(phase_dir) -> None:
    """Rewrite the phase's receipt, revision and provenance ledgers in a
    fixed order: receipts and plans by target id (a target's own rows keep
    their order: content before trigger), provenance by skill id and
    version."""
    for name in (OUTCOMES_NAME, REVISIONS_NAME):
        path = phase_dir / name
        if path.exists():
            rows = read_jsonl(path)
            write_jsonl(path, sorted(
                rows, key=lambda row: str(row.get("target_id"))))
    path = phase_dir / _provenance.PROVENANCE_NAME
    if path.exists():
        rows = read_jsonl(path)
        write_jsonl(path, sorted(
            rows, key=lambda row: (str(row.get("skill_id")),
                                   int(row.get("version") or 0))))
