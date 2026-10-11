"""D3 score statistics and the D4 rule application (layer 4).

Pure counting over Trajectory objects; no model, no disk. The metric
definitions follow the TB campaign's ``scripts/tb21/tb_stats.py`` so stage-1
and stage-6 numbers stay comparable with the reference pipeline:

* ``avg@N`` -- per task, average over that task's VALID trials first
  (success / valid), then average over tasks. Tasks whose trials were all
  infrastructure errors have no valid trial and contribute no rate; with no
  valid task at all the aggregate is ``None`` rather than a fake zero.
  (Deviation from tb_stats.py, which divides the rate sum by ALL tasks and
  thereby scores infra-only tasks as 0; the per-task-mean-then-task-mean over
  valid tasks is the definition this repo froze.)
* ``pass@N`` -- the share of tasks with at least one success among their
  valid trials; an infra-only task counts in the denominator but can never
  pass, matching tb_stats.py.

``infra_error`` is never a model failure (README 1.3, E1 row): it is counted
separately and excluded from valid/success everywhere.

The expected unit set: K1 never stores an infra-exhausted unit, so on real
data an infra error usually shows up as a MISSING trajectory, not as an
``infra_error`` one. Counting only what came back would let a task whose
every attempt exhausted infra vanish from all denominators (the conservative
avg@N would equal the conditional one, ``infra_tasks`` would always be empty,
and two arms could be compared over different task sets). Callers that know
which units they asked for pass ``expected`` (task_id -> n_attempts) and every
expected unit without a trajectory is counted as one infra attempt.

``failed_tasks`` applies a *named* task_select rule (``blocks/rules.py``) to
each task's metrics -- D4 is the application, the rule is the definition, so
redefining "failed" means registering a new named rule, not editing code here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Set

from expedistill.core.models import Trajectory

__all__ = ["compute_task_metrics", "summarize", "failed_tasks"]

#: Statuses that count as a real attempt of the model (README 1.3: infra
#: errors are excluded from scoring, never read as "the model failed").
VALID_STATUSES = ("success", "failure")


def compute_task_metrics(
    trajectories: List[Trajectory],
    expected: Optional[Mapping[str, int]] = None,
) -> Dict[str, Dict[str, int]]:
    """Count per-task attempts / valid / success / infra, keyed by raw task_id.

    Uses the task_id exactly as given on each Trajectory (benchmark-native,
    no prefixing). The four counts satisfy ``attempts == valid + infra``
    because Trajectory statuses are exactly success / failure / infra_error.

    ``expected`` (task_id -> n_attempts) is the unit set the caller asked
    for: every expected task gets an entry, and each of its attempts with no
    trajectory counts as ``infra`` (module docstring). A trajectory of a task
    outside ``expected`` is a ValueError -- the caller's unit set and its
    trajectories disagree, and silently widening the denominator would hide
    it. ``expected=None`` counts exactly the given trajectories.
    """
    if expected is not None:
        unexpected = sorted({traj.task_id for traj in trajectories}
                            - set(expected))
        if unexpected:
            raise ValueError(
                f"trajectories for tasks outside the expected unit set: "
                f"{unexpected}")
    metrics: Dict[str, Dict[str, int]] = {}
    seen: Dict[str, Set[int]] = {}
    for traj in trajectories:
        seen.setdefault(traj.task_id, set()).add(traj.attempt_index)
        per_task = metrics.setdefault(
            traj.task_id,
            {"attempts": 0, "valid": 0, "success": 0, "infra": 0})
        per_task["attempts"] += 1
        if traj.status in VALID_STATUSES:
            per_task["valid"] += 1
        if traj.status == "success":
            per_task["success"] += 1
        if traj.status == "infra_error":
            per_task["infra"] += 1
    for task_id, n_attempts in (expected or {}).items():
        per_task = metrics.setdefault(
            task_id, {"attempts": 0, "valid": 0, "success": 0, "infra": 0})
        present = {i for i in seen.get(task_id, ()) if 0 <= i < n_attempts}
        missing = n_attempts - len(present)
        per_task["attempts"] += missing
        per_task["infra"] += missing
    return metrics


def summarize(
    task_metrics: Dict[str, Dict[str, int]],
    n_attempts: int,
) -> Dict[str, Any]:
    """Aggregate per-task metrics into avg@N / pass@N (definitions above).

    ``n_attempts`` is the frozen sampling depth of the run. It is not used by
    the two aggregate definitions themselves; it is part of the signature so
    callers pass the frozen value alongside the metrics and future aggregates
    that depend on it (e.g. "share of tasks with a full valid sample") need no
    signature change. ``avg_at_n`` is None when no task has a valid trial.

    Two avg@N disciplines are reported side by side (pre-registered, never
    picked between after seeing results):
    - ``avg_at_n`` (conditional): mean over tasks with >=1 valid trial; tasks
      whose sampling collapsed entirely into infra errors are excluded.
    - ``avg_at_n_conservative`` (conservative): tb_stats.py's denominator —
      every task counts, a task with no valid trial contributes 0. Use this
      one when comparing against the TB21 campaign numbers; report both.
    """
    n_tasks = len(task_metrics)
    rates = [
        per_task["success"] / per_task["valid"]
        for per_task in task_metrics.values()
        if per_task["valid"] > 0
    ]
    conservative = sum(rates) / n_tasks if n_tasks else 0.0
    passed = [
        task_id for task_id, per_task in task_metrics.items()
        if per_task["success"] >= 1
    ]
    return {
        "tasks": n_tasks,
        "avg_at_n": sum(rates) / len(rates) if rates else None,
        "avg_at_n_conservative": conservative,
        "pass_at_n": len(passed) / n_tasks if n_tasks else 0.0,
        "infra_tasks": sorted(
            task_id for task_id, per_task in task_metrics.items()
            if per_task["infra"] > 0),
    }


def failed_tasks(
    task_metrics: Dict[str, Dict[str, int]],
    rule: Callable[[Dict[str, int], int], bool],
    n_attempts: int,
) -> List[str]:
    """Return the sorted task_ids for which ``rule(metrics, n_attempts)`` holds.

    D4: the caller resolves the named rule from ``blocks.rules`` (config names
    are identity there) and passes the callable here, so this block stays
    agnostic of which rule -- or which definition of "failed" -- is in effect.
    """
    return sorted(
        task_id for task_id, per_task in task_metrics.items()
        if rule(per_task, n_attempts))
