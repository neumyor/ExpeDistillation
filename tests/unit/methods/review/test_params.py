"""ReviewParams: the learn phase's parameters and their validation."""


import pytest

from expedistill.core.config import ConfigError
from expedistill.methods.registry import resolve_params
from expedistill.methods.review import ReviewParams


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


def test_the_ablation_switches_default_to_the_full_method():
    params = ReviewParams()
    assert (params.cards, params.evolve_profile, params.revise_scope,
            params.scope) == (True, True, None, "both")


@pytest.mark.parametrize("scope", ["both", "trigger_only", "content_only"])
def test_every_revise_scope_resolves(scope):
    params = resolve_params("review", {"name": "review",
                                       "revise_scope": scope})
    assert params.scope == scope


@pytest.mark.parametrize("field, value", [
    ("revise_scope", "body_only"), ("revise_scope", True),
    ("cards", "no"), ("cards", 0), ("evolve_profile", "off")])
def test_a_bad_switch_value_is_refused(field, value):
    with pytest.raises(ConfigError, match=field):
        resolve_params("review", {"name": "review", field: value})


@pytest.mark.parametrize("scope", ["both", "trigger_only", "content_only"])
def test_a_revise_scope_with_cards_off_is_refused(scope):
    with pytest.raises(ConfigError, match="mutually exclusive"):
        resolve_params("review", {"name": "review", "cards": False,
                                  "revise_scope": scope})


def test_cards_off_alone_is_legal():
    params = resolve_params("review", {"name": "review", "cards": False})
    assert (params.cards, params.scope) == (False, "both")
