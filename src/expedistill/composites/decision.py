"""The execute-learn loop's decision rules (layer 5, pure functions).

Plan section 3's decision checklist (D10, the M3 acceptance baseline):
one small pure function per rule. Each answers ONE question -- what to
do next -- from facts the caller already read off disk; every store
write, ledger append and model call stays with the caller (K4, K6 and
the acceptance drivers), so the persistence order that makes resume
equal an uninterrupted run is untouched. I3: the module sits in
composites beside its callers on purpose -- no decision registry, no
new state object, no new ledger (skill changes keep going through the
existing commit helpers only), and no import of K4/K6 (they import
this module; the shared discard vocabulary therefore lives HERE, with
K6 re-exporting it, so there is one definition and no import cycle).

Rules return :class:`Action` (``kind`` is one of ``KEEP`` / ``DELETE``
/ ``REVISE``, the plan's verbs; ``reason`` carries the
``Skill.discard_reason`` vocabulary when the kind is ``DELETE``) or
``None`` when no rule fires and the caller's flow simply continues.
Boolean predicates answer yes/no criteria directly.

Checklist landing, item by item (exam 8 + live 9):

exam (K4 = composites/acceptance_submit.py; driver =
methods/acceptance/exam.py):

* bare answer correct -> delete(weak_already_knows):
  ``exam_bare_answer_action`` (K4's precheck).
* a judged with-skill attempt passing ON the stored version ->
  keep(accepted): ``exam_stored_pass_action`` (K4's resume precheck)
  and ``exam_answer_action`` (K4's in-round branch).
* any measurement budget exhausted (exam generation, answer, judge or
  leak audit) -> delete(model_output_error):
  ``exam_measurement_exhausted_action``; the disposition itself stays
  with the exam driver's unit worker -- the discard is the
  crash-recovery truth its ledger row completes, and the exception is
  the fact (there is no read state to decide from).
* leaked, a verification round left -> revise (the voided rewrite):
  ``exam_leak_action``; the rewrite's source selection (the most
  recent judged with-skill attempt, else the bare attempt -- P7b) is
  ``exam_voided_rewrite_source``.
* leaked, no round left -> delete(leak_audit): ``exam_leak_action``.
* wrong answer, a round left -> revise (judge_reason + weak_answer):
  ``exam_answer_action``.
* wrong answer, no round left -> delete(retry_exhausted):
  ``exam_answer_action`` (K4's empty-range fall-through discards with
  the same rule's reason).
* rewrite budget exhausted -> version unchanged, round consumed, the
  next round re-answers the same version: STAYS in K4's
  ``_rewrite_round`` -- the decision is fused with the per-try
  REWRITE_FAILURE_LEDGER appends that carry the budget across crashes
  (a resumed round continues the same budget); splitting them would
  break the resume protocol's round accounting.

live (K6 = composites/live_verdicts.py and its two siblings; driver =
methods/acceptance/live_run.py):

* group improved -> keep: ``live_final_action`` (the finalization
  sweep's decision, read from the verdicts ledger rows).
* miss with a verification round left -> the G10 per-skill plan
  applied as G11 revisions: the plan APPLICATION stays in K6's
  ``_apply_revisions`` -- each entry's version-replay guard (version ==
  from_version -> apply, +1 -> already landed, anything else ->
  CodeBugError) IS the resume protocol for a half-applied plan; a plan
  entry cannot be decided off the store.
* miss on the last round -> delete(live_no_improvement):
  ``live_final_action``.
* no-op guard criterion (the survivors' current block hashing equal to
  the round's frozen audit line) -> discard without running the next
  round: ``live_no_op_fires``; the guard itself -- its timing after the
  revisions land, and the discards -- stays with K6/the orchestration
  (P7a: computed at the reader's decision time the comparison would be
  trivially true).
* all-success baseline under a strict-family rule -> zero-execution
  weak_already_knows: ``live_zero_execution_short_circuit`` (the live
  driver's entry guard calls it; judge_round keeps enforcing the
  family limit).
* injection over max_injection_tokens -> live_injection_overflow:
  ``live_injection_overflows`` (both dispatch loops call it before K1).
* every expected unit an accepted hole -> live_undetermined:
  ``live_all_holes`` (K6's judging core short-circuits before any rule
  runs -- no judgment without a live denominator).
* G10 budget exhausted -> the whole group discarded
  (model_output_error): ``live_attribution_exhausted`` covers the
  ledger-read branch (a stored exhausted row, or a full budget of
  failed tries); the in-loop exhaustion branch stays in K6's
  ``_attribute_group`` -- the terminal discards land BEFORE the
  exhausted receipt row, and that order is the resume protocol.
* G11 budget exhausted -> only that skill discarded, the group
  continues (model_output_error): STAYS in K6's ``_revise_one`` for
  the same reason -- the per-try REVISION_FAILURES_LEDGER appends and
  the discard-then-receipt order are the crash window's only durable
  accounting.

"Verification round left" maps to K4's ``last_round`` today; in the
phase world it is the count of A/B-kind execution phases after this
learn phase (plan section 3, P1/I8 -- counting learn phases would give
the wrong action under a legal [D, L, L] sequence). The callers pass
whichever fact they hold.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from expedistill.core.models import Attempt

__all__ = [
    "DELETE",
    "KEEP",
    "LEAK_AUDIT",
    "LIVE_INJECTION_OVERFLOW",
    "LIVE_NO_IMPROVEMENT",
    "LIVE_REVISION_DELETED",
    "LIVE_UNDETERMINED",
    "MODEL_OUTPUT_ERROR",
    "RETRY_EXHAUSTED",
    "REVISE",
    "STRICT_SHORT_CIRCUIT_RULES",
    "WEAK_ALREADY_KNOWS",
    "Action",
    "exam_answer_action",
    "exam_bare_answer_action",
    "exam_leak_action",
    "exam_measurement_exhausted_action",
    "exam_stored_pass_action",
    "exam_voided_rewrite_source",
    "live_all_holes",
    "live_attribution_exhausted",
    "live_final_action",
    "live_injection_overflows",
    "live_no_op_fires",
    "live_zero_execution_short_circuit",
]

#: Action kinds: the plan section 3 verbs (keep / delete / revise).
KEEP = "keep"
DELETE = "delete"
REVISE = "revise"

#: The discard-reason vocabulary (decision ruling A13; the free-form
#: Skill.discard_reason field carries it). ``weak_already_knows`` crosses
#: modes (exam: the bare answer was right; live: the baseline was
#: all-success); ``live_no_improvement`` is dual (no-op guard vs
#: last-round miss) and the verdicts row's round tells them apart.
#: Defined here since M3 so K4, K6 and this module share one
#: definition (composites.live_verdicts re-exports them unchanged).
WEAK_ALREADY_KNOWS = "weak_already_knows"
LIVE_INJECTION_OVERFLOW = "live_injection_overflow"
LIVE_UNDETERMINED = "live_undetermined"
LIVE_NO_IMPROVEMENT = "live_no_improvement"
LIVE_REVISION_DELETED = "live_revision_deleted"
MODEL_OUTPUT_ERROR = "model_output_error"

#: Exam-only discard reasons (K4's own two; no other mode uses them).
LEAK_AUDIT = "leak_audit"
RETRY_EXHAUSTED = "retry_exhausted"

#: Rules for which "the baseline is all-success" mathematically implies
#: "the live arm cannot be better", so the weak_already_knows zero-
#: execution short-circuit may fire (decision section 2.2: strict-family
#: only). Registering a new improve rule must consciously revisit this
#: set -- an unconditional guard would short-circuit groups under a rule
#: where a rate-1.0 baseline can still lose (pass@N-style rules).
STRICT_SHORT_CIRCUIT_RULES = frozenset({"strict_rate_gain",
                                        "first_success_gain"})


@dataclass(frozen=True)
class Action:
    """One rule's verdict: what the caller does next.

    ``kind`` is one of ``KEEP`` / ``DELETE`` / ``REVISE``; ``reason`` is
    the Skill.discard_reason when the kind is ``DELETE``, else None.
    """

    kind: str
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Exam rules (K4)
# ---------------------------------------------------------------------------

def exam_bare_answer_action(bare_verdict: Optional[bool]) -> Optional[Action]:
    """A correct bare answer means the weak model already knows the exam,
    so the skill cannot teach it anything -> delete(weak_already_knows).
    None (no rule fired) when the bare answer was wrong or absent."""
    if bare_verdict is True:
        return Action(DELETE, WEAK_ALREADY_KNOWS)
    return None


def exam_stored_pass_action(passed_on_stored_version: bool) -> Optional[Action]:
    """A judged with-skill attempt that passed ON the stored version ->
    keep(accepted) (an attempt on any other version proves nothing
    about the stored content). None when no such attempt exists."""
    if passed_on_stored_version:
        return Action(KEEP)
    return None


def exam_leak_action(*, last_round: bool) -> Action:
    """A leaked version: the round with no verification round after it
    has no budget left to fix it -> delete(leak_audit); any earlier
    round consumes itself with the voided rewrite (revise -- the
    version is never examined, the audit's reason travels into the
    rewrite description)."""
    if last_round:
        return Action(DELETE, LEAK_AUDIT)
    return Action(REVISE)


def exam_answer_action(*, verdict: bool, last_round: bool) -> Action:
    """The round's judged with-skill attempt: a pass -> keep(accepted);
    a wrong answer with a verification round left -> revise (the
    rewrite carries the judge reason and the weak answer); a wrong
    answer on the last round -> delete(retry_exhausted)."""
    if verdict:
        return Action(KEEP)
    if last_round:
        return Action(DELETE, RETRY_EXHAUSTED)
    return Action(REVISE)


def exam_voided_rewrite_source(
        *, last_with_skill: Optional[Attempt],
        bare: Optional[Attempt]) -> Optional[Attempt]:
    """The voided rewrite's source attempt (P7b): the most recent judged
    with-skill attempt of an already completed round -- with none (a
    round-0 leak) the bare attempt, whose judge reason still says how
    the weak model misunderstood the question."""
    return last_with_skill if last_with_skill is not None else bare


def exam_measurement_exhausted_action() -> Action:
    """Any measurement's repair budget exhausted (exam generation, the
    answer, the judge or the leak audit) -> delete(model_output_error):
    the skill is terminally discarded -- resampling the same broken
    contract would only burn tokens. The caller owns the
    terminal-write-first protocol around this verdict (the discard is
    the truth a resume reads; the failure ledger row is its receipt)."""
    return Action(DELETE, MODEL_OUTPUT_ERROR)


# ---------------------------------------------------------------------------
# Live rules (K6)
# ---------------------------------------------------------------------------

def live_final_action(rows: Sequence[Dict[str, Any]],
                      rounds: Sequence[int]) -> Optional[Action]:
    """The group's final decision from its verdict rows, in the
    finalization sweep's precedence: a zero-execution row (a ``verdict``
    key) discards with that reason; otherwise the latest round with a
    row is decisive -- better keeps the group, a miss on the LAST round
    discards live_no_improvement -- and a better row of an earlier
    round keeps while a miss there decides nothing (the revision
    pipeline still owes the group work).

    ``rows`` are the group's verdicts rows in append order (the first
    row per (task, round) is the one that sticks); ``rounds`` is the
    round-label sequence in order. None when no row decides anything.
    """
    action: Optional[Action] = None
    for row in rows:
        if "verdict" in row:
            action = Action(DELETE, row["verdict"])
            break
    if action is None:
        for round_label in reversed(rounds):
            of_round = [row for row in rows
                        if row.get("round") == round_label]
            if not of_round:
                continue
            if round_label == rounds[-1]:
                if of_round[0]["better"]:
                    action = Action(KEEP)
                else:
                    action = Action(DELETE, LIVE_NO_IMPROVEMENT)
            elif of_round[0]["better"]:
                action = Action(KEEP)
            break
    return action


def live_no_op_fires(*, rendered_block_sha: str,
                     frozen_audit_sha: str) -> bool:
    """The no-op guard's criterion: the survivors' CURRENT rendered
    block hashing equal to the round's frozen audit line means the
    revisions changed nothing -- running the next round would only
    re-measure the same injection, so the group is discarded without
    it. The comparison's data source is the frozen line, never a
    re-render of the pre-revision state (that state is gone), and its
    timing (after the revisions land) is the guard's own (P7a)."""
    return rendered_block_sha == frozen_audit_sha


def live_zero_execution_short_circuit(
        *, rule_name: str,
        baseline_counts: Mapping[str, int]) -> bool:
    """An all-success baseline under a rule a rate-1.0 baseline can
    never lose to (the strict family) -> the group is terminalized
    weak_already_knows with zero executions: there is nothing the
    injection could prove. A pass@N-style rule can still improve on
    rate 1.0 and never short-cuits. Whether the round was already
    judged is the caller's resume fact, not part of the criterion;
    judge_round re-verifies both halves before writing the row."""
    return (rule_name in STRICT_SHORT_CIRCUIT_RULES
            and baseline_counts["success"] == baseline_counts["valid"])


def live_injection_overflows(*, max_injection_tokens: Optional[int],
                             injection_tokens: int) -> bool:
    """The injection's D10 token estimate over ``max_injection_tokens``:
    the group never enters K1 -- the overflow verdict carries the
    frozen audit row's own token evidence and terminalizes the group
    as live_injection_overflow with zero executions."""
    return (max_injection_tokens is not None
            and injection_tokens > max_injection_tokens)


def live_all_holes(expected_ids: Sequence[str],
                   skip_ids: Sequence[str]) -> bool:
    """Every expected unit of the round is an accepted hole: nothing
    was executed, there is no live denominator and no rule to run --
    the round is live_undetermined, never a judgment on an empty
    sample."""
    skip = set(skip_ids)
    return all(unit in skip for unit in expected_ids)


def live_attribution_exhausted(failure_rows: List[Dict[str, Any]],
                               budget: int) -> bool:
    """The G10 repair budget is spent: an exhausted ledger row (a
    previous call completed the exhaustion -- the discards are the
    truth, the line their receipt) or a full budget of failed tries
    means the whole group is terminally discarded as
    model_output_error. The in-loop exhaustion branch (which lands the
    discards BEFORE the exhausted receipt row) stays with K6; this is
    the ledger-read half a resumed call decides from."""
    return (any(row.get("exhausted") for row in failure_rows)
            or len(failure_rows) >= budget)
