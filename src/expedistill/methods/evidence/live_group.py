"""The evidence driver for a live group's failed round (G10 as facts).

R6 turns the evidence family from a params-only package into a real
driver (repair-plan G7). This module is the live method's G10 entry:
given a task list row that authorizes a group revision and the run's
material (baseline trajectories, the round's trajectories, the frozen
injection audit), it produces one evidence package per group member
carrying FACTS ONLY -- the measurement numbers, the group diagnosis,
the per-skill problem and the failed attempts' views. The keep/revise/
delete vocabulary the underlying model prompt still emits (its output
contract is frozen; prompts/ is out of this wave's scope) is DISCARDED
HERE, at the evidence boundary: no package, fact or segment carries an
action. The author module decides what to do with each skill from
these facts.

What stays out on purpose:

* the repair budget and its failure ledger are composite machinery
  (composites/live_attribution.py owns them for the stage's legacy
  path); a ModelOutputError propagates to the caller. The group-abandon
  on budget EXHAUSTION is the decision module's call (R6 item 3), never
  this driver's -- it does not touch the skill store at all.
* persistence and resume. The driver reads the run directory through
  composites/live_measurement.py's disk-only readers and writes
  nothing; the Wave 2 phase driver owns the ledgers.
"""

from pathlib import Path
from typing import Any, List, Optional, Union

from expedistill.blocks.attribution import attribute_failure
from expedistill.blocks.view import render_trajectory_view
from expedistill.composites.live_attribution import (
    _LIVE_VIEWS_BUDGET_CHARS,
    _clip_view,
)
from expedistill.composites.live_measurement import (
    INJECTION_AUDIT_NAME,
    _require_audit_row_from,
    _require_group,
    _trajectories_from,
    load_baseline,
    round_dir,
)
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill, Trajectory
from expedistill.llm.call import ModelHandle
from expedistill.infra.store import Store

from expedistill.methods.evidence.package import EvidencePackage

__all__ = ["collect_group_evidence"]


def _measurement_facts(kind: str, trajectories: List[Trajectory]) \
        -> List[dict]:
    """One fact per attempt: the run's own status line (a measurement
    number the author reads alongside the diagnosis)."""
    return [
        {"kind": kind, "text": f"attempt {traj.attempt_index}: {traj.status}"}
        for traj in trajectories]


def collect_group_evidence(
    *,
    task_row: TaskListRow,
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
    """Collect one group's round evidence as fact-only packages.

    ``task_row`` is the decision module's authorization (action
    ``revise``; anything else is a caller bug); its ``target_id`` is the
    prefixed task id whose group failed to beat its baseline. The G10
    model call goes through blocks.attribution.attribute_failure with
    the same composed inputs the legacy path builds (baseline status
    lines, every attempt's clipped view, the group listing), so both
    paths see the same bytes. Every skill with a non-empty problem gets
    a ``skill_problem`` fact; a skill without one gets no problem fact
    (the author's default policy keeps it). The round's trajectories
    are pinned as segments with their step ranges, so the author can
    point at the exact runs the facts came from.
    """
    if task_row.action != "revise":
        raise ValueError(
            "collect_group_evidence needs a task list row with action "
            f"'revise', got {task_row.action!r} on target "
            f"{task_row.target_id!r}")
    task_id = task_row.target_id
    members: List[Skill] = _require_group(store, task_id)

    baseline_trajectories, _ = load_baseline(store, task_id, n_attempts)
    baseline_facts = _measurement_facts("baseline_run",
                                       baseline_trajectories)
    round_trajs = _trajectories_from(round_dir(store, round_), task_id,
                                     round_)
    live_facts = _measurement_facts("live_run", round_trajs)
    per_view_budget = _LIVE_VIEWS_BUDGET_CHARS // max(1, len(round_trajs))
    live_views = "\n\n".join(
        _clip_view(render_trajectory_view(traj, view_rules),
                   per_view_budget)
        for traj in round_trajs)
    audit = _require_audit_row_from(
        round_dir(store, round_) / INJECTION_AUDIT_NAME, round_, task_id)

    # The prompt's output contract still pins the action vocabulary
    # (frozen this wave); the actions are dropped here, never packaged.
    plan = attribute_failure(
        members, task_instruction,
        "\n".join(fact["text"] for fact in baseline_facts),
        live_views, weak_profile, author_handle, usage=usage,
        run_dir=run_dir)
    problems = {entry["skill_id"]: entry["problem"]
                for entry in plan["skill_actions"]}
    by_id = {skill.skill_id: skill for skill in members}
    segments = [
        {"trajectory_id": traj.trajectory_id,
         "step_range": [0, len(traj.steps)],
         "status": traj.status}
        for traj in round_trajs]

    packages: List[EvidencePackage] = []
    for skill in members:
        facts: List[dict] = list(baseline_facts) + list(live_facts)
        facts.append({"kind": "group_diagnosis",
                      "text": plan["diagnosis"]})
        problem = problems.get(skill.skill_id, "")
        if problem.strip():
            facts.append({"kind": "skill_problem", "text": problem})
        facts.append({"kind": "failure_view", "text": live_views})
        facts.append({"kind": "injection_block_sha256",
                      "text": audit["injection_block_sha256"]})
        packages.append(EvidencePackage(
            target=skill.skill_id,
            facts=facts,
            segments=list(segments),
            old_skill=by_id[skill.skill_id],
        ))
    return packages
