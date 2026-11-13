"""The shared verdict/status/cause vocabulary's unit tests.

Pins the exact strings the run directories and golden fixtures carry.
The cause and coverage vocabularies deliberately share the word
``missing`` (a cause is not a coverage status), but no two causes may
collide.
"""

from expedistill.core import vocabulary


def test_run_verdicts_are_exactly_the_three_expected_strings():
    assert vocabulary.RUN_SUCCESS == "success"
    assert vocabulary.RUN_FAILURE == "failure"
    assert vocabulary.RUN_INVALID == "invalid"


def test_unit_statuses_are_exactly_the_two_expected_strings():
    assert vocabulary.STATUS_NORMAL == "normal"
    assert vocabulary.STATUS_NOT_TESTABLE == "not_testable"


def test_coverage_statuses_are_exactly_the_three_expected_strings():
    assert vocabulary.COVERAGE_PRESENT == "present"
    assert vocabulary.COVERAGE_MISSING == "missing"
    assert vocabulary.COVERAGE_TERMINAL == "terminal"


def test_every_cause_constant_is_the_expected_string_and_unique():
    causes = {
        vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED:
            "invalid_redispatch_exhausted",
        vocabulary.CAUSE_ALL_HOLES: "all_holes",
        vocabulary.CAUSE_ZERO_EXECUTION: "zero_execution",
        vocabulary.CAUSE_ACCEPTED_HOLE: "accepted_hole",
        vocabulary.CAUSE_MISSING: "missing",
    }
    for actual, expected in causes.items():
        assert actual == expected
    # A dict keyed by the constants already proves uniqueness; assert the
    # size too so a future duplicate cannot silently collapse the entry.
    assert len(causes) == 5


def test_all_exported_names_exist_and_are_the_documented_kinds():
    for name in vocabulary.__all__:
        assert hasattr(vocabulary, name), name
        assert isinstance(getattr(vocabulary, name), str), name
