"""The registry's method families, pinned as data.

Families outside the table resolve to a ConfigError naming the family,
never a quiet fallback. Each family
offers exactly the names its phase fields can name; the params classes
behind the entries are exercised by tests/unit/methods/test_params.py.
"""

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve_params
from expedistill.methods.registry import _ENTRIES


def test_the_table_is_organised_by_the_method_families():
    by_family = {}
    for (family, name) in _ENTRIES:
        by_family.setdefault(family, []).append(name)
    assert set(by_family) == {"delivery", "execution", "review", "author"}
    assert sorted(by_family["delivery"]) == sorted([
        "whole_bank", "none", "folder"])
    assert sorted(by_family["execution"]) == ["real", "real_folder"]
    assert by_family["review"] == ["review"]
    assert by_family["author"] == ["subtask_cards"]


def test_the_config_literals_name_registry_entries():
    # core.config cannot import the registry, so it spells the delivery
    # and execution names it checks; they must be the table's names.
    from expedistill.core.config import (
        EXECUTION_NAMES,
        FOLDER_DELIVERY_NAME,
        NONE_DELIVERY_NAME,
    )
    assert ("delivery", NONE_DELIVERY_NAME) in _ENTRIES
    assert ("delivery", FOLDER_DELIVERY_NAME) in _ENTRIES
    assert {name for family, name in _ENTRIES
            if family == "execution"} == set(EXECUTION_NAMES)


def test_a_family_outside_the_table_is_refused():
    for family, name in (("segmentation", "llm"),
                         ("acceptance", "exam"),
                         ("curation", "agentbrew")):
        with pytest.raises(ConfigError, match="unknown methods"):
            resolve_params(family, {"name": name})


def test_the_retired_families_offer_nothing():
    # The refusal lists the family's real offering (empty for slots).
    with pytest.raises(ConfigError, match=r"known: \[\]"):
        resolve_params("acceptance", {"name": "exam"})


def test_unknown_name_lists_the_known_ones():
    with pytest.raises(
            ConfigError,
            match=r"known: \['review'\]"):
        resolve_params("review", {"name": "windows"})
    with pytest.raises(
            ConfigError,
            match=r"known: \['subtask_cards'\]"):
        resolve_params("author", {"name": "solo"})
    with pytest.raises(ConfigError, match="known:"):
        resolve_params("execution", {"name": "live_run"})


def test_required_params_still_fail_loudly():
    with pytest.raises(ConfigError, match="K"):
        resolve_params("review", {"name": "review", "K": 0})


def test_the_review_family_resolves_its_params():
    from expedistill.methods.review import ReviewParams

    assert resolve_params("review", {"name": "review", "K": 5,
                                     "retry_limit": 2}) == \
        ReviewParams(K=5, retry_limit=2)


def test_the_card_authoring_family_resolves_its_params():
    from expedistill.methods.cards import CardAuthoringParams

    assert resolve_params("author", {"name": "subtask_cards"}) == \
        CardAuthoringParams()
    assert resolve_params(
        "author", {"name": "subtask_cards",
                   "typing_chunk_size": 50,
                   "max_segments_per_card": 4,
                   "max_chars_per_segment": 700,
                   "write_retry_limit": 0}) == CardAuthoringParams(
        typing_chunk_size=50, max_segments_per_card=4,
        max_chars_per_segment=700, write_retry_limit=0)
    with pytest.raises(ConfigError, match="must be <class 'int'>"):
        resolve_params(
            "author", {"name": "subtask_cards", "typing_chunk_size": "many"})
    with pytest.raises(ConfigError, match="unknown parameter"):
        resolve_params(
            "author", {"name": "subtask_cards", "min_cluster_size": 1})


def test_the_review_family_resolves_its_defaults_and_types():
    from expedistill.methods.review import ReviewParams

    assert resolve_params("review", {"name": "review"}) == ReviewParams()
    assert resolve_params(
        "review", {"name": "review", "write_lessons": False}) == \
        ReviewParams(write_lessons=False)
    with pytest.raises(ConfigError, match="must be <class 'int'>"):
        resolve_params("review", {"name": "review", "K": "wide"})
