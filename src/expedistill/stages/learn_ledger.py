"""The learn phase's ledgers and per-skill landing primitives.

Both learn paths share this module:

* the P02 add path (``stages.learn_add``) lands task-level add rows;
* the P04/P06 decision path (``stages.learn_apply``) lands per-skill
  keep / revise / delete rows.

Only the ledger mechanics live here -- constants, atomic writes, the
receipt lookups and the two status landings. Evidence collection and
authoring stay in the path modules, so each path owns its own model
calls and resume rules.
"""

from typing import Any, Dict, List, Optional, Sequence, Set

from expedistill.composites import provenance as _provenance
from expedistill.composites.decision import KEEP, DELETE
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
    "TASK_LIST_NAME",
    "append_outcome",
    "land_delete",
    "land_keep",
    "outcome_skills",
    "record_version",
    "receipted_skills",
    "revision_rows",
    "revision_skip_targets",
    "rows_of",
    "write_revision_plan",
    "write_task_list",
]

TASK_LIST_NAME = "task_list.jsonl"
REVISIONS_NAME = "revisions.jsonl"
OUTCOMES_NAME = "outcomes.jsonl"

_TASK_LIST_SCHEMA = "learn-task-list-v1"
_REVISION_SCHEMA = "learn-revision-v1"
_OUTCOME_SCHEMA = "learn-outcome-v1"

#: The statuses a learn phase may still act on. "merged" is curation's
#: product and "discarded" is terminal: rows pointing at either are
#: visibility leftovers, never actions.
ACTABLE_STATUSES = ("pending", "accepted")


def write_task_list(phase_dir, label: str,
                    rows: Sequence[TaskListRow],
                    superseded: Sequence[str] = ()) -> None:
    """Rewrite the phase's task list; superseded rows are exam-only."""
    rows_out = [{
        "schema": _TASK_LIST_SCHEMA,
        "phase_label": label,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "action": row.action,
        "reason": row.reason,
        "basis_unit": row.basis_unit,
    } for row in rows]
    if superseded:
        rows_out.append({
            "schema": _TASK_LIST_SCHEMA,
            "phase_label": label,
            "superseded_units": sorted(superseded),
        })
    write_jsonl(phase_dir / TASK_LIST_NAME, rows_out)


def append_outcome(phase: Any, row: TaskListRow,
                   skill_id: Optional[str], outcome: str,
                   version: Optional[int], phase_dir) -> None:
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
    })


def write_revision_plan(phase: Any, row: TaskListRow,
                        skill_id: str, from_version: int, phase_dir) -> None:
    """Record the pre-revision version before the author call.

    The version number is the completion proof a resume reads; the row
    is written exactly once per (target, skill)."""
    if any(record.get("skill_id") == skill_id
           for record in rows_of(phase_dir / REVISIONS_NAME, phase.label,
                                 row.target_id)):
        return
    append_jsonl(phase_dir / REVISIONS_NAME, {
        "schema": _REVISION_SCHEMA,
        "phase_label": phase.label,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "skill_id": skill_id,
        "from_version": from_version,
    })


def rows_of(path, phase_label: str, target_id: str) -> List[dict]:
    """One target's rows from one of the phase's ledgers."""
    if not path.exists():
        return []
    return [row for row in read_jsonl(path)
            if row.get("phase_label") == phase_label
            and row.get("target_id") == target_id]


def receipted_skills(phase: Any, row: TaskListRow,
                     phase_dir) -> Set[str]:
    """Skill ids this target already receipted in this phase."""
    return {record.get("skill_id")
            for record in rows_of(phase_dir / OUTCOMES_NAME, phase.label,
                                  row.target_id)
            if record.get("skill_id") is not None}


def outcome_skills(phase: Any, row: TaskListRow,
                   phase_dir) -> Set[str]:
    """Alias kept explicit for the apply path's resume branch."""
    return receipted_skills(phase, row, phase_dir)


def revision_rows(phase: Any, row: TaskListRow,
                  phase_dir) -> Dict[str, int]:
    """This target's first revision plan per skill (the resume proof)."""
    out: Dict[str, int] = {}
    for record in rows_of(phase_dir / REVISIONS_NAME, phase.label,
                          row.target_id):
        out.setdefault(record["skill_id"], record["from_version"])
    return out


def revision_skip_targets(store: Store, phase: Any,
                          rows: Sequence[TaskListRow], phase_dir) -> Set[str]:
    """Targets whose revision is already settled in this phase.

    A row whose skill already carries the version this phase's revision
    plan records, or whose receipt is present, needs no new evidence.
    """
    out: Set[str] = set()
    path = phase_dir / OUTCOMES_NAME
    receipts = ([record for record in read_jsonl(path)
                 if record.get("phase_label") == phase.label]
                if path.exists() else [])
    for row in rows:
        if getattr(row, "action", None) != "revise":
            continue
        if not store.exists("skills", row.target_id):
            continue
        skill = store.load("skills", row.target_id, Skill)
        if skill.status not in ACTABLE_STATUSES:
            out.add(row.target_id)
            continue
        if any(record.get("target_id") == row.target_id
               and record.get("skill_id") == skill.skill_id
               for record in receipts):
            out.add(row.target_id)
            continue
        revisions = revision_rows(phase, row, phase_dir)
        from_version = revisions.get(skill.skill_id)
        if from_version is not None and skill.version == from_version + 1:
            out.add(row.target_id)
    return out


def _load(store: Store, row: TaskListRow) -> Skill:
    if not store.exists("skills", row.target_id):
        raise ValueError(
            f"task-list row targets skill {row.target_id!r}, which the "
            "store does not carry: the decision and the store disagree")
    return store.load("skills", row.target_id, Skill)


def land_keep(store: Store, phase: Any, row: TaskListRow,
              phase_dir) -> None:
    """Accept one pending skill; a receipted or terminal skill is a no-op."""
    skill = _load(store, row)
    if skill.skill_id in receipted_skills(phase, row, phase_dir):
        return
    if skill.status not in ACTABLE_STATUSES:
        append_outcome(phase, row, skill.skill_id, "skipped_terminal",
                       skill.version, phase_dir)
        return
    if skill.status == "pending":
        skill.status = "accepted"
        store.save(skill, "skills")
        append_outcome(phase, row, skill.skill_id, "accepted",
                       skill.version, phase_dir)
    else:
        append_outcome(phase, row, skill.skill_id, "already_accepted",
                       skill.version, phase_dir)


def land_delete(store: Store, phase: Any, row: TaskListRow,
                phase_dir) -> None:
    """Discard one pending/accepted skill with the decision's reason."""
    skill = _load(store, row)
    if skill.skill_id in receipted_skills(phase, row, phase_dir):
        return
    if skill.status not in ACTABLE_STATUSES:
        append_outcome(phase, row, skill.skill_id, "skipped_terminal",
                       skill.version, phase_dir)
        return
    discard_skill(store, skill, row.reason)
    append_outcome(phase, row, skill.skill_id, "discarded",
                   skill.version, phase_dir)


def land_keep_or_delete(store: Store, phase: Any, row: TaskListRow,
                        phase_dir) -> None:
    """Small dispatcher for the apply path's non-revise rows."""
    if row.action == KEEP:
        land_keep(store, phase, row, phase_dir)
    elif row.action == DELETE:
        land_delete(store, phase, row, phase_dir)
    else:
        raise ValueError(f"not a keep/delete row: {row.action!r}")


def record_version(store: Store, phase_dir, run_dir, skill: Skill,
                   provenance: Provenance) -> None:
    """Save a new or revised skill version together with its provenance.

    The one door for every learn path (P02 add, live revise, exam revise,
    requirement): see ``composites.provenance.record_version``.
    """
    _provenance.record_version(store, phase_dir, run_dir, skill, provenance)
