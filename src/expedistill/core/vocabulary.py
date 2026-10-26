"""The loop's shared verdict / status / cause strings (layer 0).

One definition site so grading, coverage, unit summaries, decision and audit
cannot drift.  The values are the frozen words the run directories and the
golden fixtures carry; moving them here changes no bytes.
"""

__all__ = [
    "CAUSE_ACCEPTED_HOLE",
    "CAUSE_ALL_HOLES",
    "CAUSE_EXAM_GENERATION_FAILED",
    "CAUSE_INJECTION_OVERFLOW",
    "CAUSE_INVALID_REDISPATCH_EXHAUSTED",
    "CAUSE_LEAK",
    "CAUSE_MISSING",
    "CAUSE_WEAK_ALREADY_KNOWS",
    "CAUSE_ZERO_EXECUTION",
    "COVERAGE_MISSING",
    "COVERAGE_PRESENT",
    "COVERAGE_STATUSES",
    "COVERAGE_TERMINAL",
    "NOT_TESTABLE_CAUSES",
    "RUN_FAILURE",
    "RUN_INVALID",
    "RUN_SUCCESS",
    "RUN_VERDICTS",
    "STATUS_NORMAL",
    "STATUS_NOT_RUN_CONTROL_PERFECT",
    "STATUS_NOT_TESTABLE",
    "UNIT_STATUSES",
]

# ---------------------------------------------------------------------------
# One run's grading verdict
# ---------------------------------------------------------------------------

RUN_SUCCESS = "success"
RUN_FAILURE = "failure"
RUN_INVALID = "invalid"
RUN_VERDICTS = (RUN_SUCCESS, RUN_FAILURE, RUN_INVALID)

# ---------------------------------------------------------------------------
# One unit's status / cause
# ---------------------------------------------------------------------------

STATUS_NORMAL = "normal"
STATUS_NOT_TESTABLE = "not_testable"
STATUS_NOT_RUN_CONTROL_PERFECT = "not_run_control_perfect"
UNIT_STATUSES = (
    STATUS_NORMAL,
    STATUS_NOT_TESTABLE,
    STATUS_NOT_RUN_CONTROL_PERFECT,
)

CAUSE_LEAK = "leak"
CAUSE_INJECTION_OVERFLOW = "injection_overflow"
CAUSE_EXAM_GENERATION_FAILED = "exam_generation_failed"
CAUSE_INVALID_REDISPATCH_EXHAUSTED = "invalid_redispatch_exhausted"
CAUSE_ALL_HOLES = "all_holes"
CAUSE_ZERO_EXECUTION = "zero_execution"
CAUSE_WEAK_ALREADY_KNOWS = "weak_already_knows"

NOT_TESTABLE_CAUSES = frozenset({
    CAUSE_LEAK,
    CAUSE_INJECTION_OVERFLOW,
    CAUSE_EXAM_GENERATION_FAILED,
    CAUSE_INVALID_REDISPATCH_EXHAUSTED,
    CAUSE_ALL_HOLES,
    CAUSE_ZERO_EXECUTION,
})

# ---------------------------------------------------------------------------
# One trial's coverage record
# ---------------------------------------------------------------------------

COVERAGE_PRESENT = "present"
COVERAGE_MISSING = "missing"
COVERAGE_TERMINAL = "terminal"
COVERAGE_STATUSES = (
    COVERAGE_PRESENT,
    COVERAGE_MISSING,
    COVERAGE_TERMINAL,
)

#: A trial the baseline phase accepted as an infrastructure hole.  It is not
#: a missing dispatch and not a zero score; the unit summary maps an
#: all-hole unit to ``all_holes``.
CAUSE_ACCEPTED_HOLE = "accepted_hole"

#: An expected trial that was never stored and was not accepted as a hole.
#: The unit summary maps it to ``invalid_redispatch_exhausted``.
CAUSE_MISSING = "missing"
