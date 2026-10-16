"""Direct tests of the decision rules (plan section 3's checklist).

The callers (the phase drivers and the live verdicts ledger) exercise
every rule end-to-end in tests/composites/; these tests pin the rules'
own branch tables, so the checklist's landing -- the named function
each item maps to -- is contract, not convention (the item-by-item
table lives in composites/decision.py's module docstring).
"""

from expedistill.composites import decision
from expedistill.composites import (
    live_verdicts as lv,
)
from expedistill.composites.decision import Action


# ---------------------------------------------------------------------------
# The live verdict protocol's rules (the retained rules the phase world
# still calls; the retired K4/K6 shells' exam_* rules went with the shells)
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
        rounds) == \
        Action(decision.DELETE, "weak_already_knows")
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


# ---------------------------------------------------------------------------
# One definition, no copies: the live verdicts module re-exports the
# shared vocabulary
# ---------------------------------------------------------------------------

def test_vocabulary_is_the_one_definition():
    assert decision.WEAK_ALREADY_KNOWS is lv.WEAK_ALREADY_KNOWS
    assert decision.LIVE_INJECTION_OVERFLOW is lv.LIVE_INJECTION_OVERFLOW
    assert decision.LIVE_UNDETERMINED is lv.LIVE_UNDETERMINED
    assert decision.LIVE_NO_IMPROVEMENT is lv.LIVE_NO_IMPROVEMENT
    assert decision.LIVE_REVISION_DELETED is lv.LIVE_REVISION_DELETED
    assert decision.MODEL_OUTPUT_ERROR is lv.MODEL_OUTPUT_ERROR
