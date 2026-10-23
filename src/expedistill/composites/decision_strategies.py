"""The P04/P06 decision strategies: one per-skill row per measured skill.

The decision module decides WHAT happens to each skill; attribution and
authoring stay downstream and shared. Two strategies are available:

* ``task_level`` -- run the existing group table (``decision.decide_task_list``)
  and expand each group action to its measured members;
* ``skill_level`` -- decide from the recorded read facts: a skill that was
  never read is deleted, a skill that was read is revised. If the usage
  evidence is missing or untrusted, the group is kept: "not delivered" is
  not "not useful".

Both return ``TaskListRow`` rows with ``target_kind='skill'``. The caller
writes them to the phase's ``task_list.jsonl``.
"""

from typing import Dict, List, Optional, Sequence

from expedistill.composites import decision
from expedistill.core import ids
from expedistill.core.contracts import AcceptanceReportRow, TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "DECISION_NAMES",
    "REASON_NEVER_READ",
    "REASON_SKILL_READ",
    "REASON_UNVERIFIED_DELIVERY",
    "decide",
]

TASK_LEVEL = "task_level"
SKILL_LEVEL = "skill_level"
DECISION_NAMES = (TASK_LEVEL, SKILL_LEVEL)

REASON_SKILL_READ = "skill_read"
REASON_NEVER_READ = "skill_never_read"
REASON_UNVERIFIED_DELIVERY = "unverified_delivery"


def decide(*, name: str,
           current_kind: Optional[str],
           current_report: Optional[Sequence[AcceptanceReportRow]],
           control_kind: Optional[str],
           control_report: Optional[Sequence[AcceptanceReportRow]],
           bank: Sequence[Skill],
           checks_left: int,
           compare_rule: str,
           n_attempts_weak: int,
           task_select_rule: str,
           usage_by_task: Optional[Dict[str, List[dict]]] = None
           ) -> List[TaskListRow]:
    """Dispatch one configured strategy; both return per-skill rows."""
    if name == TASK_LEVEL:
        return task_level(
            current_kind=current_kind, current_report=current_report,
            control_kind=control_kind, control_report=control_report,
            bank=bank, checks_left=checks_left, compare_rule=compare_rule,
            n_attempts_weak=n_attempts_weak,
            task_select_rule=task_select_rule)
    if name == SKILL_LEVEL:
        return skill_level(
            current_report=current_report or (),
            usage_by_task=usage_by_task or {})
    raise CodeBugError(
        f"unknown learn decision strategy {name!r}; expected one of "
        f"{DECISION_NAMES}")


def task_level(*, current_kind: Optional[str],
               current_report: Optional[Sequence[AcceptanceReportRow]],
               control_kind: Optional[str],
               control_report: Optional[Sequence[AcceptanceReportRow]],
               bank: Sequence[Skill], checks_left: int,
               compare_rule: str, n_attempts_weak: int,
               task_select_rule: str) -> List[TaskListRow]:
    """The existing task-group table, expanded to one row per member."""
    group_rows = decision.decide_task_list(
        current_kind=current_kind, current_report=current_report,
        control_kind=control_kind, control_report=control_report,
        bank=bank, checks_left=checks_left, compare_rule=compare_rule,
        n_attempts_weak=n_attempts_weak,
        task_select_rule=task_select_rule)
    by_unit = {row.unit_id: row for row in current_report or ()}
    out: List[TaskListRow] = []
    for row in group_rows:
        if row.target_kind != "group":
            out.append(row)
            continue
        report_row = by_unit.get(row.basis_unit)
        if report_row is None:
            raise CodeBugError(
                f"group row {row.target_id!r} names basis unit "
                f"{row.basis_unit!r}, which the current report does not "
                "carry")
        for skill_id in _members(report_row, row.target_id, bank):
            out.append(TaskListRow(
                target_kind="skill", target_id=skill_id,
                action=row.action, reason=row.reason,
                basis_unit=row.basis_unit))
    _assert_unique(out)
    return out


def skill_level(*, current_report: Sequence[AcceptanceReportRow],
                usage_by_task: Dict[str, List[dict]]
                ) -> List[TaskListRow]:
    """Read facts decide: read -> revise, never read -> delete.

    One guard covers the abnormal cases: missing usage rows, a non-ok
    delivery status, or a member absent from the delivered manifest all
    mean the skill never had a trustworthy chance to be read, so the
    group is kept instead of deleted.
    """
    out: List[TaskListRow] = []
    for report_row in sorted(current_report, key=lambda row: row.unit_id):
        task_id, _ = decision.parse_unit_id(report_row.unit_id)
        members = _members(report_row, task_id, ())
        if not members:
            continue
        rows = usage_by_task.get(task_id, [])
        trusted = bool(rows) and all(
            row.get("delivery_status") == "ok" for row in rows)
        available = set()
        read = set()
        if trusted:
            for row in rows:
                available.update(row.get("available_skill_ids") or [])
                read.update(row.get("read_skill_ids") or [])
        for skill_id in members:
            if not trusted or skill_id not in available:
                action, reason = (decision.KEEP,
                                  REASON_UNVERIFIED_DELIVERY)
            elif skill_id in read:
                action, reason = decision.REVISE, REASON_SKILL_READ
            else:
                action, reason = decision.DELETE, REASON_NEVER_READ
            out.append(TaskListRow(
                target_kind="skill", target_id=skill_id,
                action=action, reason=reason,
                basis_unit=report_row.unit_id))
    _assert_unique(out)
    return out


def _members(report_row: AcceptanceReportRow, task_id: str,
             bank: Sequence[Skill]) -> List[str]:
    """The measured members: the report's own refs, else the task's bank."""
    refs = report_row.injected_skills
    if isinstance(refs, list) and refs:
        return [ref["skill_id"] for ref in refs
                if isinstance(ref, dict) and ref.get("skill_id")]
    if bank:
        return sorted(skill.skill_id for skill in bank
                      if _task_of_skill(skill) == task_id)
    return []


def _task_of_skill(skill: Skill) -> Optional[str]:
    try:
        trajectory_id, _ = ids.parse_subtask_id(skill.subtask_id)
        return ids.parse_trajectory_id(trajectory_id)[0]
    except (ValueError, TypeError):
        return None


def _assert_unique(rows: Sequence[TaskListRow]) -> None:
    seen = set()
    for row in rows:
        if row.target_id in seen:
            raise CodeBugError(
                f"decision emitted two rows for skill {row.target_id!r}; "
                "one skill must have exactly one action")
        seen.add(row.target_id)
