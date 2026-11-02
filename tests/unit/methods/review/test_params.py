"""ReviewParams: the ablation switches and their validation."""

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve_params
from expedistill.methods.review import REVISE_SCOPES, ReviewParams


def test_defaults_are_the_unrestricted_method():
    params = ReviewParams()
    assert params.revise_scope == "both"
    assert params.new_skills is True
    assert params.accept_new is False


@pytest.mark.parametrize("scope", REVISE_SCOPES)
def test_every_declared_scope_is_accepted(scope):
    assert ReviewParams(revise_scope=scope).revise_scope == scope


def test_an_unknown_scope_is_refused():
    with pytest.raises(ConfigError, match="revise_scope"):
        ReviewParams(revise_scope="steps")


@pytest.mark.parametrize("name", ["new_skills", "accept_new"])
def test_the_boolean_switches_refuse_other_types(name):
    with pytest.raises(ConfigError, match=name):
        ReviewParams(**{name: "yes"})


def test_the_switches_resolve_from_a_phase_mapping():
    params = resolve_params("review", {
        "name": "review", "decision": "trial_level",
        "revise_scope": "trigger", "new_skills": False,
        "accept_new": True})
    assert (params.revise_scope, params.new_skills, params.accept_new) \
        == ("trigger", False, True)
