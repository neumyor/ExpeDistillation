"""K6 verdicts: judge a round, guard against no-ops, finalize the group
(layer 5).

The judging part of the former composites/live_acceptance.py, split out
mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md):
the round judgment and the verdicts ledger's append-once / recompute
compare, the zero-execution terminals, the no-op guard and the
finalization sweep's commit body. It reads the round store and the frozen
audit line through the measurement part (composites/live_measurement.py)
and commits statuses through ``composites.skill_status.discard_skill``;
the discard-reason vocabulary is composites.decision's (re-exported here
so the drivers and tests that read it off K6 keep one stop).

State is derived from disk only (decision section 2.4 -- no new state
objects):

* the verdict of round r: recomputed from the baseline counts x the
  round store x the named rule; a stored verdicts.jsonl first row for
  the (task, round) that disagrees with the recomputation is a
  CodeBugError (anti-tamper on the store);
* a group's terminal state: its members' statuses, completed idempotently
  by ``finalize_group`` (the finalization sweep's execution body).

Persistence order is what makes resume equal an uninterrupted run: the
verdict row is appended BEFORE the group's statuses are committed, so
the sweep can finish any commit the crash cut short.

The M2 de-rounding seam (plan section 3, D5/P5): the ``_``-prefixed
cores are round-agnostic -- every ledger path, round-store directory
and round label is an explicit parameter. The public functions are the
execute-phase driver's API (stages/phase_execute/live.py): thin shells
that compute today's exact paths and pass them to the cores. The seam
stays on purpose -- the tests drive the round-agnostic cores directly
with explicit paths, and the shells keep the frozen call shape the
driver uses.

Since R1 the acceptance verdict core is ``composites/verdicts.py`` (the
status/cause vocabulary, the named run-verdict rules, the one report
row). This module is K6's frozen verdicts-ledger protocol -- the ledger
rows are pinned, so the judgment here is deliberately unchanged; the
zero-execution terminals it writes are exactly the keys of verdicts'
terminal table, and the report reader (composites/reports.py, kind
"live") maps them onto the one acceptance report shape.
"""

from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
)

from expedistill.blocks.metrics import compute_task_metrics
from expedistill.blocks.rules import get_acceptance_improve
from expedistill.composites import decision
from expedistill.composites.live_measurement import (
    INJECTION_AUDIT_NAME,
    VERDICTS_LEDGER,
    ROUNDS,
    _check_round,
    first_row_from,
    ledger_path,
    _pending_group,
    _require_audit_row_from,
    _rows_from,
    _trajectories_from,
    _unit_ids,
    expected_units,
    round_dir,
)
from expedistill.composites.skill_status import discard_skill
from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store, append_jsonl

__all__ = [
    "LIVE_INJECTION_OVERFLOW",
    "LIVE_NO_IMPROVEMENT",
    "LIVE_REVISION_DELETED",
    "LIVE_UNDETERMINED",
    "MODEL_OUTPUT_ERROR",
    "WEAK_ALREADY_KNOWS",
    "finalize_group",
    "judge_round",
]

#: discard_reason values (decision ruling A13). Defined in
#: composites.decision since M3 (one definition shared with K4 and the
#: decision rules, no import cycle); the module-level names stay so the
#: drivers and tests that read them off K6 keep working. See decision.py
#: for the vocabulary's semantics.
WEAK_ALREADY_KNOWS = decision.WEAK_ALREADY_KNOWS
LIVE_INJECTION_OVERFLOW = decision.LIVE_INJECTION_OVERFLOW
LIVE_UNDETERMINED = decision.LIVE_UNDETERMINED
LIVE_NO_IMPROVEMENT = decision.LIVE_NO_IMPROVEMENT
LIVE_REVISION_DELETED = decision.LIVE_REVISION_DELETED
MODEL_OUTPUT_ERROR = decision.MODEL_OUTPUT_ERROR


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

def _judge_round(
    *,
    task_id: str,
    round_: int,
    rule_name: str,
    baseline_counts: Dict[str, int],
    n_attempts: int,
    skip_ids: Iterable[str],
    aux_scorer: Optional[Callable[[Trajectory], bool]] = None,
    terminal: Optional[str] = None,
    max_injection_tokens: Optional[int] = None,
    verdicts_path: Path,
    round_store_dir: Path,
    audit_path: Path,
) -> Dict[str, Any]:
    """The round-agnostic core of ``judge_round`` (module docstring):
    the judgment and the verdicts ledger's append-once / recompute
    compare, against the explicit ledger / round-store / audit paths.
    ``round_`` is a row label only -- written into the row, filtered
    by, named in errors -- never checked against a pinned round set."""
    get_acceptance_improve(rule_name)  # unknown rule names fail loudly here
    skip = set(skip_ids)
    expected_ids = _unit_ids(task_id, n_attempts)
    judged_with_shortfall = bool(skip.intersection(expected_ids))

    if terminal == LIVE_UNDETERMINED:
        # The one caller-side fact live_undetermined may be asserted for
        # is an unmeasured baseline (valid == 0): R2b made that a normal
        # reachable state (its stage-1 trials all came back without a
        # scorer verdict, or are all accepted holes), and with no control
        # to compare against no round can ever be judged -- the entry
        # guard terminalizes the group with zero executions. Verified
        # here so the zero-execution row cannot paper over a measurable
        # baseline; on the live side the terminal stays derived (below).
        if baseline_counts["valid"] != 0:
            raise CodeBugError(
                "live_undetermined is derived by judge_round (a live "
                "sample with no valid measurement); the one caller-side "
                "fact it may be asserted for is an unmeasured baseline "
                f"(valid == 0), and this baseline is {baseline_counts}")
        row = _verdict_row(task_id, round_, rule_name, baseline=None,
                           live=None, better=None,
                           verdict=LIVE_UNDETERMINED,
                           injection_tokens=None,
                           judged_with_shortfall=False, aux=None)
    elif terminal is not None and terminal not in (WEAK_ALREADY_KNOWS,
                                                  LIVE_INJECTION_OVERFLOW):
        raise CodeBugError(
            f"unknown zero-execution terminal {terminal!r}; expected one "
            f"of {(WEAK_ALREADY_KNOWS, LIVE_INJECTION_OVERFLOW)}")
    elif terminal == WEAK_ALREADY_KNOWS:
        if rule_name not in decision.STRICT_SHORT_CIRCUIT_RULES:
            raise CodeBugError(
                f"weak_already_knows short-circuit is limited to the "
                "strict-family rules "
                f"{sorted(decision.STRICT_SHORT_CIRCUIT_RULES)} "
                f"(decision section 2.2); rule {rule_name!r} may still "
                "improve on an all-success baseline, so the round must "
                "be executed")
        if (baseline_counts["valid"] <= 0
                or baseline_counts["success"] != baseline_counts["valid"]):
            raise CodeBugError(
                f"weak_already_knows asserted for task {task_id!r} with "
                f"baseline {baseline_counts}: the baseline is not "
                "all-success, so there is something to prove and the "
                "round must be executed")
        row = _verdict_row(task_id, round_, rule_name, baseline=None,
                           live=None, better=None, verdict=WEAK_ALREADY_KNOWS,
                           injection_tokens=None,
                           judged_with_shortfall=False, aux=None)
    elif terminal == LIVE_INJECTION_OVERFLOW:
        audit = _require_audit_row_from(audit_path, round_, task_id)
        if (max_injection_tokens is None
                or audit["injection_tokens"] <= max_injection_tokens):
            raise CodeBugError(
                f"live_injection_overflow asserted for task {task_id!r} "
                f"but the frozen audit row shows {audit['injection_tokens']}"
                f" tokens against a limit of {max_injection_tokens!r}: "
                "the overflow must be the audit row's own evidence")
        row = _verdict_row(task_id, round_, rule_name, baseline=None,
                           live=None, better=None,
                           verdict=LIVE_INJECTION_OVERFLOW,
                           injection_tokens=audit["injection_tokens"],
                           judged_with_shortfall=False, aux=None)
    else:
        trajectories = _trajectories_from(round_store_dir, task_id, round_)
        stored = {t.trajectory_id for t in trajectories}
        incomplete = [unit for unit in expected_ids
                      if unit not in stored and unit not in skip]
        if incomplete:
            # Judging an incomplete group would count its missing units as
            # infra and silently shrink the verdict; the stage judges only
            # complete groups (resume re-dispatches the rest).
            raise CodeBugError(
                f"round {round_} of task {task_id!r} is incomplete: "
                f"units {incomplete} are neither stored nor accepted "
                "holes")
        live_counts = compute_task_metrics(
            trajectories, expected_units(task_id, n_attempts),
            aux_scorer=aux_scorer)[task_id]
        audit = _require_audit_row_from(audit_path, round_, task_id)
        if (decision.live_all_holes(expected_ids, skip)
                or live_counts["valid"] == 0
                or baseline_counts["valid"] == 0):
            # No valid measurement on either side (decision section 2.5,
            # widened by R2b): every expected unit an accepted hole, every
            # stored run back without a scorer verdict, or a baseline
            # whose trials never came back scored. Completeness passed
            # above, so those are exactly the zero-valid cases -- there is
            # no denominator and no rule to run. The verdict is
            # live_undetermined, never a judgment over zero evidence (an
            # unscored trial is not a measurement of anything).
            row = _verdict_row(task_id, round_, rule_name, baseline=None,
                               live=None, better=None,
                               verdict=LIVE_UNDETERMINED,
                               injection_tokens=audit["injection_tokens"],
                               judged_with_shortfall=True, aux=None)
        else:
            rule = get_acceptance_improve(rule_name)
            better = bool(rule(baseline_counts, live_counts, n_attempts))
            row = _verdict_row(
                task_id, round_, rule_name,
                baseline={"valid": baseline_counts["valid"],
                          "success": baseline_counts["success"]},
                live={"valid": live_counts["valid"],
                      "success": live_counts["success"]},
                better=better, verdict=None,
                injection_tokens=audit["injection_tokens"],
                judged_with_shortfall=judged_with_shortfall,
                aux=_aux_slot(aux_scorer, baseline_counts, live_counts))

    stored_row = first_row_from(verdicts_path, task_id, round_)
    if stored_row is not None:
        if stored_row != row:
            raise CodeBugError(
                f"recomputed verdict for task {task_id!r} round {round_} "
                f"disagrees with the stored first row:\nstored: "
                f"{stored_row}\nrecomputed: {row}\nThe store or the "
                "caller's inputs changed under the ledger; refusing to "
                "re-judge")
        return stored_row
    append_jsonl(verdicts_path, row)
    return row


def judge_round(
    *,
    store: Store,
    task_id: str,
    round_: int,
    rule_name: str,
    baseline_counts: Dict[str, int],
    n_attempts: int,
    skip_ids: Iterable[str],
    aux_scorer: Optional[Callable[[Trajectory], bool]] = None,
    terminal: Optional[str] = None,
    max_injection_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Judge one group's round and append its verdicts.jsonl row; return
    the stored row (the first row for this (task, round) once written).

    The judgment itself is a pure function -- baseline counts (from
    ``load_baseline``) x the round store's counts (D3 under the full
    expected set, accepted holes included as infra) x the named rule from
    ACCEPTANCE_IMPROVE. A stored first row for the (task, round) is
    recomputed and compared: agreement returns the stored row unchanged
    (append-once, idempotent), disagreement is a CodeBugError -- the
    store was tampered with or the caller's inputs drifted, and either
    way silently re-judging would bury it (decision section 2.4).

    ``terminal`` records a zero-execution terminal instead of judging:
    ``weak_already_knows`` (the entry check found the baseline
    all-success AND the rule is one a rate-1.0 baseline can never lose
    to -- both verified here, else CodeBugError, so the strict-family
    limitation cannot be forgotten by a future rule) or
    ``live_injection_overflow`` (the frozen audit row's estimate exceeds
    ``max_injection_tokens`` -- verified here against the audit row, so
    the verdict row never claims an overflow its evidence does not
    show). ``live_undetermined`` has one caller-side use, verified here:
    an unmeasured baseline (valid == 0 -- R2b's normal reachable state;
    the entry guard terminalizes the group with zero executions).
    Otherwise it is DERIVED: a group whose round has no valid
    measurement (every expected unit an accepted hole, every stored run
    back without a scorer verdict, or the baseline unmeasured)
    short-circuits to it before any rule runs -- no judgment without a
    denominator. Zero-execution rows keep the schema's field set
    with the counts and ``better`` null and the terminal's name in a
    ``verdict`` field -- the verdicts file is the audit's complete fact
    source (decision section 2.4).

    The row is appended BEFORE any status is committed (``finalize_group``
    does the committing), so the finalization sweep can always finish a
    commit the crash cut short.

    Thin shell over ``_judge_round`` (module docstring): checks the
    pinned round and computes today's exact paths.
    """
    _check_round(round_)
    directory = round_dir(store, round_)
    return _judge_round(
        task_id=task_id, round_=round_, rule_name=rule_name,
        baseline_counts=baseline_counts, n_attempts=n_attempts,
        skip_ids=skip_ids, aux_scorer=aux_scorer, terminal=terminal,
        max_injection_tokens=max_injection_tokens,
        verdicts_path=ledger_path(store, VERDICTS_LEDGER),
        round_store_dir=directory,
        audit_path=directory / INJECTION_AUDIT_NAME)


def _verdict_row(task_id: str, round_: int, rule_name: str, *,
                 baseline: Optional[Dict[str, int]],
                 live: Optional[Dict[str, int]], better: Optional[bool],
                 verdict: Optional[str], injection_tokens: Optional[int],
                 judged_with_shortfall: bool,
                 aux: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One verdicts.jsonl row in the frozen shape (decision section 2.4).

    Judged rows carry exactly the schema's ten fields (``better`` decides
    them); zero-execution rows carry the same fields with the counts and
    ``better`` null plus the terminal's name in ``verdict`` -- key
    presence is what tells a consumer which kind it is reading."""
    row: Dict[str, Any] = {
        "schema": "live-verdict-v1",
        "task_id": task_id,
        "round": round_,
        "rule": rule_name,
        "baseline": baseline,
        "live": live,
        "better": better,
        "injection_tokens": injection_tokens,
        "judged_with_shortfall": judged_with_shortfall,
        "aux": aux,
    }
    if verdict is not None:
        row["verdict"] = verdict
    return row


def _aux_slot(
    aux_scorer: Optional[Callable[[Trajectory], bool]],
    baseline_counts: Dict[str, int],
    live_counts: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """The verdict row's aux slot (user ruling 11): the plugin-declared
    auxiliary metric's counts recorded ALONGSIDE the primary, never part
    of the criterion; null when the run declared no aux metric. Both
    arms are recorded so the audit can read either side's aux rate from
    the row alone."""
    if aux_scorer is None:
        return None
    if "aux_success" not in baseline_counts:
        # The wiring forgot the scorer on the baseline side; a null there
        # would fake a measured zero.
        raise CodeBugError(
            "judge_round got baseline counts without aux_success while an "
            "aux_scorer was given: load_baseline must be called with the "
            "same scorer")
    return {
        "baseline": {"valid": baseline_counts["valid"],
                     "aux_success": baseline_counts["aux_success"]},
        "live": {"valid": live_counts["valid"],
                 "aux_success": live_counts["aux_success"]},
    }


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------

def _finalize_group(store: Store, task_id: str, *, verdicts_path: Path,
                    rounds: Sequence[int]) -> Dict[str, Any]:
    """The round-agnostic core of ``finalize_group`` (module docstring):
    the verdicts file at ``verdicts_path`` decides over the round labels
    ``rounds`` (in order), through composites.decision.live_final_action
    (the finalization sweep's decision rule); this core owns the commit
    -- the statuses land in skill_id order, pending members only."""
    action = decision.live_final_action(
        _rows_from(verdicts_path, task_id), rounds)
    if action is None:
        return {"outcome": "pending", "reason": None, "committed": []}

    direction = ("accepted" if action.kind == decision.KEEP
                 else "discarded")
    reason = action.reason or ""
    committed: List[str] = []
    for skill in _pending_group(store, task_id):
        if direction == "accepted":
            skill.status = "accepted"
            store.save(skill, "skills")
        else:
            discard_skill(store, skill, reason)
        committed.append(skill.skill_id)
    return {"outcome": direction, "reason": reason if reason else None,
            "committed": committed}


def finalize_group(store: Store, task_id: str) -> Dict[str, Any]:
    """Commit one group's decided terminal state idempotently; return
    ``{"outcome", "reason", "committed"}``.

    This is the finalization sweep's execution body (decision section
    2.4): a group whose verdict row already decided its fate but whose
    status commits a crash cut short is finished here, and the in-flow
    calls after each verdict use the same function. The decision is
    read from the verdicts ledger, in this precedence:

    * a zero-execution row (weak_already_knows / live_injection_overflow
      / live_undetermined) discards every pending member with that
      reason;
    * a round-1 row accepts the survivors (better) or discards them as
      ``live_no_improvement`` (not better -- the round field is what
      tells this discard apart from the no-op guard's, ruling A13);
    * a round-0 row that is better accepts every pending member;
    * a round-0 row that is not better decides nothing -- the
      attribution/revision pipeline still owes the group work, and a
      sweep that "finished" it here would bury pending revisions.

    Commits land in skill_id order, pending members only: already
    terminal members keep their own truth, which is what makes a second
    call a no-op.

    Thin shell over ``_finalize_group`` (module docstring): computes
    today's exact path and passes the pinned rounds.
    """
    return _finalize_group(
        store, task_id,
        verdicts_path=ledger_path(store, VERDICTS_LEDGER),
        rounds=ROUNDS)
