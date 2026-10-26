"""The P04/P06 decision strategies: one per-skill row per measured skill.

The decision module decides WHAT happens to each skill; attribution and
authoring stay downstream and shared. Two strategies are available:

* ``task_level`` -- run the existing group table (``decision.decide_task_list``)
  and expand each group action to its measured members;
* ``skill_level`` -- decide from the recorded read facts and the live
  group result: a skill that was never read is deleted; a skill that was
  read and whose group won (status ``accepted``) is kept unchanged; a
  skill that was read but whose group did not win (status ``pending``)
  is revised. If the usage evidence is missing or untrusted, the group
  is kept: "not delivered" is not "not useful". The 2026-10-24 ruling
  made "read -> revise" conditional on not having won; P04 and P06 use
  this one function.

Both return ``TaskListRow`` rows with ``target_kind='skill'``. The caller
writes them to the phase's ``task_list.jsonl``.
"""

from typing import Dict, List, Optional, Sequence

from expedistill.composites import decision
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.composites.skill_use import (
    FOLDER_DELIVERIES,
    skill_used_in_attempt,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "DECISION_NAMES",
    "REASON_ALREADY_WON",
    "REASON_GROUP_WON",
    "REASON_NEVER_READ",
    "REASON_SKILL_NOT_WON",
    "REASON_SKILL_READ",
    "REASON_UNVERIFIED_DELIVERY",
    "REASON_UNVERIFIED_GROUP",
    "REASON_UNVERIFIED_STATUS",
    "REASON_WON_GROUP_UNREAD",
    "UNREAD_POLICIES",
    "decide",
]

TASK_LEVEL = "task_level"
SKILL_LEVEL = "skill_level"
DECISION_NAMES = (TASK_LEVEL, SKILL_LEVEL)

REASON_SKILL_READ = "skill_read"
REASON_NEVER_READ = "skill_never_read"
REASON_UNVERIFIED_DELIVERY = "unverified_delivery"
REASON_ALREADY_WON = "already_won"
REASON_SKILL_NOT_WON = "skill_not_won"
REASON_GROUP_WON = "group_won"
REASON_WON_GROUP_UNREAD = "won_group_unread"
REASON_UNVERIFIED_GROUP = "unverified_group"
REASON_UNVERIFIED_STATUS = "unverified_status"

UNREAD_POLICIES = ("delete", "keep")


def decide(*, name: str,
           current_kind: Optional[str],
           current_report: Optional[Sequence[UnitSummary]],
           control_kind: Optional[str],
           control_report: Optional[Sequence[UnitSummary]],
           bank: Sequence[Skill],
           checks_left: int,
           compare_rule: str,
           n_attempts_weak: int,
           task_select_rule: str,
           delivery: str = "",
           usage_by_task: Optional[Dict[str, List[dict]]] = None,
           group_better_by_task: Optional[Dict[str, Optional[bool]]] = None,
           skill_status_by_id: Optional[Dict[str, str]] = None,
           unread_in_won_group: str = "delete",
           ) -> List[TaskListRow]:
    """Dispatch one configured strategy; both return per-skill rows."""
    if name == TASK_LEVEL:
        return task_level(
            current_kind=current_kind, current_report=current_report,
            control_kind=control_kind, control_report=control_report,
            bank=bank, checks_left=checks_left, compare_rule=compare_rule,
            n_attempts_weak=n_attempts_weak,
            task_select_rule=task_select_rule, delivery=delivery,
            usage_by_task=usage_by_task or {})
    if name == SKILL_LEVEL:
        return skill_level(
            current_report=current_report or (), bank=bank,
            checks_left=checks_left, delivery=delivery,
            usage_by_task=usage_by_task or {},
            group_better_by_task=group_better_by_task,
            skill_status_by_id=skill_status_by_id,
            unread_in_won_group=unread_in_won_group)
    raise CodeBugError(
        f"unknown learn decision strategy {name!r}; expected one of "
        f"{DECISION_NAMES}")


def task_level(*, current_kind: Optional[str],
               current_report: Optional[Sequence[UnitSummary]],
               control_kind: Optional[str],
               control_report: Optional[Sequence[UnitSummary]],
               bank: Sequence[Skill], checks_left: int,
               compare_rule: str, n_attempts_weak: int,
               task_select_rule: str, delivery: str = "",
               usage_by_task: Optional[Dict[str, List[dict]]] = None
               ) -> List[TaskListRow]:
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
        members = _members(report_row, row.target_id, bank)
        for skill_id in members:
            action, reason = row.action, row.reason
            if row.action == decision.REVISE and not _used(
                    skill_id, row.target_id, delivery,
                    usage_by_task or {}, members):
                action, reason = decision.KEEP, "not_triggered"
            out.append(TaskListRow(
                target_kind="skill", target_id=skill_id,
                action=action, reason=reason,
                basis_unit=row.basis_unit))
    _assert_unique(out)
    return out


def skill_level(*, current_report: Sequence[UnitSummary],
                bank: Sequence[Skill] = (),
                checks_left: int = 0,
                delivery: str,
                usage_by_task: Dict[str, List[dict]],
                group_better_by_task: Optional[
                    Dict[str, Optional[bool]]] = None,
                skill_status_by_id: Optional[Dict[str, str]] = None,
                unread_in_won_group: str = "delete",
                ) -> List[TaskListRow]:
    """Read facts + group result decide the three terminal actions.

    A unit-level terminal status is resolved FIRST with the shared action
    table: control-perfect deletes ``weak_already_knows``; a not-testable
    cause deletes (or revises a leak while a check remains); a
    ``zero_execution`` row is a visibility fact and emits no row.

    For a normal unit: never read -> delete. Read and already won
    (``accepted``) -> keep, never rewrite a proven skill. Read, still
    ``pending``, and the live group did not win -> revise while a check
    remains, delete on the last check (the shared table's no-gain row).
    Missing/untrusted usage or a missing group/status fact keeps the
    skill rather than guessing.

    A reused unit (rule 1) has no usage of its own in this phase; its
    carried summary's ``reused_from`` marker lets the group result fall
    back to the skill statuses (a group that won accepted its members).
    """
    if unread_in_won_group not in UNREAD_POLICIES:
        raise CodeBugError(
            f"unknown unread_in_won_group policy {unread_in_won_group!r}; "
            f"expected one of {UNREAD_POLICIES}")
    group_better = group_better_by_task or {}
    statuses = skill_status_by_id or {}
    out: List[TaskListRow] = []
    for report_row in sorted(current_report, key=lambda row: row.unit_id):
        task_id, _ = decision.parse_unit_id(report_row.unit_id)
        members = _members(report_row, task_id, bank)
        if not members:
            continue
        if (report_row.status == decision.ROW_NOT_TESTABLE
                and report_row.cause == decision.CAUSE_ZERO_EXECUTION):
            # B1: a visibility row has no completed run to decide from.
            continue
        if report_row.status in (decision.ROW_NOT_RUN_CONTROL_PERFECT,
                                 decision.ROW_NOT_TESTABLE):
            action = _terminal_action(
                report_row, checks_left=checks_left,
                phase_kind=decision.PHASE_LIVE)
            out.extend(_rows_for(members, action.kind,
                                 action.reason or "",
                                 report_row.unit_id))
            continue
        if report_row.status != decision.ROW_NORMAL:
            raise CodeBugError(
                f"unit {report_row.unit_id!r} reports unknown status "
                f"{report_row.status!r}")
        if report_row.valid_count == 0:
            raise CodeBugError(
                f"unit {report_row.unit_id!r} reports status normal with "
                "no valid run; acceptance must report not_testable instead")
        rows = usage_by_task.get(task_id, [])
        if delivery in FOLDER_DELIVERIES:
            trusted = bool(rows) and all(
                row.get("delivery_status") == "ok" for row in rows)
            if not trusted:
                out.extend(_rows_for(members, decision.KEEP,
                                     REASON_UNVERIFIED_DELIVERY,
                                     report_row.unit_id))
                continue
        better = group_better.get(task_id)
        if better is None:
            better = _carried_better(report_row, members, statuses)
        for skill_id in members:
            used = _used(skill_id, task_id, delivery, usage_by_task,
                         members)
            status = statuses.get(skill_id)
            if used is None:
                action, reason = (decision.KEEP,
                                  REASON_UNVERIFIED_DELIVERY)
            elif used:
                if status == "accepted":
                    action, reason = decision.KEEP, REASON_ALREADY_WON
                elif status == "pending":
                    if better is False:
                        if checks_left > 0:
                            action, reason = (decision.REVISE,
                                              REASON_SKILL_NOT_WON)
                        else:
                            action, reason = (decision.DELETE,
                                              decision.LIVE_NO_IMPROVEMENT)
                    elif better is True:
                        action, reason = decision.KEEP, REASON_GROUP_WON
                    else:
                        action, reason = (decision.KEEP,
                                          REASON_UNVERIFIED_GROUP)
                else:
                    action, reason = (decision.KEEP,
                                      REASON_UNVERIFIED_STATUS)
            else:
                if unread_in_won_group == "keep" and better is True \
                        and status == "accepted":
                    action, reason = (decision.KEEP,
                                      REASON_WON_GROUP_UNREAD)
                else:
                    action, reason = decision.DELETE, REASON_NEVER_READ
            out.append(TaskListRow(
                target_kind="skill", target_id=skill_id,
                action=action, reason=reason,
                basis_unit=report_row.unit_id))
    _assert_unique(out)
    return out


def _terminal_action(report_row: UnitSummary, *, checks_left: int,
                     phase_kind: str) -> decision.Action:
    """The shared table's action for a unit-level terminal status."""
    if report_row.status == decision.ROW_NOT_RUN_CONTROL_PERFECT:
        return decision.table_action(
            conclusion=decision.CONCLUSION_NO_HEADROOM,
            checks_left=checks_left, phase_kind=phase_kind)
    if report_row.cause not in decision.NOT_TESTABLE_CAUSES:
        raise CodeBugError(
            f"unit {report_row.unit_id!r} reports cause "
            f"{report_row.cause!r}, which is not in the frozen "
            "not-testable set")
    return decision.table_action(
        conclusion=decision.CONCLUSION_NOT_TESTABLE,
        cause=report_row.cause, checks_left=checks_left,
        phase_kind=phase_kind)


def _carried_better(report_row: UnitSummary, members: Sequence[str],
                    statuses: Dict[str, str]) -> Optional[bool]:
    """A reused unit's group result, inferred from its members' status.

    A won group accepts every pending member; a lost group leaves them
    pending. Only used when the current phase has no verdict row for the
    task (rule 1 reused it), never as a substitute for real usage facts.
    """
    if not (report_row.artifacts or {}).get("reused_from"):
        return None
    known = [statuses.get(skill_id) for skill_id in members
             if statuses.get(skill_id) is not None]
    if not known:
        return None
    return any(status == "accepted" for status in known)


def _used(skill_id: str, task_id: str, delivery: str,
          usage_by_task: Dict[str, List[dict]],
          members: Sequence[str]) -> Optional[bool]:
    if delivery not in FOLDER_DELIVERIES:
        return skill_id in set(members)
    rows = usage_by_task.get(task_id, [])
    if not rows:
        return None
    available = set()
    for row in rows:
        available.update(row.get("available_skill_ids") or [])
    if skill_id not in available:
        return None
    answers = [
        skill_used_in_attempt(
            skill_id=skill_id, attempt=row.get("attempt", 0),
            delivery=delivery, usage_row=row).used
        for row in rows]
    if any(answer is True for answer in answers):
        return True
    if any(answer is None for answer in answers):
        return None
    return False


def _members(report_row: UnitSummary, task_id: str,
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


def _rows_for(skill_ids: Sequence[str], action: str, reason: str,
              basis_unit: str) -> List[TaskListRow]:
    return [TaskListRow(target_kind="skill", target_id=skill_id,
                        action=action, reason=reason,
                        basis_unit=basis_unit)
            for skill_id in skill_ids]
