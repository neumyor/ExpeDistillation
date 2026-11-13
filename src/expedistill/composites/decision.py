"""Unit-id parsing and the final test's summary statistics (layer 5, pure).

* :func:`parse_unit_id` -- the task and role a unit id names;
* :func:`final_test_delta` -- the test-phase summary's with-bank minus
  control difference;
* :func:`control_arm_source` -- the control arm's source label.

Nothing here decides what a learn phase does: test results never reach a
learn phase, and the learn phase's evidence routing lives in
``composites/evidence.py``.
"""

import re
from typing import Any, Dict, Optional, Tuple

from expedistill.core.models import MODEL_ROLES
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "control_arm_source",
    "final_test_delta",
    "parse_unit_id",
]

#: A unit id's sha field (core/contracts.py: the first eight hex chars of
#: the sha256 over the canonical injected skills) and its phase label's
#: ``pNN`` head (R9: labels are ``pNN:<local name>``).
_UNIT_SHA_RE = re.compile(r"[0-9a-f]{8}")
_PHASE_HEAD_RE = re.compile(r"p[0-9]+")


def parse_unit_id(unit_id: str) -> Tuple[str, str]:
    """(task_id, role) out of a unit id, per core/contracts.py's frozen
    construction rule ``phase:benchmark:task:role:sha8``.

    Parsed from BOTH ends because the middle swallows colons: task ids
    carry their benchmark prefix (``tb:t1``), while the phase label is
    ``pNN:<local>`` (one colon) and the benchmark carries none -- so the
    task id is everything
    between the third colon and the role. The task slot must itself
    carry its benchmark prefix (a colon): a bare ``pNN`` phase label
    (no ``:<name>``) eats the benchmark slot and leaves the task slot
    colonless, and that mis-slice is refused here instead of silently
    returning a wrong task id (D2). Anything malformed is a
    CodeBugError: the id is contract, and a report that disagrees with
    it is a wiring bug, never data to guess with.
    """
    try:
        head, role, sha = unit_id.rsplit(":", 2)
    except ValueError:
        raise CodeBugError(f"malformed unit id {unit_id!r}") from None
    prefix = head.split(":", 3)
    if (len(prefix) != 4
            or not _PHASE_HEAD_RE.fullmatch(prefix[0])
            or ":" not in prefix[3]
            or role not in MODEL_ROLES
            or not _UNIT_SHA_RE.fullmatch(sha)):
        raise CodeBugError(f"malformed unit id {unit_id!r}")
    return prefix[3], role


# ---------------------------------------------------------------------------
# The final test's summary statistics
# ---------------------------------------------------------------------------

def final_test_delta(with_bank: Dict[str, Any],
                     control: Dict[str, Any]) -> Dict[str, Any]:
    """The test-phase summary's with-bank minus control difference.

    Section 3 rule 9 / repair plan section 4.5: the final summary
    computes the test delta itself -- this is the comparison that used
    to live inside stage 6 (G3), moved here unchanged. It is a summary
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
