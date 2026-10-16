"""Report readers of the execute-learn loop (layer 5, read-only).

Plan section 3's reader table (D3): three pure functions that turn a
run directory's on-disk state into the in-memory dict rows the learn
side's decisions consume. Read-only by construction -- no ledger
append, no store save, no new object kind -- and no path derivation of
their own: every ledger, store and round-directory path is K4's / K6's
own helper (the M2 cores' explicit-path seam), so a reader and the
writer it reads after can never disagree about where a file lives.

What the readers deliberately do NOT carry:

* live_report has no "the survivors' block equals the frozen audit
  line" field -- the no-op guard computes that comparison at ITS time
  point (after the revisions land; P7a); at the reader's decision time
  point it would be trivially true.
* The exam row's ``attempt`` IS the source selection (P7b): the most
  recent judged with-skill attempt, else the bare attempt -- the same
  rule ``composites.decision.exam_voided_rewrite_source`` names, so the
  G6 rewrite's weak answer and the leak-round source cannot drift
  between the reader and the loop.
"""

from typing import Any, Dict, List

from expedistill.blocks import metrics as metrics_block
from expedistill.blocks.rules import get_task_select
from expedistill.composites import decision
from expedistill.composites import live_acceptance as la
from expedistill.composites import shortfall
from expedistill.composites.acceptance import (
    REWRITE_FAILURE_LEDGER,
    _exam_attempts,
    _find_bare,
    _find_leak_verdict,
    _last_with_skill,
    _skill_records,
)
from expedistill.composites.skill_status import stage3_candidates
from expedistill.core import ids
from expedistill.core.models import Attempt, Trajectory
from expedistill.infra.store import Store, read_jsonl

__all__ = ["baseline_report", "exam_report", "live_report"]

#: The bare-baseline arms stage 1 executed (the D phase's two roles).
_ROLES = ("strong", "weak")

#: ``_last_with_skill`` cuts completed rounds off at a round bound; the
#: report wants the latest judged with-skill attempt of ANY round, and
#: no retry_round is ever >= infinity.
_ANY_ROUND = float("inf")


# ---------------------------------------------------------------------------
# baseline_report: the D phase's trajectory store + accepted shortfall
# ---------------------------------------------------------------------------

def baseline_report(store: Store, *, rule_name: str, n_attempts_strong: int,
                    n_attempts_weak: int) -> Dict[str, Any]:
    """Per-task rows of the D phase's bare baseline, plus the failure set.

    Reads the run-level trajectory store (both arms) and stage 1's
    accepted-shortfall record (a durable hole stays a hole and counts
    as infra, exactly as stage 1 scored it) and returns
    ``{"tasks": [...], "F": [...]}``:

    * one row per task, in task_id order -- ``{"task_id", "strong":
      {"success", "total"}, "weak": {...}}``, the counts D3 gives over
      each arm's FULL expected unit set (absent units count as infra,
      so ``total`` is the frozen sampling depth, never a silently
      shrunken denominator);
    * ``F``: the task_select rule's hits over the weak arm's counts
      (the same rule application stage 1 froze -- the name resolves
      through the named-rule registry, so swapping the criterion is a
      new name, not an edit here).

    The task universe is derived from the store (stored trajectories
    and accepted-hole unit ids), never assumed: a task whose every
    unit is an accepted hole has no trajectory but appears with an
    all-infra row, and can never reach F (every registered task_select
    rule requires valid > 0).
    """
    get_task_select(rule_name)  # unknown rule names fail loudly here
    accepted = set(shortfall.stage1_accepted_unit_ids(store.run_dir))

    trajectories = [store.load("trajectories", trajectory_id, Trajectory)
                    for trajectory_id in store.ids("trajectories")]
    universe = {traj.task_id for traj in trajectories}
    for unit_id in accepted:
        task_id, _, _ = ids.parse_trajectory_id(unit_id)
        universe.add(task_id)

    expected_strong = {task_id: n_attempts_strong for task_id in universe}
    expected_weak = {task_id: n_attempts_weak for task_id in universe}
    per_role: Dict[str, Dict[str, Dict[str, int]]] = {}
    for role, expected in (("strong", expected_strong), ("weak", expected_weak)):
        of_role = [traj for traj in trajectories
                   if traj.model_role == role
                   and traj.trajectory_id not in accepted]
        per_role[role] = metrics_block.compute_task_metrics(of_role, expected)

    rows = []
    for task_id in sorted(universe):
        row: Dict[str, Any] = {"task_id": task_id}
        for role in _ROLES:
            counts = per_role[role][task_id]
            row[role] = {"success": counts["success"],
                         "total": counts["attempts"]}
        rows.append(row)

    weak = per_role["weak"]
    failed = metrics_block.failed_tasks(
        weak, get_task_select(rule_name), n_attempts=n_attempts_weak)
    return {"tasks": rows, "F": failed}


# ---------------------------------------------------------------------------
# exam_report: the shared attempts store + leak verdicts + skill states
# ---------------------------------------------------------------------------

def exam_report(store: Store) -> Dict[str, Any]:
    """One row per stored stage-3 candidate, in skill_id order:
    ``{"skill_id", "version", "status", "bare_verdict", "leak_verdict",
    "attempt", "rewrite_exhausted"}``.

    * ``version`` / ``status``: the STORED skill's current state (the
      store is the resume truth; the object a caller may hold can be
      older than the disk);
    * ``bare_verdict``: the exam's judged bare attempt, True/False, or
      None when none is stored (a candidate whose exam is missing has
      no row content beyond its identity and state);
    * ``leak_verdict``: the stored first-verdict ledger row of the
      CURRENT (skill, version) pair, or None -- the audit is one judge
      call per (skill, version), first verdict sticks;
    * ``attempt``: the source the pending revision would rewrite from
      (P7b, ``composites.decision.exam_voided_rewrite_source``): the
      most recent judged with-skill attempt, else the bare attempt --
      ``{"attempt_id", "verdict", "judge_reason", "weak_answer",
      "skill_version", "retry_round"}``, the id being the source
      pointer -- or None. The L phase matches its "this version's
      attempt" on (skill_id, skill_version): an attempt of an older
      version is not this round's basis (review C-1);
    * ``rewrite_exhausted``: how many of the skill's rounds were
      consumed by an exhausted G6 budget (the REWRITE_FAILURE_LEDGER's
      ``exhausted`` rows -- the same count K4's round accounting uses).
    """
    rows = []
    for skill in stage3_candidates(store):
        attempts: List[Attempt] = []
        exam_id = ids.make_exam_id(skill.subtask_id)
        if store.exists("exams", exam_id):
            attempts = _exam_attempts(store, exam_id)
        bare = _find_bare(attempts)
        last_with_skill = _last_with_skill(attempts, skill.skill_id,
                                            _ANY_ROUND)
        source = decision.exam_voided_rewrite_source(
            last_with_skill=last_with_skill, bare=bare)
        attempt = None
        if source is not None:
            attempt = {"attempt_id": source.attempt_id,
                       "verdict": source.judge_verdict,
                       "judge_reason": source.judge_reason,
                       "weak_answer": source.weak_answer,
                       "skill_version": source.skill_version,
                       "retry_round": source.retry_round}
        exhausted = [record for record in _skill_records(
                         store, REWRITE_FAILURE_LEDGER, skill.skill_id)
                     if record.get("exhausted")]
        rows.append({
            "skill_id": skill.skill_id,
            "version": skill.version,
            "status": skill.status,
            "bare_verdict": bare.judge_verdict if bare is not None else None,
            "leak_verdict": _find_leak_verdict(store, skill.skill_id,
                                               skill.version),
            "attempt": attempt,
            "rewrite_exhausted": len(exhausted),
        })
    return {"skills": rows}


# ---------------------------------------------------------------------------
# live_report: the verdicts / attributions / injection_audit ledgers
# + the round store
# ---------------------------------------------------------------------------

def live_report(store: Store, *, round_: int) -> Dict[str, Any]:
    """One row per task group of ``round_``, in task_id order:
    ``{"task_id", "members", "baseline", "result", "better",
    "verdict", "attribution"}``.

    The universe is every group the round touched -- a frozen
    injection_audit row (dispatched) or a verdicts row (judged,
    including the zero-execution terminals). Per group:

    * ``members``: the group as injected this round, with versions --
      the frozen audit row's member list; a group the round never
      dispatched (the weak_already_knows short-circuit) falls back to
      its current pending members;
    * ``baseline``: the verdict row's baseline counts slot;
    * ``result``: the round store's stored per-attempt statuses, in
      attempt order (empty for a zero-execution round);
    * ``better``: the improvement rule's judgment (None on a
      zero-execution row);
    * ``verdict``: the zero-execution terminal's name, else None;
    * ``attribution``: the stored G10 plan row of this (task, round) --
      the already-landed plan a resumed learn phase must not re-ask --
      or None.
    """
    verdicts_path = la._ledger_path(store, la.VERDICTS_LEDGER)
    attributions_path = la._ledger_path(store, la.ATTRIBUTIONS_LEDGER)
    audit_path = la.round_dir(store, round_) / la.INJECTION_AUDIT_NAME

    universe = {row["task_id"] for row in read_jsonl(verdicts_path)
                if row.get("round") == round_}
    universe |= {row["task_id"] for row in read_jsonl(audit_path)}

    pending = la.pending_groups(store)
    rows = []
    for task_id in sorted(universe):
        audit = la._audit_row_from(audit_path, round_, task_id)
        if audit is not None:
            members = [{"skill_id": member["skill_id"],
                        "version": member["version"]}
                       for member in audit["members"]]
        else:
            members = [{"skill_id": skill.skill_id, "version": skill.version}
                       for skill in pending.get(task_id, [])]
        row = la._first_row_from(verdicts_path, task_id, round_)
        directory = la.round_dir(store, round_)
        result: List[str] = []
        if row is None or "verdict" not in row:
            # A judged round has a round store; a zero-execution terminal
            # has none (the group never entered K1).
            if directory.exists():
                result = [traj.status for traj in la._trajectories_from(
                    directory, task_id, round_)]
        rows.append({
            "task_id": task_id,
            "members": members,
            "baseline": row["baseline"] if row is not None else None,
            "result": result,
            "better": row["better"] if row is not None else None,
            "verdict": row.get("verdict") if row is not None else None,
            "attribution": la._first_row_from(attributions_path, task_id,
                                              round_),
        })
    return {"round": round_, "groups": rows}
