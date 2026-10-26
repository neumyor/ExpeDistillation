"""The shared verdict/status/cause vocabulary's unit tests.

Pins the exact strings the run directories and golden fixtures carry and
the fixed not-testable cause set.  Uniqueness is asserted per vocabulary:
the cause and coverage vocabularies deliberately share the word
``missing`` (a cause is not a coverage status), but no two values inside
one vocabulary may collide.
"""

from expedistill.core import vocabulary


def test_run_verdicts_are_exactly_the_three_expected_strings():
    assert vocabulary.RUN_SUCCESS == "success"
    assert vocabulary.RUN_FAILURE == "failure"
    assert vocabulary.RUN_INVALID == "invalid"
    assert vocabulary.RUN_VERDICTS == (
        vocabulary.RUN_SUCCESS,
        vocabulary.RUN_FAILURE,
        vocabulary.RUN_INVALID,
    )
    assert len(set(vocabulary.RUN_VERDICTS)) == 3


def test_unit_statuses_are_exactly_the_three_expected_strings():
    assert vocabulary.STATUS_NORMAL == "normal"
    assert vocabulary.STATUS_NOT_TESTABLE == "not_testable"
    assert vocabulary.STATUS_NOT_RUN_CONTROL_PERFECT == \
        "not_run_control_perfect"
    assert vocabulary.UNIT_STATUSES == (
        vocabulary.STATUS_NORMAL,
        vocabulary.STATUS_NOT_TESTABLE,
        vocabulary.STATUS_NOT_RUN_CONTROL_PERFECT,
    )
    assert len(set(vocabulary.UNIT_STATUSES)) == 3


def test_coverage_statuses_are_exactly_the_three_expected_strings():
    assert vocabulary.COVERAGE_PRESENT == "present"
    assert vocabulary.COVERAGE_MISSING == "missing"
    assert vocabulary.COVERAGE_TERMINAL == "terminal"
    assert vocabulary.COVERAGE_STATUSES == (
        vocabulary.COVERAGE_PRESENT,
        vocabulary.COVERAGE_MISSING,
        vocabulary.COVERAGE_TERMINAL,
    )
    assert len(set(vocabulary.COVERAGE_STATUSES)) == 3


def test_not_testable_causes_are_exactly_the_six_fixed_causes():
    assert vocabulary.NOT_TESTABLE_CAUSES == frozenset({
        "leak",
        "injection_overflow",
        "exam_generation_failed",
        "invalid_redispatch_exhausted",
        "all_holes",
        "zero_execution",
    })
    assert isinstance(vocabulary.NOT_TESTABLE_CAUSES, frozenset)


def test_every_cause_constant_is_the_expected_string_and_unique():
    causes = {
        vocabulary.CAUSE_LEAK: "leak",
        vocabulary.CAUSE_INJECTION_OVERFLOW: "injection_overflow",
        vocabulary.CAUSE_EXAM_GENERATION_FAILED: "exam_generation_failed",
        vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED:
            "invalid_redispatch_exhausted",
        vocabulary.CAUSE_ALL_HOLES: "all_holes",
        vocabulary.CAUSE_ZERO_EXECUTION: "zero_execution",
        vocabulary.CAUSE_WEAK_ALREADY_KNOWS: "weak_already_knows",
        vocabulary.CAUSE_ACCEPTED_HOLE: "accepted_hole",
        vocabulary.CAUSE_MISSING: "missing",
    }
    for actual, expected in causes.items():
        assert actual == expected
    # A dict keyed by the constants already proves uniqueness; assert the
    # size too so a future duplicate cannot silently collapse the entry.
    assert len(causes) == 9


def test_the_six_not_testable_causes_are_the_first_six_cause_constants():
    assert vocabulary.NOT_TESTABLE_CAUSES == frozenset({
        vocabulary.CAUSE_LEAK,
        vocabulary.CAUSE_INJECTION_OVERFLOW,
        vocabulary.CAUSE_EXAM_GENERATION_FAILED,
        vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED,
        vocabulary.CAUSE_ALL_HOLES,
        vocabulary.CAUSE_ZERO_EXECUTION,
    })
    # The control-perfect cause is a skip, never a not-testable cause.
    assert vocabulary.CAUSE_WEAK_ALREADY_KNOWS not in \
        vocabulary.NOT_TESTABLE_CAUSES


def test_all_exported_names_exist_and_are_the_documented_kinds():
    for name in vocabulary.__all__:
        assert hasattr(vocabulary, name), name
        value = getattr(vocabulary, name)
        if name in ("RUN_VERDICTS", "UNIT_STATUSES", "COVERAGE_STATUSES"):
            assert isinstance(value, tuple), name
            assert all(isinstance(item, str) for item in value), name
        elif name == "NOT_TESTABLE_CAUSES":
            assert isinstance(value, frozenset), name
        else:
            assert isinstance(value, str), name
