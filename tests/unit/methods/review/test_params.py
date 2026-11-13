"""ReviewParams: the learn phase's parameters and their validation."""

from dataclasses import fields

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve_params
from expedistill.methods.review import ReviewParams


def test_defaults():
    params = ReviewParams()
    assert (params.K, params.retry_limit, params.write_lessons) \
        == (5, 2, True)


def test_the_deferred_switches_are_not_parameters():
    # revise_scope and cards are designed but deferred (decision doc
    # evidence_driven_evolution_20261113 3.5).
    assert {item.name for item in fields(ReviewParams)} == {
        "K", "retry_limit", "write_lessons"}


@pytest.mark.parametrize("field, value", [
    ("K", 0), ("K", True), ("retry_limit", -1), ("write_lessons", "yes")])
def test_bad_values_are_refused(field, value):
    with pytest.raises(ConfigError, match=field):
        ReviewParams(**{field: value})


def test_the_params_resolve_from_a_phase_mapping():
    params = resolve_params("review", {
        "name": "review", "K": 3, "retry_limit": 0, "write_lessons": False})
    assert (params.K, params.retry_limit, params.write_lessons) \
        == (3, 0, False)
