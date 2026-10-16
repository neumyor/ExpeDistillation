"""W1-C (R3): the section 4.3 companion rules and the moved comparisons.

Three companion rules (unchanged units are not re-tested; a failed
rewrite keeps the target as it was; a perfect control may skip the
with-skill run), plus the two comparisons that moved out of
stage6_test.py into the decision layer (G3): the final-test summary
delta and the control-arm source label. The C phase's never-reuse
exception is a caller contract, pinned here only as a docstring rule.
"""

import pytest

from expedistill.composites import decision
from expedistill.composites.decision import (
    ExecutionUnitRow,
    TaskListRow,
    control_arm_source,
    control_perfect_skips_run,
    final_test_delta,
    reuse_key,
    reusable_units,
    revise_outcome,
)
from expedistill.infra.reliability import CodeBugError


def _unit(unit_id, task_ref="tb:t1", skills_injected=None, block=""):
    return ExecutionUnitRow(unit_id=unit_id, task_ref=task_ref,
                            skills_injected=skills_injected,
                            skills_block=block, aux={})


# ---------------------------------------------------------------------------
# Rule 1: unchanged units are not re-tested
# ---------------------------------------------------------------------------

def test_reuse_key_covers_task_role_and_block_bytes():
    a = _unit("p01:B:tb:tb:t1:weak:11111111", block="the block")
    same = _unit("p01:B:tb:tb:t1:weak:22222222", block="the block")
    assert reuse_key(a) == reuse_key(same)
    # A different task, role or block bytes each break the key.
    assert reuse_key(a) != reuse_key(
        _unit("p01:B:tb:tb:t1:weak:11111111", task_ref="tb:t2",
              block="the block"))
    assert reuse_key(a) != reuse_key(
        _unit("p01:B:tb:tb:t1:strong:11111111", block="the block"))
    assert reuse_key(a) != reuse_key(
        _unit("p01:B:tb:tb:t1:weak:11111111", block="another block"))
    assert reuse_key(a) != reuse_key(
        _unit("p01:B:tb:tb:t1:weak:11111111", block=""))


def test_unchanged_units_reuse_the_previous_result():
    previous = [
        _unit("p00:B:tb:tb:t1:weak:11111111", block="the block"),
        _unit("p00:B:tb:tb:t2:weak:22222222", block="other"),
    ]
    new = [
        # Same task, same block: reuse (the unit id differs -- the sha is
        # over the injected REFERENCES, a rewrite bumped the version).
        _unit("p01:B:tb:tb:t1:weak:33333333",
              skills_injected=[{"skill_id": "s", "version": 2}],
              block="the block"),
        # Different block: re-execute.
        _unit("p01:B:tb:tb:t2:weak:44444444", block="changed"),
    ]
    assert reusable_units(new, previous) == {
        "p01:B:tb:tb:t1:weak:33333333": "p00:B:tb:tb:t1:weak:11111111"}


def test_bare_and_retrieval_miss_units_share_the_empty_block_key():
    # Both are the same measurement (a bare run), so one may reuse the
    # other's acceptance result under rule 1.
    previous = [_unit("p00:C:tb:tb:t1:weak:11111111",
                      skills_injected=None, block="")]
    new = [_unit("p01:C:tb:tb:t1:weak:22222222",
                 skills_injected=[], block="")]
    assert reusable_units(new, previous) == {
        "p01:C:tb:tb:t1:weak:22222222": "p00:C:tb:tb:t1:weak:11111111"}


def test_duplicate_previous_keys_fail_loudly():
    previous = [
        _unit("p00:B:tb:tb:t1:weak:11111111", block="the block"),
        _unit("p00:B:tb:tb:t1:weak:22222222", block="the block"),
    ]
    with pytest.raises(CodeBugError, match="share the reuse key"):
        reusable_units([_unit("p01:B:tb:tb:t1:weak:33333333",
                              block="the block")], previous)


def test_malformed_unit_ids_fail_loudly_in_the_reuse_key():
    for bad in ("no-colons-at-all", "p01:B:tb:t1:weak:ZZZZZZZZ",
                "xx:B:tb:t1:weak:11111111"):
        with pytest.raises(CodeBugError, match="malformed unit id"):
            reuse_key(_unit(bad, block="the block"))


# ---------------------------------------------------------------------------
# Rule 2: a rewrite that produced no new version keeps the target
# ---------------------------------------------------------------------------

def test_failed_rewrite_keeps_the_target_as_it_was():
    revise = TaskListRow(target_kind="group", target_id="tb:t1",
                         action=decision.REVISE, reason="no_gain",
                         basis_unit="p01:B:tb:tb:t1:weak:22222222")
    landed = revise_outcome(revise, produced_new_version=False)
    assert landed.target_kind == "group"
    assert landed.target_id == "tb:t1"
    assert landed.action == decision.KEEP
    assert landed.reason == "rewrite_produced_no_new_version"
    assert landed.basis_unit == revise.basis_unit


def test_successful_rewrite_passes_the_row_through():
    revise = TaskListRow(target_kind="skill", target_id="s1",
                         action=decision.REVISE, reason="no_gain",
                         basis_unit="u1")
    assert revise_outcome(revise, produced_new_version=True) is revise


# ---------------------------------------------------------------------------
# Rule 3: a perfect control may skip the with-skill run
# ---------------------------------------------------------------------------

def test_perfect_control_skips_only_under_strict_family_rules():
    perfect = {"compare_rule": "strict_rate_gain",
               "control_valid": 2, "control_success": 2}
    assert control_perfect_skips_run(**perfect) is True
    assert control_perfect_skips_run(compare_rule="first_success_gain",
                                     control_valid=2,
                                     control_success=2) is True
    # A rule outside the strict family never skips (a rate-1.0 baseline
    # could still lose under it), and neither does an imperfect control.
    assert control_perfect_skips_run(compare_rule="pass_at_8",
                                     control_valid=2,
                                     control_success=2) is False
    assert control_perfect_skips_run(compare_rule="strict_rate_gain",
                                     control_valid=2,
                                     control_success=1) is False
    assert control_perfect_skips_run(compare_rule="strict_rate_gain",
                                     control_valid=0,
                                     control_success=0) is False


# ---------------------------------------------------------------------------
# The comparisons that moved out of stage 6 (G3)
# ---------------------------------------------------------------------------

def test_final_test_delta_conservative_defined_when_conditional_is_none():
    # An arm with no valid trial has avg_at_n None, but its conservative
    # avg is still a number (0.0), so its delta is a number too.
    with_bank = {"avg_at_n": 0.5, "avg_at_n_conservative": 0.5,
                 "pass_at_n": 0.5}
    control = {"avg_at_n": None, "avg_at_n_conservative": 0.0,
               "pass_at_n": 0.0}
    delta = final_test_delta(with_bank, control)
    assert delta["avg_at_n"] is None
    assert delta["avg_at_n_conservative"] == pytest.approx(0.5)
    assert delta["pass_at_n"] == pytest.approx(0.5)


def test_final_test_delta_reports_both_avg_disciplines_side_by_side():
    delta = final_test_delta(
        {"avg_at_n": 0.75, "avg_at_n_conservative": 0.6, "pass_at_n": 0.5},
        {"avg_at_n": 0.25, "avg_at_n_conservative": 0.2, "pass_at_n": 0.25})
    assert delta["avg_at_n"] == pytest.approx(0.5)
    assert delta["avg_at_n_conservative"] == pytest.approx(0.4)
    assert delta["pass_at_n"] == pytest.approx(0.25)


def test_final_test_delta_aux_is_none_without_a_declaration():
    delta = final_test_delta({"avg_at_n": 1.0, "avg_at_n_conservative": 1.0,
                              "pass_at_n": 1.0, "aux": None},
                             {"avg_at_n": 0.0, "avg_at_n_conservative": 0.0,
                              "pass_at_n": 0.0, "aux": None})
    assert delta["aux"] is None


def test_final_test_delta_aux_conditional_none_when_an_arm_has_no_trial():
    # Same honesty rule as the primary: a delta between a number and an
    # absent conditional average is null, not zero.
    with_bank = {"avg_at_n": 0.5, "avg_at_n_conservative": 0.5,
                 "pass_at_n": 0.5,
                 "aux": {"metric": "fake_aux", "avg_at_n": 0.5,
                         "avg_at_n_conservative": 0.5}}
    control = {"avg_at_n": None, "avg_at_n_conservative": 0.0,
               "pass_at_n": 0.0,
               "aux": {"metric": "fake_aux", "avg_at_n": None,
                       "avg_at_n_conservative": 0.0}}
    delta = final_test_delta(with_bank, control)
    assert delta["aux"]["metric"] == "fake_aux"
    assert delta["aux"]["avg_at_n"] is None
    assert delta["aux"]["avg_at_n_conservative"] == pytest.approx(0.5)


def test_control_arm_source_keeps_the_summary_vocabulary():
    # E3 pins the C summary's control_source values; the choice moved
    # into the decision layer but the labels must not change.
    assert control_arm_source(reused_phase="stage1") == "stage1_reuse"
    assert control_arm_source(reused_phase=None) == "fresh_bare_run"
    assert control_arm_source(reused_phase="p00:D") == "p00:D_reuse"
