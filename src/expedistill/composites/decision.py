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

R3 (repair plan, item R3; boundary doc sections 2.4, 3, 4): the phase
world's ONE decision entry is :func:`decide_task_list` -- (this round's
report, the control report, the current bank, how many checks are left)
-> task list -- with the comparison rules as named parameters (default
``strict_rate_gain``) and the section 4.2 action table as
:func:`table_action`. The exam_*/live_* rules above are the LEGACY
per-mode rules the still-unwired K4/K6 shells call; they retire with the
Wave 2 phase drivers (their callers -- acceptance_submit, live_verdicts,
live_attribution, reports, the two acceptance drivers -- are other
agents' frozen files, so removing them now would break a tree this wave
must keep green). The other half of G3 moves NOW: stage 6's delta
computation and control-source choice are :func:`final_test_delta` and
:func:`control_arm_source` here, so no execution stage compares arms
anymore.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from expedistill.blocks.rules import get_task_select
from expedistill.core import ids
from expedistill.core.contracts import (
    AcceptanceReportRow,
    ExecutionUnitRow,
    TaskListRow,
)
from expedistill.core.models import Attempt, MODEL_ROLES, Skill
from expedistill.infra.reliability import CodeBugError, ConfigError

__all__ = [
    "ADD",
    "BASELINE_GAP_REASON",
    "CAUSE_ALL_HOLES",
    "CAUSE_EXAM_GENERATION_FAILED",
    "CAUSE_INJECTION_OVERFLOW",
    "CAUSE_INVALID_REDISPATCH_EXHAUSTED",
    "CAUSE_LEAK",
    "CAUSE_ZERO_EXECUTION",
    "CHECK_PHASE_KINDS",
    "CONCLUSION_BASELINE_GAP",
    "CONCLUSION_IMPROVED",
    "CONCLUSION_NO_GAIN",
    "CONCLUSION_NO_HEADROOM",
    "CONCLUSION_NOT_TESTABLE",
    "DELETE",
    "FinalTestReportRejected",
    "KEEP",
    "LEAK_AUDIT",
    "LIVE_INJECTION_OVERFLOW",
    "LIVE_NO_IMPROVEMENT",
    "LIVE_REVISION_DELETED",
    "LIVE_UNDETERMINED",
    "MODEL_OUTPUT_ERROR",
    "NOT_TESTABLE_CAUSES",
    "PHASE_BASELINE",
    "PHASE_EXAM",
    "PHASE_KINDS",
    "PHASE_LEARN",
    "PHASE_LIVE",
    "PHASE_TEST",
    "RETRY_EXHAUSTED",
    "REVISE",
    "ROW_NOT_RUN_CONTROL_PERFECT",
    "ROW_NOT_TESTABLE",
    "ROW_NORMAL",
    "STRICT_SHORT_CIRCUIT_RULES",
    "WEAK_ALREADY_KNOWS",
    "Action",
    "compare_conclusion",
    "control_arm_source",
    "control_perfect_skips_run",
    "decide_task_list",
    "exam_answer_action",
    "exam_bare_answer_action",
    "exam_leak_action",
    "exam_measurement_exhausted_action",
    "exam_stored_pass_action",
    "exam_voided_rewrite_source",
    "final_test_delta",
    "first_success_gain",
    "live_all_holes",
    "live_attribution_exhausted",
    "live_final_action",
    "live_injection_overflows",
    "live_no_op_fires",
    "live_zero_execution_short_circuit",
    "remaining_checks",
    "reuse_key",
    "reusable_units",
    "revise_outcome",
    "strict_rate_gain",
    "table_action",
]

#: Action kinds: the plan section 3 verbs (keep / delete / revise), plus
#: the unified table's add (a task list may grow a skill, not only act on
#: existing ones; R3).
KEEP = "keep"
DELETE = "delete"
REVISE = "revise"
ADD = "add"

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

    ``kind`` is one of ``KEEP`` / ``DELETE`` / ``REVISE`` (the unified
    table also yields ``ADD``); ``reason`` is the Skill.discard_reason
    when the kind is ``DELETE``, else None.
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


# ---------------------------------------------------------------------------
# R3: the unified decision entry (the phase world's task list)
# ---------------------------------------------------------------------------

#: The phase-kind vocabulary (boundary doc sections 1.1 and 1.3): one
#: kind per semantic phase role. The config's phase sequence (R9) maps
#: onto these names; decision never sees a phase label -- labels are
#: bookkeeping (unit ids, report rows), kinds are semantics.
PHASE_BASELINE = "baseline"  # D: the two-arm bare baseline
PHASE_EXAM = "exam"          # A: paper acceptance, one skill per exam
PHASE_LIVE = "live"          # B: live acceptance, a task's whole group
PHASE_TEST = "test"          # C: the final test -- never reaches decision
PHASE_LEARN = "learn"        # L: a learn phase

PHASE_KINDS = frozenset({PHASE_BASELINE, PHASE_EXAM, PHASE_LIVE,
                         PHASE_TEST, PHASE_LEARN})

#: Which execution phases count as "a later check" (boundary doc section
#: 4.2): acceptance on the train set only. The final test C never counts
#: (section 3 rule 9), and neither do further learn phases.
CHECK_PHASE_KINDS = frozenset({PHASE_EXAM, PHASE_LIVE})

#: Acceptance-report row statuses: the values core/contracts.py freezes
#: in AcceptanceReportRow.status's comment, named for the entry's
#: consumption side.
ROW_NORMAL = "normal"
ROW_NOT_TESTABLE = "not_testable"
ROW_NOT_RUN_CONTROL_PERFECT = "not_run_control_perfect"

#: The fixed ``cause`` set R0 places in this module (core/contracts.py
#: names composites.decision as its definition site): why a row is
#: ``not_testable`` (or, for ``zero_execution``, the weak_already_knows
#: short-circuit's zero-run marker).
CAUSE_LEAK = "leak"
CAUSE_INJECTION_OVERFLOW = "injection_overflow"
CAUSE_EXAM_GENERATION_FAILED = "exam_generation_failed"
CAUSE_INVALID_REDISPATCH_EXHAUSTED = "invalid_redispatch_exhausted"
CAUSE_ALL_HOLES = "all_holes"
CAUSE_ZERO_EXECUTION = "zero_execution"

NOT_TESTABLE_CAUSES = frozenset({
    CAUSE_LEAK,
    CAUSE_INJECTION_OVERFLOW,
    CAUSE_EXAM_GENERATION_FAILED,
    CAUSE_INVALID_REDISPATCH_EXHAUSTED,
    CAUSE_ALL_HOLES,
    CAUSE_ZERO_EXECUTION,
})

#: The four comparison conclusions (boundary doc section 4.1) plus the
#: baseline gap (section 4.2's last row -- decided from the D report,
#: not from a comparison).
CONCLUSION_IMPROVED = "improved"
CONCLUSION_NO_GAIN = "no_gain"
CONCLUSION_NO_HEADROOM = "no_headroom"
CONCLUSION_NOT_TESTABLE = "not_testable"
CONCLUSION_BASELINE_GAP = "baseline_gap"

#: The baseline row's task-list reason: what makes a task an add.
BASELINE_GAP_REASON = "weak_fails_and_no_skill"

#: Why a not-testable unit is deleted, per cause (the discard_reason
#: vocabulary). ``leak`` reaches this table only through its revise
#: split; ``zero_execution`` is the weak_already_knows short-circuit's
#: zero-run marker, so its delete keeps that reason.
_CAUSE_DELETE_REASONS = {
    CAUSE_LEAK: LEAK_AUDIT,
    CAUSE_INJECTION_OVERFLOW: LIVE_INJECTION_OVERFLOW,
    CAUSE_EXAM_GENERATION_FAILED: MODEL_OUTPUT_ERROR,
    CAUSE_INVALID_REDISPATCH_EXHAUSTED: LIVE_UNDETERMINED,
    CAUSE_ALL_HOLES: LIVE_UNDETERMINED,
    CAUSE_ZERO_EXECUTION: WEAK_ALREADY_KNOWS,
}


class FinalTestReportRejected(CodeBugError):
    """A final-test (C-phase) report was handed to the decision entry.

    Boundary doc section 3 rule 9: test results do not flow into
    learning. The final summary computes the with-bank delta itself
    (:func:`final_test_delta`); a C-phase report reaching decision is a
    wiring bug, not data.
    """


def remaining_checks(phase_kinds: Sequence[str], learn_index: int) -> int:
    """How many A/B-kind execution phases follow the learn phase at
    ``learn_index`` (boundary doc section 4.2's "a later check" column).

    ``phase_kinds`` is the method's whole frozen phase sequence as kind
    strings (the vocabulary above); the final test C and further learn
    phases never count. Unknown kinds and out-of-range indices are
    CodeBugError: the sequence is config-frozen, so a mismatch means the
    caller's wiring disagrees with the frozen config.
    """
    unknown = [kind for kind in phase_kinds if kind not in PHASE_KINDS]
    if unknown:
        raise CodeBugError(
            f"unknown phase kind(s) {unknown!r} in the phase sequence; "
            f"expected values from {sorted(PHASE_KINDS)}")
    if not 0 <= learn_index < len(phase_kinds):
        raise CodeBugError(
            f"learn_index {learn_index} is outside the phase sequence of "
            f"{len(phase_kinds)} phase(s)")
    if phase_kinds[learn_index] != PHASE_LEARN:
        raise CodeBugError(
            f"phase {learn_index} is {phase_kinds[learn_index]!r}, not a "
            "learn phase")
    return sum(1 for kind in phase_kinds[learn_index + 1:]
               if kind in CHECK_PHASE_KINDS)


def strict_rate_gain(current: Mapping[str, int],
                     control: Mapping[str, int]) -> bool:
    """Default compare rule: the unit's success RATE strictly exceeds the
    control's.

    Compared by integer cross-multiplication, never a float division
    (exact in small integers; the arms may have different valid
    denominators). No valid run on either side is "not better", never a
    crash. The same math as the registered live rule of the same name in
    blocks/rules.py -- deliberately restated here: the comparison is a
    decision PARAMETER (boundary doc section 2.4), not a named-rule seam,
    and the entry must stay independent of that registry.
    """
    if current["valid"] == 0 or control["valid"] == 0:
        return False
    return (current["success"] * control["valid"]
            > control["success"] * current["valid"])


def first_success_gain(current: Mapping[str, int],
                       control: Mapping[str, int]) -> bool:
    """The pass@N-flavoured alternative: the control never succeeded and
    the unit did at least once (weaker than strict_rate_gain on purpose --
    running the two rules against each other measures exactly that)."""
    return current["success"] >= 1 and control["success"] == 0


#: Selection of a compare rule by parameter name: a fixed two-entry
#: table, not a registry (repair plan section 5, note 19: no decision-
#: rule registry). Adding a rule means adding a named function above and
#: one line here.
_COMPARE_RULES = {
    "strict_rate_gain": strict_rate_gain,
    "first_success_gain": first_success_gain,
}


def _get_compare_rule(name: str):
    try:
        return _COMPARE_RULES[name]
    except KeyError:
        raise ConfigError(
            f"unknown compare rule {name!r}; the decision entry knows "
            f"{sorted(_COMPARE_RULES)}") from None


def _control_is_perfect(compare_rule: str, control_valid: int,
                        control_success: int) -> bool:
    # Rule 3's criterion and the no-headroom conclusion share one
    # definition so the skip and the verdict can never disagree.
    return (compare_rule in STRICT_SHORT_CIRCUIT_RULES
            and control_valid > 0
            and control_success == control_valid)


def control_perfect_skips_run(*, compare_rule: str, control_valid: int,
                              control_success: int) -> bool:
    """Boundary doc section 4.3 rule 3: under a rule a rate-1.0 control
    can never lose to (the strict family), an all-success control means
    the with-skill run cannot conclude "improved", so the execution phase
    may skip that run and report the unit ``not_run_control_perfect`` --
    decision then concludes "no headroom" off that status. This saves
    calls only; under any other rule it is False and the run happens."""
    return _control_is_perfect(compare_rule, control_valid, control_success)


def compare_conclusion(*, row: AcceptanceReportRow, control_valid: int,
                       control_success: int, control_cause: str,
                       compare_rule: str) -> Tuple[str, str]:
    """One unit's section 4.1 conclusion from its report row and its
    control's counts; returns ``(conclusion, cause)``.

    Precedence:

    * ``not_run_control_perfect`` (the section 4.3 rule-3 skip) -> no
      headroom; the row consumed the skip, the conclusion follows it;
    * ``not_testable`` -> the not-testable conclusion with the row's
      cause (the fixed vocabulary above; an unknown cause is a
      CodeBugError -- the set is frozen, a new value needs this module);
    * an unmeasurable control (no valid run) -> not testable, carrying
      the CONTROL's cause as the reason there is nothing to compare
      against;
    * an all-success control under a strict-family rule -> no headroom
      (the same criterion as rule 3's skip);
    * otherwise the compare rule decides improved vs no gain.
    """
    rule = _get_compare_rule(compare_rule)
    if row.status == ROW_NOT_RUN_CONTROL_PERFECT:
        return CONCLUSION_NO_HEADROOM, ""
    if row.status == ROW_NOT_TESTABLE:
        if row.cause not in NOT_TESTABLE_CAUSES:
            raise CodeBugError(
                f"unit {row.unit_id!r} reports cause {row.cause!r}, which "
                f"is not in the frozen not-testable set "
                f"{sorted(NOT_TESTABLE_CAUSES)}")
        return CONCLUSION_NOT_TESTABLE, row.cause
    if row.status != ROW_NORMAL:
        raise CodeBugError(
            f"unit {row.unit_id!r} reports unknown status "
            f"{row.status!r}; the frozen set is "
            f"{[ROW_NORMAL, ROW_NOT_TESTABLE, ROW_NOT_RUN_CONTROL_PERFECT]}")
    if row.valid_count == 0:
        raise CodeBugError(
            f"unit {row.unit_id!r} reports status normal with no valid "
            "run; acceptance must report not_testable instead")
    if control_valid == 0:
        if control_cause not in NOT_TESTABLE_CAUSES:
            raise CodeBugError(
                f"the control of unit {row.unit_id!r} has no valid run "
                f"and no not-testable cause (got {control_cause!r}); "
                "acceptance must report the control's cause")
        return CONCLUSION_NOT_TESTABLE, control_cause
    if _control_is_perfect(compare_rule, control_valid, control_success):
        return CONCLUSION_NO_HEADROOM, ""
    better = rule({"valid": row.valid_count, "success": row.success_count},
                  {"valid": control_valid, "success": control_success})
    return (CONCLUSION_IMPROVED if better else CONCLUSION_NO_GAIN), ""


def table_action(*, conclusion: str, cause: str = "", checks_left: int,
                 phase_kind: str) -> Action:
    """The boundary doc section 4.2 action table, one row at a time:
    (conclusion x whether a check remains) -> action.

    * improved -> keep either way;
    * no gain -> revise while a check remains, else delete -- the exam
      kind keeps its historical reason (retry_exhausted) and the live
      kind its (live_no_improvement);
    * no headroom -> delete(weak_already_knows) either way;
    * not testable, leaked -> the voided rewrite (revise) while a check
      remains, else delete(leak_audit);
    * not testable, any other cause -> delete with the cause's reason
      (without a measurement there is nothing a rewrite could repair);
    * the baseline gap (the D-report row: the weak model fails the task
      and it has no skill yet) -> add, either way.
    """
    if checks_left < 0:
        raise CodeBugError(f"checks_left must be >= 0, got {checks_left}")
    if conclusion == CONCLUSION_IMPROVED:
        return Action(KEEP, CONCLUSION_IMPROVED)
    if conclusion == CONCLUSION_NO_GAIN:
        if checks_left > 0:
            return Action(REVISE, CONCLUSION_NO_GAIN)
        reason = (RETRY_EXHAUSTED if phase_kind == PHASE_EXAM
                  else LIVE_NO_IMPROVEMENT)
        return Action(DELETE, reason)
    if conclusion == CONCLUSION_NO_HEADROOM:
        return Action(DELETE, WEAK_ALREADY_KNOWS)
    if conclusion == CONCLUSION_NOT_TESTABLE:
        if cause == CAUSE_LEAK and checks_left > 0:
            return Action(REVISE, CAUSE_LEAK)
        try:
            return Action(DELETE, _CAUSE_DELETE_REASONS[cause])
        except KeyError:
            raise CodeBugError(
                f"unknown not-testable cause {cause!r}") from None
    if conclusion == CONCLUSION_BASELINE_GAP:
        return Action(ADD, BASELINE_GAP_REASON)
    raise CodeBugError(f"unknown conclusion {conclusion!r}")


def _task_of_bank_skill(skill: Skill) -> Optional[str]:
    """The task a bank skill's ID chain names (None when the chain
    carries no task -- never true of a bank row: even a merge product
    anchors on a real subtask)."""
    try:
        trajectory_id, _ = ids.parse_subtask_id(skill.subtask_id)
        return ids.parse_trajectory_id(trajectory_id)[0]
    except ValueError:
        return None


#: A unit id's sha field (core/contracts.py: the first eight hex chars of
#: the sha256 over the canonical injected skills) and its phase label's
#: ``pNN`` head (R9: labels are ``pNN:<local name>``).
_UNIT_SHA_RE = re.compile(r"[0-9a-f]{8}")
_PHASE_HEAD_RE = re.compile(r"p[0-9]+")


def _parse_unit_id(unit_id: str) -> Tuple[str, str]:
    """(task_or_exam_id, role) out of a unit id, per core/contracts.py's
    frozen construction rule ``phase:benchmark:task:role:sha8``.

    Parsed from BOTH ends because the middle swallows colons: task ids
    carry their benchmark prefix (``tb:t1``) and exam ids carry a whole
    trajectory chain, while the phase label is ``pNN:<local>`` (one
    colon) and the benchmark carries none -- so the task id is everything
    between the third colon and the role. Anything malformed is a
    CodeBugError: the id is contract, and a report that disagrees with
    it is a wiring bug, never data to guess with.

    ExecutionUnitRow carries ``task_ref`` as a field and needs no
    parsing; the report row does not (its shape is frozen), so this
    parser is the report-side join.
    """
    try:
        head, role, sha = unit_id.rsplit(":", 2)
    except ValueError:
        raise CodeBugError(f"malformed unit id {unit_id!r}") from None
    prefix = head.split(":", 3)
    if (len(prefix) != 4
            or not _PHASE_HEAD_RE.fullmatch(prefix[0])
            or role not in MODEL_ROLES
            or not _UNIT_SHA_RE.fullmatch(sha)):
        raise CodeBugError(f"malformed unit id {unit_id!r}")
    return prefix[3], role


def _exam_target(row: AcceptanceReportRow) -> Tuple[str, str]:
    """The skill an exam-phase unit measures: the exam's own subtask's
    stage-3 skill, from the ID chain (the report's injected refs are
    evidence, not targeting -- the exam id is the address)."""
    exam_id, _ = _parse_unit_id(row.unit_id)
    if not exam_id.endswith("/exam"):
        raise CodeBugError(
            f"exam-phase unit {row.unit_id!r} names {exam_id!r}, which is "
            "not an exam id")
    return "skill", ids.make_skill_id(exam_id[: -len("/exam")])


def _baseline_rows(*, control_kind: Optional[str],
                   control_report: Optional[Sequence[AcceptanceReportRow]],
                   bank: Sequence[Skill], n_attempts_weak: int,
                   task_select_rule: str) -> List[TaskListRow]:
    """The section 4.2 baseline row: the first learn phase follows the D
    phase, no acceptance phase has run yet, so this round's report is
    empty and the D-phase report acts as the input. Every train task the
    weak model fails (the named task-select rule over its weak rows) and
    that has no skill in the bank yet gets one add row."""
    rows = list(control_report or ())
    if control_kind != PHASE_BASELINE or not rows:
        raise CodeBugError(
            "the empty-current-report path (the first learn phase) needs "
            "the D-phase report as the control: got control_kind="
            f"{control_kind!r} with {len(rows)} row(s)")
    rule = get_task_select(task_select_rule)
    covered = {task for task in (_task_of_bank_skill(skill) for skill in bank)
               if task is not None}
    out: List[TaskListRow] = []
    for row in sorted((r for r in rows if r.role == "weak"),
                      key=lambda r: r.unit_id):
        # The rules read only the keys they need (blocks/rules.py's fixed
        # signature); a weak row with no valid run fails both registered
        # rules, so an unmeasured baseline task is never an add.
        counts = {"valid": row.valid_count, "success": row.success_count}
        if not rule(counts, n_attempts_weak):
            continue
        task_id, _ = _parse_unit_id(row.unit_id)
        if task_id in covered:
            continue
        action = table_action(conclusion=CONCLUSION_BASELINE_GAP,
                              checks_left=0, phase_kind=PHASE_BASELINE)
        out.append(TaskListRow(
            target_kind="task", target_id=task_id, action=action.kind,
            reason=action.reason or "", basis_unit=row.unit_id))
    return out


def decide_task_list(
    *,
    current_kind: Optional[str],
    current_report: Optional[Sequence[AcceptanceReportRow]] = None,
    control_kind: Optional[str] = None,
    control_report: Optional[Sequence[AcceptanceReportRow]] = None,
    bank: Sequence[Skill],
    checks_left: int,
    compare_rule: str = "strict_rate_gain",
    n_attempts_weak: int = 0,
    task_select_rule: str = "any_weak_failed",
) -> List[TaskListRow]:
    """The unified decision entry (boundary doc section 2.4): this
    round's acceptance report, the control report, the current bank and
    the number of checks left become one task list.

    Pure: no store, no ledger, no bank mutation (R4's decision side -- a
    skill's deletion is a task-list row, never a write here), and no
    trajectory is read: the report rows are the only evidence (section 3
    rule 7).

    * ``current_kind`` / ``control_kind`` name the phases the two
      reports belong to (the vocabulary above; the phase driver reads
      them off the frozen config). ``PHASE_TEST`` on either side is
      :class:`FinalTestReportRejected` (section 3 rule 9).
    * ``current_kind=None`` is the first learn phase's path: no
      acceptance phase has run yet, so this round's report is empty and
      the D-phase report (``control_kind="baseline"``) acts as the input
      -- the table's baseline row, one add per failed, skill-less train
      task. ``task_select_rule`` (a named blocks/rules.py rule) plus
      ``n_attempts_weak`` decide "the weak model fails"; the bank decides
      "no skill yet".
    * Otherwise every row of ``current_report`` that injected skills is
      a decision target -- the skill its exam measures (exam kind:
      ``target_kind="skill"``) or the task's whole group (live kind:
      ``target_kind="group"``, the task names the group) -- and the
      report's own no-injection rows plus ``control_report``'s weak rows
      are the controls, matched by task. A target with no control is a
      CodeBugError: the phase config names the control source, so a
      missing one is wiring, not data.
    * ``checks_left`` is the "a later check remains" column, computed by
      :func:`remaining_checks` from the frozen phase sequence.
    * ``compare_rule`` selects the comparison by name; unknown names are
      a ConfigError before any work.

    Rows come out in unit-id order, one per target, with the target's
    own ``unit_id`` as ``basis_unit``.
    """
    _get_compare_rule(compare_rule)  # unknown names fail before any work
    if current_kind == PHASE_TEST or control_kind == PHASE_TEST:
        raise FinalTestReportRejected(
            "the decision entry does not take final-test (C-phase) "
            "reports (boundary doc section 3 rule 9: test results do not "
            "flow into learning); the final summary computes the "
            "with-bank delta itself (decision.final_test_delta)")
    if current_kind not in (None, PHASE_EXAM, PHASE_LIVE):
        raise CodeBugError(f"unknown current phase kind {current_kind!r}; "
                           f"expected None or {PHASE_EXAM}/{PHASE_LIVE}")
    if control_kind not in (None, PHASE_BASELINE, PHASE_EXAM, PHASE_LIVE):
        raise CodeBugError(f"unknown control phase kind {control_kind!r}")
    if checks_left < 0:
        raise CodeBugError(f"checks_left must be >= 0, got {checks_left}")

    rows = list(current_report or ())
    if current_kind is None:
        if rows:
            raise CodeBugError(
                "a current report without a phase kind: pass the kind of "
                "the phase the report belongs to (current_kind=None is "
                "the first learn phase's empty-report path)")
        return _baseline_rows(control_kind=control_kind,
                               control_report=control_report, bank=bank,
                               n_attempts_weak=n_attempts_weak,
                               task_select_rule=task_select_rule)
    if not rows:
        return []

    # Targets are the rows that injected skills; a no-injection row of
    # the same report is an in-phase control (the bare exam answer; a
    # control unit may be run in-phase, boundary doc section 1.3).
    targets = [row for row in rows if row.injected_skills is not None]
    controls: Dict[str, List[AcceptanceReportRow]] = {}
    for row in list(control_report or ()) + [
            row for row in rows if row.injected_skills is None]:
        if row.role != "weak":
            # The control is the weak model's bare result: the question
            # is about the student, and a D-phase control batch carries
            # the strong arm's rows too.
            continue
        task_id, _ = _parse_unit_id(row.unit_id)
        controls.setdefault(task_id, []).append(row)

    out: List[TaskListRow] = []
    for row in sorted(targets, key=lambda r: r.unit_id):
        if not row.injected_skills:
            raise CodeBugError(
                f"unit {row.unit_id!r} is a with-skill unit that injects "
                "nothing; delivery produced a bare run under a with-skill "
                "label in an acceptance phase")
        task_id, _ = _parse_unit_id(row.unit_id)
        matched = controls.get(task_id, [])
        if not matched:
            raise CodeBugError(
                f"no control row for unit {row.unit_id!r} (task "
                f"{task_id!r}): an acceptance phase's units are "
                "compared against the bare control the phase config names")
        control_valid = sum(r.valid_count for r in matched)
        control_success = sum(r.success_count for r in matched)
        control_cause = next((r.cause for r in matched
                              if r.status != ROW_NORMAL), "")
        conclusion, cause = compare_conclusion(
            row=row, control_valid=control_valid,
            control_success=control_success, control_cause=control_cause,
            compare_rule=compare_rule)
        action = table_action(conclusion=conclusion, cause=cause,
                              checks_left=checks_left,
                              phase_kind=current_kind)
        if current_kind == PHASE_EXAM:
            target_kind, target_id = _exam_target(row)
        else:
            # The group is named by its task: the live phase injects one
            # task's whole group and its verdict can only reach the pack.
            target_kind, target_id = "group", task_id
        out.append(TaskListRow(
            target_kind=target_kind, target_id=target_id,
            action=action.kind, reason=action.reason or "",
            basis_unit=row.unit_id))
    return out


# ---------------------------------------------------------------------------
# R3: the section 4.3 companion rules
# ---------------------------------------------------------------------------

def reuse_key(unit: ExecutionUnitRow) -> str:
    """Section 4.3 rule 1's judgement key: the task, the execution role
    and the sha256 of the RENDERED injection block.

    The block bytes -- not the injected references -- are the key on
    purpose: a rewrite that lands byte-identical content still bumps the
    version, so the references differ while the measurement does not,
    and rule 1 counts that as "unchanged" (修改后内容和原来一字不差,
    也算没变). A bare unit and a retrieval-miss unit share the empty
    block because they are the same measurement. This key is separate
    from the resume/skip key (repair plan section 4.2, note 7); the
    final test C never uses it -- it re-runs every time.
    """
    digest = hashlib.sha256(unit.skills_block.encode("utf-8")).hexdigest()
    _, role = _parse_unit_id(unit.unit_id)
    return f"{unit.task_ref}:{role}:{digest}"


def reusable_units(new_units: Sequence[ExecutionUnitRow],
                   previous_units: Sequence[ExecutionUnitRow]
                   ) -> Dict[str, str]:
    """Which new units reuse a previous unit's acceptance result
    (section 4.3 rule 1: task and injection both unchanged -> no
    re-execution): ``{new unit_id: previous unit_id}``.

    The phase driver skips execution for the new units in the mapping
    and reads the previous report's row for the returned ids; every
    other new unit re-executes. Two previous units sharing a key is a
    CodeBugError: within one phase a (task, role, block) triple names
    one unit, so a duplicate means the batches disagree.
    """
    by_key: Dict[str, str] = {}
    for unit in previous_units:
        key = reuse_key(unit)
        if key in by_key:
            raise CodeBugError(
                f"two previous units share the reuse key {key!r}: "
                f"{by_key[key]!r} and {unit.unit_id!r}")
        by_key[key] = unit.unit_id
    return {unit.unit_id: by_key[reuse_key(unit)]
            for unit in new_units if reuse_key(unit) in by_key}


def revise_outcome(row: TaskListRow, *,
                   produced_new_version: bool) -> TaskListRow:
    """Section 4.3 rule 2: a revise whose rewrite produced no new
    version leaves the target exactly as it was -- the row becomes keep,
    nothing changed (so rule 1 skips the re-test) and the next learn
    phase tries the revision again. A last learn phase that still shows
    no gain deletes it through the table. The row passes through
    unchanged when a new version exists."""
    if produced_new_version:
        return row
    return TaskListRow(target_kind=row.target_kind,
                       target_id=row.target_id, action=KEEP,
                       reason="rewrite_produced_no_new_version",
                       basis_unit=row.basis_unit)


# ---------------------------------------------------------------------------
# R3: the comparisons that moved out of the execution stages (G3)
# ---------------------------------------------------------------------------

def final_test_delta(with_bank: Dict[str, Any],
                     control: Dict[str, Any]) -> Dict[str, Any]:
    """The C-phase summary's with-bank minus control difference.

    Section 3 rule 9 / repair plan section 4.5: the final summary
    computes the test delta itself -- this is the comparison that used
    to live inside stage 6 (G3), moved here unchanged. It is a summary
    statistic, NOT a decision: the decision entry never sees a C-phase
    report (``decide_task_list`` rejects one).

    D3 pre-registers two avg@N disciplines (conditional ``avg_at_n`` and
    the TB21 ``avg_at_n_conservative``); both deltas are reported side by
    side so nobody picks the friendlier one after seeing the numbers.

    ``avg_at_n`` is None when an arm has no valid trial at all; a delta
    between a number and nothing is not zero (that would claim "no
    improvement" from absent evidence), it is None -- recorded honestly
    rather than coerced.

    The plugin-declared auxiliary metric (when there is one) gets the
    same treatment: ``aux`` carries its name and the same two deltas
    under the same None rule, always beside the primary, never instead
    of it. It is None when the plugin declares no auxiliary metric (both
    arms' summaries carry ``aux: null`` then).
    """
    with_avg = with_bank["avg_at_n"]
    control_avg = control["avg_at_n"]
    return {
        "avg_at_n": (with_avg - control_avg
                     if with_avg is not None and control_avg is not None
                     else None),
        # summarize() never yields None for these two (0.0 with no valid
        # trial / no tasks), so their deltas are always numbers.
        "avg_at_n_conservative": (with_bank["avg_at_n_conservative"]
                                  - control["avg_at_n_conservative"]),
        "pass_at_n": with_bank["pass_at_n"] - control["pass_at_n"],
        "aux": _aux_delta(with_bank.get("aux"), control.get("aux")),
    }


def _aux_delta(with_aux: Optional[Dict[str, Any]],
               control_aux: Optional[Dict[str, Any]]
               ) -> Optional[Dict[str, Any]]:
    """With-bank minus control for the auxiliary metric (None when absent)."""
    if with_aux is None or control_aux is None:
        return None
    with_avg = with_aux["avg_at_n"]
    control_avg = control_aux["avg_at_n"]
    return {
        "metric": with_aux["metric"],
        "avg_at_n": (with_avg - control_avg
                     if with_avg is not None and control_avg is not None
                     else None),
        "avg_at_n_conservative": (with_aux["avg_at_n_conservative"]
                                  - control_aux["avg_at_n_conservative"]),
    }


def control_arm_source(*, reused_phase: Optional[str]) -> str:
    """The control arm's source label -- the choice that used to sit
    inside stage 6 (G3): a named earlier phase whose stored results the
    control reuses, or a fresh bare run. Boundary doc section 2.4: the
    control report's phase is a stage-config decision, not an execution
    stage's. The label keeps the historical summary vocabulary (E3 pins
    it): ``"<phase>_reuse"`` / ``"fresh_bare_run"``."""
    if reused_phase is None:
        return "fresh_bare_run"
    return f"{reused_phase}_reuse"
