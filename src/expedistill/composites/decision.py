"""The final test's summary statistics (layer 5, pure).

* :func:`final_test_delta` -- the test-phase summary's with-bank minus
  control difference;
* :func:`control_arm_source` -- the control arm's source label.

Nothing here decides what a learn phase does: test results never reach a
learn phase, and the learn phase's evidence routing lives in
``composites/evidence.py``.
"""

from typing import Any, Dict, Optional

__all__ = [
    "control_arm_source",
    "final_test_delta",
]

# ---------------------------------------------------------------------------
# The final test's summary statistics
# ---------------------------------------------------------------------------

def final_test_delta(with_bank: Dict[str, Any],
                     control: Dict[str, Any]) -> Dict[str, Any]:
    """The test-phase summary's with-bank minus control difference.

    The final summary computes the test delta itself. It is a summary
    statistic, NOT a decision: test results never reach a learn phase.

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


def control_arm_source(*, external_campaign: bool) -> str:
    """The control arm's source label: ``"external_campaign"`` (zero
    execution, the campaign's score file) or ``"fresh_bare_run"``."""
    return "external_campaign" if external_campaign else "fresh_bare_run"
