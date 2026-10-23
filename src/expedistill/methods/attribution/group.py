"""One task group's usage-aware attribution, as fact-only packages.

This replaces the deprecated ``usage_group`` collector. The policy gate
that lived there (when to attribute, what to keep/delete) is gone: the
decision has already chosen the revise rows, and this driver only reads
facts and asks the attribution model once for the whole group.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.blocks.attribution import attribute_usage
from expedistill.blocks.view import final_status_word
from expedistill.composites.live_measurement import (
    load_baseline,
    render_live_views,
    round_dir,
    task_trajectories,
)
from expedistill.composites.skill_usage import SKILL_USAGE_NAME
from expedistill.core.contracts import AcceptanceReportRow
from expedistill.core.models import Skill, Trajectory
from expedistill.infra.store import Store, read_jsonl
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    description_text,
)
from expedistill.llm.call import ModelHandle
from expedistill.composites.evidence_package import EvidencePackage

__all__ = ["collect_group_attribution"]


def _measurement_facts(kind: str, trajectories: List[Trajectory]) \
        -> List[dict]:
    return [
        {"kind": kind,
         "text": f"attempt {traj.attempt_index}: "
                 f"{final_status_word(traj.correctness)}"}
        for traj in trajectories
    ]


def _usage_rows(round_dir_path: Path, task_id: str,
                round_: int) -> List[dict]:
    return [row for row in read_jsonl(round_dir_path / SKILL_USAGE_NAME)
            if row.get("task_id") == task_id
            and row.get("round") == round_]


def _usage_facts(rows: List[dict], skills: List[Skill]) \
        -> tuple[str, Dict[str, Dict[str, Any]], str, str]:
    """(prompt table, per-skill aggregates, delivery_status, selection)."""
    by_id: Dict[str, Dict[str, Any]] = {
        skill.skill_id: {
            "attempts": 0,
            "read_attempts": 0,
            "read_steps": [],
            "outcomes_when_read": [],
            "outcomes_when_unread": [],
        }
        for skill in skills
    }
    lines: List[str] = []
    delivery_status = "ok"
    any_read = False
    all_read = bool(rows)
    for row in sorted(rows, key=lambda item: item.get("attempt", 0)):
        read_ids = list(row.get("read_skill_ids") or [])
        unread_ids = list(row.get("unread_skill_ids") or [])
        any_read = any_read or bool(read_ids)
        all_read = all_read and not unread_ids
        if row.get("delivery_status") != "ok":
            delivery_status = row.get("delivery_status") or "unknown"
        reads = ", ".join(
            f"{event['skill_id']}@step{event['step_index']}"
            for event in row.get("reads") or [])
        lines.append(
            f"attempt {row.get('attempt')}: outcome={row.get('outcome')} "
            f"read=[{reads}] unread=[{', '.join(unread_ids)}]")
        for skill_id, aggregate in by_id.items():
            aggregate["attempts"] += 1
            if skill_id in read_ids:
                aggregate["read_attempts"] += 1
                step = next((event["step_index"] for event in row.get("reads")
                             or [] if event["skill_id"] == skill_id), None)
                if step is not None:
                    aggregate["read_steps"].append(step)
                aggregate["outcomes_when_read"].append(row.get("outcome"))
            else:
                aggregate["outcomes_when_unread"].append(row.get("outcome"))
    for skill in skills:
        aggregate = by_id[skill.skill_id]
        first_step = min(aggregate["read_steps"]) \
            if aggregate["read_steps"] else None
        lines.append(
            f"{skill.skill_id}: read "
            f"{aggregate['read_attempts']}/{aggregate['attempts']}, "
            f"first_step={first_step}, "
            f"outcomes_when_read={aggregate['outcomes_when_read']}, "
            f"outcomes_when_unread={aggregate['outcomes_when_unread']}")
    selection = "none_read" if not any_read else (
        "all_read" if all_read else "some_read")
    return "\n".join(lines), by_id, delivery_status, selection


def _sibling_descriptions(skill: Skill, members: List[Skill],
                          description_mode: str) -> str:
    return "\n".join(
        f"{other.skill_id}: {description_text(other, description_mode)}"
        for other in members if other.skill_id != skill.skill_id)


def _first_actions(trajectories: List[Trajectory]) -> str:
    if not trajectories:
        return ""
    return "\n".join(step.action for step in trajectories[0].steps[:3])[:4000]


def _skill_usage_text(aggregate: Dict[str, Any]) -> str:
    first_step = min(aggregate["read_steps"]) \
        if aggregate["read_steps"] else None
    return (
        f"read {aggregate['read_attempts']}/{aggregate['attempts']}; "
        f"first_step={first_step}; "
        f"outcomes_when_read={aggregate['outcomes_when_read']}; "
        f"outcomes_when_unread={aggregate['outcomes_when_unread']}")


def collect_group_attribution(
    *,
    task_id: str,
    members: List[Skill],
    report_row: AcceptanceReportRow,
    store: Store,
    round_: int,
    n_attempts: int,
    task_instruction: str,
    weak_profile: str,
    author_handle: ModelHandle,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> List[EvidencePackage]:
    """Return one fact-only package per group member.

    ``report_row`` is the live report row that measured the group; its
    presence is the caller's proof that the group was actually measured.
    """
    if not members:
        raise ValueError(
            f"task {task_id!r} has no members to attribute")
    baseline_trajectories, _ = load_baseline(store, task_id, n_attempts)
    baseline_facts = _measurement_facts("baseline_run",
                                        baseline_trajectories)
    round_trajs = task_trajectories(store, task_id, round_)
    live_facts = _measurement_facts("live_run", round_trajs)
    live_views = render_live_views(round_trajs, view_rules)

    rows = _usage_rows(round_dir(store, round_), task_id, round_)
    usage_table, aggregates, delivery_status, selection = _usage_facts(
        rows, members)
    description_mode = (rows[0].get("description_mode", DEFAULT_DESCRIPTION_MODE)
                        if rows else DEFAULT_DESCRIPTION_MODE)
    judged: Dict[str, Dict[str, Any]] = {}
    diagnosis = ""
    if rows and delivery_status == "ok":
        plan = attribute_usage(
            members, task_instruction,
            "\n".join(fact["text"] for fact in baseline_facts),
            live_views, usage_table, weak_profile, author_handle,
            usage=usage, run_dir=run_dir)
        diagnosis = plan["diagnosis"]
        judged = {entry["skill_id"]: entry for entry in plan["skills"]}

    segments = [
        {"trajectory_id": traj.trajectory_id,
         "step_range": [0, len(traj.steps)],
         "correctness": traj.correctness}
        for traj in round_trajs]
    first_actions = _first_actions(round_trajs)

    packages: List[EvidencePackage] = []
    for skill in members:
        facts: List[dict] = list(baseline_facts) + list(live_facts)
        if diagnosis:
            facts.append({"kind": "group_diagnosis", "text": diagnosis})
        facts.append({"kind": "delivery_status", "text": delivery_status})
        facts.append({"kind": "selection_status", "text": selection})
        facts.append({"kind": "skill_usage",
                      "text": _skill_usage_text(aggregates[skill.skill_id])})
        facts.append({
            "kind": "skill_read_attempts",
            "text": str(aggregates[skill.skill_id]["read_attempts"])})
        facts.append({"kind": "sibling_descriptions",
                      "text": _sibling_descriptions(
                          skill, members, description_mode)})
        facts.append({"kind": "first_actions", "text": first_actions})
        facts.append({"kind": "task_instruction", "text": task_instruction})
        entry = judged.get(skill.skill_id)
        if entry is not None:
            facts.append({"kind": "skill_relevance",
                          "text": entry["relevance"]})
            facts.append({"kind": "skill_application",
                          "text": entry["application"]})
            if entry["problem"].strip():
                facts.append({"kind": "skill_problem",
                              "text": entry["problem"]})
        facts.append({"kind": "failure_view", "text": live_views})
        packages.append(EvidencePackage(
            target=skill.skill_id,
            facts=facts,
            segments=list(segments),
            old_skill=skill,
        ))
    return packages
