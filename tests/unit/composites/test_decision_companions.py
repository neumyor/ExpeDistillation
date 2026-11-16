"""The decision layer's final-test statistics:
the final-test summary delta and the control-arm source label."""

import pytest

from expedistill.composites.decision import (
    final_test_delta,
)


# ---------------------------------------------------------------------------
# The final test's summary statistics
# ---------------------------------------------------------------------------

def test_final_test_delta_conservative_defined_when_conditional_is_none():
    # An arm with no valid trial has avg_at_n None, but its conservative
    # avg is still a number (0.0), so its delta is a number too.
    with_bank = {"avg_at_n": 0.5, "avg_at_n_conservative": 0.5,
                 "avg_at_n_invalid_as_failure": 0.5,
                 "pass_at_n": 0.5}
    control = {"avg_at_n": None, "avg_at_n_conservative": 0.0,
               "avg_at_n_invalid_as_failure": 0.0,
               "pass_at_n": 0.0}
    delta = final_test_delta(with_bank, control)
    assert delta["avg_at_n"] is None
    assert delta["avg_at_n_conservative"] == pytest.approx(0.5)
    assert delta["pass_at_n"] == pytest.approx(0.5)


def test_final_test_delta_reports_both_avg_disciplines_side_by_side():
    delta = final_test_delta(
        {"avg_at_n": 0.75, "avg_at_n_conservative": 0.6, "avg_at_n_invalid_as_failure": 0.6, "pass_at_n": 0.5},
        {"avg_at_n": 0.25, "avg_at_n_conservative": 0.2, "avg_at_n_invalid_as_failure": 0.2, "pass_at_n": 0.25})
    assert delta["avg_at_n"] == pytest.approx(0.5)
    assert delta["avg_at_n_conservative"] == pytest.approx(0.4)
    assert delta["avg_at_n_invalid_as_failure"] == pytest.approx(0.4)
    assert delta["pass_at_n"] == pytest.approx(0.25)


def test_final_test_delta_aux_is_none_without_a_declaration():
    delta = final_test_delta({"avg_at_n": 1.0, "avg_at_n_conservative": 1.0,
                              "avg_at_n_invalid_as_failure": 1.0,
                              "pass_at_n": 1.0, "aux": None},
                             {"avg_at_n": 0.0, "avg_at_n_conservative": 0.0,
                              "avg_at_n_invalid_as_failure": 0.0,
                              "pass_at_n": 0.0, "aux": None})
    assert delta["aux"] is None


def test_final_test_delta_aux_conditional_none_when_an_arm_has_no_trial():
    # Same honesty rule as the primary: a delta between a number and an
    # absent conditional average is null, not zero.
    with_bank = {"avg_at_n": 0.5, "avg_at_n_conservative": 0.5,
                 "avg_at_n_invalid_as_failure": 0.5,
                 "pass_at_n": 0.5,
                 "aux": {"metric": "fake_aux", "avg_at_n": 0.5,
                         "avg_at_n_conservative": 0.5}}
    control = {"avg_at_n": None, "avg_at_n_conservative": 0.0,
               "avg_at_n_invalid_as_failure": 0.0,
               "pass_at_n": 0.0,
               "aux": {"metric": "fake_aux", "avg_at_n": None,
                       "avg_at_n_conservative": 0.0}}
    delta = final_test_delta(with_bank, control)
    assert delta["aux"]["metric"] == "fake_aux"
    assert delta["aux"]["avg_at_n"] is None
    assert delta["aux"]["avg_at_n_conservative"] == pytest.approx(0.5)
