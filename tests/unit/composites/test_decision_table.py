"""W1-C (R3): the unified decision table, one test per section 4.2 row.

The boundary doc's table is contract, not convention: every
(conclusion x whether a check remains) cell has its own test, plus the
consumption rules around it -- the strict short-circuit statuses
(``not_run_control_perfect``, ``zero_execution``), the compare rules as
named parameters and the phase-sequence check counter. The end-to-end
entry lives in tests/unit/composites/test_decision_entry.py; the section 4.3 companion
rules in tests/unit/composites/test_decision_companions.py.
"""

import pytest

from expedistill.composites import decision
from expedistill.composites.decision import AcceptanceReportRow
from expedistill.infra.reliability import CodeBugError, ConfigError


def _row(*, valid=1, success=1, status="normal", cause="", unit_id="u1",
         role="weak", injected=None):
    return AcceptanceReportRow(
        unit_id=unit_id, phase_label="p01:A", role=role,
        injected_skills=injected if injected is not None else
        [{"skill_id": "traj/tb:t1/strong/00/seg00/skill", "version": 1}],
        runs=[], valid_count=valid, success_count=success, status=status,
        cause=cause, artifacts={})


# ---------------------------------------------------------------------------
# table_action: one test per section 4.2 row
# ---------------------------------------------------------------------------

def test_improved_keeps_either_way():
    for checks_left in (0, 1, 2):
        assert decision.table_action(
            conclusion=decision.CONCLUSION_IMPROVED, checks_left=checks_left,
            phase_kind=decision.PHASE_EXAM) == decision.Action(
            decision.KEEP, "improved")


def test_no_gain_revises_while_a_check_remains():
    for kind in (decision.PHASE_EXAM, decision.PHASE_LIVE):
        assert decision.table_action(
            conclusion=decision.CONCLUSION_NO_GAIN, checks_left=1,
            phase_kind=kind) == decision.Action(decision.REVISE, "no_gain")


def test_no_gain_deletes_on_the_last_check_with_the_kind_reason():
    assert decision.table_action(
        conclusion=decision.CONCLUSION_NO_GAIN, checks_left=0,
        phase_kind=decision.PHASE_EXAM) == decision.Action(
        decision.DELETE, decision.RETRY_EXHAUSTED)
    assert decision.table_action(
        conclusion=decision.CONCLUSION_NO_GAIN, checks_left=0,
        phase_kind=decision.PHASE_LIVE) == decision.Action(
        decision.DELETE, decision.LIVE_NO_IMPROVEMENT)


def test_no_headroom_deletes_either_way():
    for checks_left in (0, 1):
        assert decision.table_action(
            conclusion=decision.CONCLUSION_NO_HEADROOM,
            checks_left=checks_left,
            phase_kind=decision.PHASE_LIVE) == decision.Action(
            decision.DELETE, decision.WEAK_ALREADY_KNOWS)


def test_not_testable_leak_revises_while_a_check_remains():
    for kind in (decision.PHASE_EXAM, decision.PHASE_LIVE):
        assert decision.table_action(
            conclusion=decision.CONCLUSION_NOT_TESTABLE,
            cause=decision.CAUSE_LEAK, checks_left=1,
            phase_kind=kind) == decision.Action(decision.REVISE, "leak")


def test_not_testable_leak_deletes_on_the_last_check():
    for kind in (decision.PHASE_EXAM, decision.PHASE_LIVE):
        assert decision.table_action(
            conclusion=decision.CONCLUSION_NOT_TESTABLE,
            cause=decision.CAUSE_LEAK, checks_left=0,
            phase_kind=kind) == decision.Action(
            decision.DELETE, decision.LEAK_AUDIT)


@pytest.mark.parametrize("cause,reason", [
    (decision.CAUSE_INJECTION_OVERFLOW, decision.INJECTION_OVERFLOW),
    (decision.CAUSE_EXAM_GENERATION_FAILED, decision.MODEL_OUTPUT_ERROR),
    (decision.CAUSE_INVALID_REDISPATCH_EXHAUSTED,
     decision.INVALID_REDISPATCH_EXHAUSTED),
    (decision.CAUSE_ALL_HOLES, decision.ALL_HOLES),
])
def test_not_testable_other_causes_delete_either_way(cause, reason):
    # P4: the delete reasons of these phase-neutral causes are the
    # causes' own names in every phase -- the live_* names stay with
    # K6's terminal vocabulary, so a baseline control whose redispatch
    # died can never delete an exam-phase skill as "live_undetermined".
    for checks_left in (0, 1):
        for kind in (decision.PHASE_EXAM, decision.PHASE_LIVE):
            assert decision.table_action(
                conclusion=decision.CONCLUSION_NOT_TESTABLE, cause=cause,
                checks_left=checks_left,
                phase_kind=kind) == decision.Action(decision.DELETE, reason)


def test_not_testable_zero_execution_has_no_delete_reason():
    # B1's one-meaning rule: zero_execution is a visibility fact (no
    # completed run to report yet), never a delete reason. The entry
    # skips such rows; the table itself refuses the cause loudly, so a
    # future caller cannot resurrect weak_already_knows through it.
    with pytest.raises(CodeBugError, match="unknown not-testable cause"):
        decision.table_action(
            conclusion=decision.CONCLUSION_NOT_TESTABLE,
            cause=decision.CAUSE_ZERO_EXECUTION, checks_left=1,
            phase_kind=decision.PHASE_LIVE)


def test_deciding_against_an_in_flight_control_is_a_wiring_bug():
    # B1's danger path: the with-skill arm is measured while the control
    # is still in flight (zero_execution). Deciding then is the
    # caller's bug -- the unmeasured control must never yield
    # weak_already_knows (or any other conclusion).
    with pytest.raises(CodeBugError, match="in flight"):
        decision.compare_conclusion(
            row=_row(valid=1, success=1), control_valid=0,
            control_success=0,
            control_cause=decision.CAUSE_ZERO_EXECUTION,
            compare_rule="strict_rate_gain")


def test_baseline_gap_adds_either_way():
    for checks_left in (0, 1):
        assert decision.table_action(
            conclusion=decision.CONCLUSION_BASELINE_GAP,
            checks_left=checks_left,
            phase_kind=decision.PHASE_BASELINE) == decision.Action(
            decision.ADD, decision.BASELINE_GAP_REASON)


def test_table_rejects_unknown_conclusion_and_cause():
    with pytest.raises(CodeBugError, match="unknown conclusion"):
        decision.table_action(conclusion="mysterious", checks_left=1,
                              phase_kind=decision.PHASE_LIVE)
    with pytest.raises(CodeBugError, match="unknown not-testable cause"):
        decision.table_action(conclusion=decision.CONCLUSION_NOT_TESTABLE,
                              cause="mysterious", checks_left=0,
                              phase_kind=decision.PHASE_LIVE)
    with pytest.raises(CodeBugError, match="checks_left"):
        decision.table_action(conclusion=decision.CONCLUSION_IMPROVED,
                              checks_left=-1, phase_kind=decision.PHASE_EXAM)


# ---------------------------------------------------------------------------
# compare_conclusion: the section 4.1 conclusions
# ---------------------------------------------------------------------------

def test_improved_and_no_gain_follow_the_compare_rule():
    improved = decision.compare_conclusion(
        row=_row(valid=2, success=2), control_valid=2, control_success=0,
        control_cause="", compare_rule="strict_rate_gain")
    assert improved == (decision.CONCLUSION_IMPROVED, "")
    no_gain = decision.compare_conclusion(
        row=_row(valid=2, success=0), control_valid=2, control_success=1,
        control_cause="", compare_rule="strict_rate_gain")
    assert no_gain == (decision.CONCLUSION_NO_GAIN, "")


def test_not_run_control_perfect_consumes_the_rule_3_skip():
    conclusion, cause = decision.compare_conclusion(
        row=_row(valid=0, success=0,
                 status=decision.ROW_NOT_RUN_CONTROL_PERFECT),
        control_valid=2, control_success=2, control_cause="",
        compare_rule="strict_rate_gain")
    assert (conclusion, cause) == (decision.CONCLUSION_NO_HEADROOM, "")


def test_not_testable_rows_carry_their_cause():
    for cause in sorted(decision.NOT_TESTABLE_CAUSES):
        conclusion, out_cause = decision.compare_conclusion(
            row=_row(valid=0, success=0, status=decision.ROW_NOT_TESTABLE,
                     cause=cause),
            control_valid=2, control_success=0, control_cause="",
            compare_rule="strict_rate_gain")
        assert (conclusion, out_cause) == (decision.CONCLUSION_NOT_TESTABLE,
                                           cause)


def test_perfect_control_under_a_strict_rule_is_no_headroom():
    conclusion, cause = decision.compare_conclusion(
        row=_row(valid=2, success=2), control_valid=2, control_success=2,
        control_cause="", compare_rule="strict_rate_gain")
    assert (conclusion, cause) == (decision.CONCLUSION_NO_HEADROOM, "")


def test_perfect_control_under_first_success_gain_is_also_no_headroom():
    # Both registered rules are strict-family (STRICT_SHORT_CIRCUIT_RULES),
    # so a rate-1.0 control is no headroom under either; a hypothetical
    # non-strict rule would let the comparison decide instead -- that gate
    # is pinned in tests/unit/composites/test_decision_companions.py's rule-3 tests.
    conclusion, _ = decision.compare_conclusion(
        row=_row(valid=2, success=2), control_valid=2, control_success=2,
        control_cause="", compare_rule="first_success_gain")
    assert conclusion == decision.CONCLUSION_NO_HEADROOM


def test_unmeasurable_control_is_not_testable_with_the_controls_cause():
    conclusion, cause = decision.compare_conclusion(
        row=_row(valid=1, success=1), control_valid=0, control_success=0,
        control_cause=decision.CAUSE_ALL_HOLES,
        compare_rule="strict_rate_gain")
    assert (conclusion, cause) == (decision.CONCLUSION_NOT_TESTABLE,
                                   decision.CAUSE_ALL_HOLES)


def test_conclusion_rejects_bad_rows_loudly():
    # A normal row with no valid run is acceptance's bug, and so are an
    # unknown status, an unknown cause and an uncaused unmeasurable
    # control: all wiring bugs, never silent defaults.
    with pytest.raises(CodeBugError, match="no valid run"):
        decision.compare_conclusion(
            row=_row(valid=0, success=0), control_valid=1, control_success=0,
            control_cause="", compare_rule="strict_rate_gain")
    with pytest.raises(CodeBugError, match="unknown status"):
        decision.compare_conclusion(
            row=_row(status="mysterious"), control_valid=1,
            control_success=0, control_cause="",
            compare_rule="strict_rate_gain")
    with pytest.raises(CodeBugError, match="frozen not-testable set"):
        decision.compare_conclusion(
            row=_row(valid=0, success=0, status=decision.ROW_NOT_TESTABLE,
                     cause="mysterious"),
            control_valid=1, control_success=0, control_cause="",
            compare_rule="strict_rate_gain")
    with pytest.raises(CodeBugError, match="no not-testable cause"):
        decision.compare_conclusion(
            row=_row(valid=1, success=1), control_valid=0,
            control_success=0, control_cause="",
            compare_rule="strict_rate_gain")


# ---------------------------------------------------------------------------
# The compare rules as named parameters
# ---------------------------------------------------------------------------

def test_strict_rate_gain_is_exact_cross_multiplication():
    current = {"valid": 3, "success": 1}
    control = {"valid": 2, "success": 1}
    # 1*2 > 1*3 is false: more successes but a worse rate is NOT better.
    assert decision.strict_rate_gain(current, control) is False
    assert decision.strict_rate_gain({"valid": 5, "success": 3},
                                     {"valid": 3, "success": 1}) is True
    # No valid run on either side is "not better", never a crash.
    assert decision.strict_rate_gain({"valid": 0, "success": 0},
                                     {"valid": 2, "success": 0}) is False
    assert decision.strict_rate_gain({"valid": 2, "success": 2},
                                     {"valid": 0, "success": 0}) is False


def test_first_success_gain_is_pass_at_n_semantics():
    assert decision.first_success_gain({"valid": 3, "success": 1},
                                       {"valid": 2, "success": 0}) is True
    assert decision.first_success_gain({"valid": 3, "success": 0},
                                       {"valid": 2, "success": 0}) is False
    assert decision.first_success_gain({"valid": 3, "success": 3},
                                       {"valid": 2, "success": 1}) is False


def test_unknown_compare_rule_is_a_config_error():
    with pytest.raises(ConfigError, match="unknown compare rule"):
        decision.compare_conclusion(
            row=_row(valid=1, success=1), control_valid=1, control_success=0,
            control_cause="", compare_rule="pass_at_8")


# ---------------------------------------------------------------------------
# remaining_checks: the "a later check remains" column
# ---------------------------------------------------------------------------

def test_remaining_checks_counts_only_a_and_b_after_this_learn():
    kinds = [decision.PHASE_BASELINE, decision.PHASE_LEARN,
             decision.PHASE_EXAM, decision.PHASE_LEARN,
             decision.PHASE_LIVE, decision.PHASE_LEARN,
             decision.PHASE_TEST]
    assert decision.remaining_checks(kinds, 1) == 2
    assert decision.remaining_checks(kinds, 3) == 1
    # The final test C never counts (section 3 rule 9).
    assert decision.remaining_checks(kinds, 5) == 0
    # A legal [D, L, L] sequence: the second L inherits nothing.
    assert decision.remaining_checks(
        [decision.PHASE_BASELINE, decision.PHASE_LEARN,
         decision.PHASE_LEARN], 1) == 0


def test_remaining_checks_rejects_bad_sequences_loudly():
    with pytest.raises(CodeBugError, match="unknown phase kind"):
        decision.remaining_checks([decision.PHASE_LEARN, "mystery"], 0)
    with pytest.raises(CodeBugError, match="outside the phase sequence"):
        decision.remaining_checks([decision.PHASE_LEARN], 3)
    with pytest.raises(CodeBugError, match="not a learn phase"):
        decision.remaining_checks([decision.PHASE_BASELINE,
                                   decision.PHASE_EXAM], 0)
