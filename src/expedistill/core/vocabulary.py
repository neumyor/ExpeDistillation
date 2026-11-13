"""The loop's shared verdict / status / cause strings (layer 0).

One definition site so grading, coverage, unit summaries, decision and audit
cannot drift.  The values are the frozen words the run directories and the
golden fixtures carry; moving them here changes no bytes.
"""

__all__ = [
    "CAUSE_ACCEPTED_HOLE",
    "CAUSE_ALL_HOLES",
    "CAUSE_INVALID_REDISPATCH_EXHAUSTED",
    "CAUSE_MISSING",
    "CAUSE_ZERO_EXECUTION",
    "COVERAGE_MISSING",
    "COVERAGE_PRESENT",
    "COVERAGE_TERMINAL",
    "RUN_FAILURE",
    "RUN_INVALID",
    "RUN_SUCCESS",
    "STATUS_NORMAL",
    "STATUS_NOT_TESTABLE",
]

# ---------------------------------------------------------------------------
# One run's grading verdict
# ---------------------------------------------------------------------------

RUN_SUCCESS = "success"
RUN_FAILURE = "failure"
RUN_INVALID = "invalid"

# ---------------------------------------------------------------------------
# One unit's status / cause
# ---------------------------------------------------------------------------

STATUS_NORMAL = "normal"
STATUS_NOT_TESTABLE = "not_testable"

CAUSE_INVALID_REDISPATCH_EXHAUSTED = "invalid_redispatch_exhausted"
CAUSE_ALL_HOLES = "all_holes"
CAUSE_ZERO_EXECUTION = "zero_execution"

# ---------------------------------------------------------------------------
# One trial's coverage record
# ---------------------------------------------------------------------------

COVERAGE_PRESENT = "present"
COVERAGE_MISSING = "missing"
COVERAGE_TERMINAL = "terminal"

#: A trial the baseline phase accepted as an infrastructure hole.  It is not
#: a missing dispatch and not a zero score; the unit summary maps an
#: all-hole unit to ``all_holes``.
CAUSE_ACCEPTED_HOLE = "accepted_hole"

#: An expected trial that was never stored and was not accepted as a hole.
#: The unit summary maps it to ``invalid_redispatch_exhausted``.
CAUSE_MISSING = "missing"
