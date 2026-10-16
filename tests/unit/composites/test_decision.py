"""Direct tests of the decision rules (plan section 3's checklist).

The callers (K4, K6 and the acceptance drivers) exercise every rule
end-to-end in tests/composites/; these tests pin the rules' own branch
tables, so the checklist's landing -- the named function each item
maps to -- is contract, not convention (the item-by-item table lives
in composites/decision.py's module docstring).
"""

from expedistill.composites import decision
from expedistill.composites import (
    live_verdicts as lv,
)
from expedistill.composites.decision import Action
from expedistill.core.models import Attempt


def _attempt(serial, *, purpose="bare"):
    return Attempt(attempt_id=f"traj/tb:t1/strong/00/seg00/exam/att{serial:02d}",
                   exam_id="traj/tb:t1/strong/00/seg00/exam",
                   purpose=purpose, weak_answer=f"answer {serial}",
                   judge_verdict=False, judge_reason="missed a step",
                   retry_round=serial)


# ---------------------------------------------------------------------------
# Exam rules
# ---------------------------------------------------------------------------

def test_bare_answer_rule():
    assert decision.exam_bare_answer_action(True) == \
        Action(decision.DELETE, "weak_already_knows")
    assert decision.exam_bare_answer_action(False) is None
    assert decision.exam_bare_answer_action(None) is None


def test_stored_pass_rule():
    assert decision.exam_stored_pass_action(True) == Action(decision.KEEP)
    assert decision.exam_stored_pass_action(False) is None


def test_leak_rule_splits_on_the_last_round():
    assert decision.exam_leak_action(last_round=True) == \
        Action(decision.DELETE, "leak_audit")
    assert decision.exam_leak_action(last_round=False) == \
        Action(decision.REVISE)


def test_answer_rule():
    assert decision.exam_answer_action(verdict=True,
                                       last_round=False) == Action(decision.KEEP)
    assert decision.exam_answer_action(verdict=True,
                                       last_round=True) == Action(decision.KEEP)
    assert decision.exam_answer_action(verdict=False,
                                       last_round=False) == Action(decision.REVISE)
    assert decision.exam_answer_action(verdict=False, last_round=True) == \
        Action(decision.DELETE, "retry_exhausted")


def test_measurement_exhaustion_uses_the_shared_vocabulary():
    action = decision.exam_measurement_exhausted_action()
    assert action == Action(decision.DELETE, "model_output_error")
    assert action.reason == decision.MODEL_OUTPUT_ERROR


def test_voided_rewrite_source_prefers_the_last_with_skill_attempt():
    bare = _attempt(0)
    with_skill = _attempt(1, purpose="acceptance")
    assert decision.exam_voided_rewrite_source(
        last_with_skill=with_skill, bare=bare) is with_skill
    assert decision.exam_voided_rewrite_source(
        last_with_skill=None, bare=bare) is bare
    assert decision.exam_voided_rewrite_source(
        last_with_skill=None, bare=None) is None


# ---------------------------------------------------------------------------
# Live rules
# ---------------------------------------------------------------------------

def _row(round_, *, better=None, verdict=None):
    row = {"task_id": "tb:t1", "round": round_, "better": better}
    if verdict is not None:
        row["verdict"] = verdict
    return row


def test_live_final_action_precedence():
    rounds = (0, 1)
    # A zero-execution row anywhere discards with its own reason.
    assert decision.live_final_action(
        [_row(0, verdict="live_undetermined")], rounds) == \
        Action(decision.DELETE, "live_undetermined")
    assert decision.live_final_action(
        [_row(0, better=False), _row(1, verdict="weak_already_knows")],
        rounds) == Action(decision.DELETE, "weak_already_knows")
    # The latest round with a row is decisive: better keeps, a miss on
    # the last round discards live_no_improvement.
    assert decision.live_final_action(
        [_row(0, better=False), _row(1, better=True)], rounds) == \
        Action(decision.KEEP)
    assert decision.live_final_action(
        [_row(0, better=False), _row(1, better=False)], rounds) == \
        Action(decision.DELETE, "live_no_improvement")
    # A round-0 pass keeps; a round-0 miss decides nothing (the revision
    # pipeline still owes the group work); no rows decides nothing.
    assert decision.live_final_action([_row(0, better=True)], rounds) == \
        Action(decision.KEEP)
    assert decision.live_final_action([_row(0, better=False)], rounds) is None
    assert decision.live_final_action([], rounds) is None


def test_no_op_criterion_is_byte_identity():
    assert decision.live_no_op_fires(rendered_block_sha="sha",
                                     frozen_audit_sha="sha")
    assert not decision.live_no_op_fires(rendered_block_sha="sha",
                                         frozen_audit_sha="other")


def test_zero_execution_short_circuit_is_strict_family_only():
    all_success = {"valid": 3, "success": 3}
    assert decision.live_zero_execution_short_circuit(
        rule_name="strict_rate_gain", baseline_counts=all_success)
    assert not decision.live_zero_execution_short_circuit(
        rule_name="strict_rate_gain",
        baseline_counts={"valid": 3, "success": 2})
    # A pass@N-style rule can still improve on rate 1.0 -> never fires.
    assert not decision.live_zero_execution_short_circuit(
        rule_name="some_pass_at_n_rule", baseline_counts=all_success)


def test_injection_overflow_rule():
    assert decision.live_injection_overflows(max_injection_tokens=4096,
                                             injection_tokens=4097)
    assert not decision.live_injection_overflows(max_injection_tokens=4096,
                                                 injection_tokens=4096)
    # No limit configured: no overflow, whatever the estimate says.
    assert not decision.live_injection_overflows(
        max_injection_tokens=None, injection_tokens=10 ** 9)


def test_all_holes_rule():
    assert decision.live_all_holes(["u0", "u1"], ["u1", "u0"])
    assert not decision.live_all_holes(["u0", "u1"], ["u0"])
    assert decision.live_all_holes([], [])


def test_attribution_exhaustion_rule():
    tries = [{"exhausted": False}, {"exhausted": False}]
    assert not decision.live_attribution_exhausted(tries, budget=3)
    # A full budget of failed tries without the receipt (a crash ate it).
    assert decision.live_attribution_exhausted(tries, budget=2)
    # An exhausted receipt row from a previous call completes the story.
    assert decision.live_attribution_exhausted(
        tries[:1] + [{"exhausted": True}], budget=3)


# ---------------------------------------------------------------------------
# One definition, no copies: K6 re-exports the shared vocabulary
# ---------------------------------------------------------------------------

def test_vocabulary_is_the_one_definition():
    assert decision.WEAK_ALREADY_KNOWS is lv.WEAK_ALREADY_KNOWS
    assert decision.LIVE_INJECTION_OVERFLOW is lv.LIVE_INJECTION_OVERFLOW
    assert decision.LIVE_UNDETERMINED is lv.LIVE_UNDETERMINED
    assert decision.LIVE_NO_IMPROVEMENT is lv.LIVE_NO_IMPROVEMENT
    assert decision.LIVE_REVISION_DELETED is lv.LIVE_REVISION_DELETED
    assert decision.MODEL_OUTPUT_ERROR is lv.MODEL_OUTPUT_ERROR
    assert decision.STRICT_SHORT_CIRCUIT_RULES is lv._ALL_SUCCESS_SAFE_RULES
