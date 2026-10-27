"""R8's registry re-key: the method families, pinned as data.

The v3 slot keys (segmentation.llm, authoring.distill_diagnose,
acceptance.exam, acceptance.live_run) are retired -- asking for one is a
ConfigError naming the family, never a quiet fallback. Each family
offers exactly the names its phase fields can name; the params classes
behind the entries are exercised by tests/unit/methods/test_params.py.
"""

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve_params
from expedistill.methods.registry import FAMILIES, _ENTRIES


def test_the_table_is_organised_by_the_method_families():
    # R8's acceptance: named families, no slots.
    assert FAMILIES == ("delivery", "execution", "review", "author",
                        "curation")
    by_family = {}
    for (family, name) in _ENTRIES:
        by_family.setdefault(family, []).append(name)
    assert set(by_family) == set(FAMILIES)
    assert sorted(by_family["delivery"]) == sorted([
        "whole_bank", "retrieval", "none", "per_skill", "group_by_task",
        "folder", "folder_by_task"])
    assert sorted(by_family["execution"]) == [
        "exam", "real", "real_folder"]
    assert by_family["review"] == ["review"]
    assert by_family["author"] == ["distill_diagnose", "cluster_by_trigger"]
    assert sorted(by_family["curation"]) == ["agentbrew", "none"]


def test_every_retired_slot_key_is_refused():
    # The 13-entry v3 table's four slot keys are gone; the slot PACKAGES
    # (methods/segmentation, methods/authoring) no longer have entries
    # either, so nothing can resolve through them.
    for family, name in (("segmentation", "llm"),
                         ("authoring", "distill_diagnose"),
                         ("acceptance", "exam"),
                         ("acceptance", "live_run")):
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
            match=r"known: \['distill_diagnose', 'cluster_by_trigger'\]"):
        resolve_params("author", {"name": "solo"})
    with pytest.raises(ConfigError, match="known:"):
        resolve_params("execution", {"name": "live_run"})


def test_required_params_still_fail_loudly():
    with pytest.raises(ConfigError, match="K"):
        resolve_params("review", {"name": "review", "K": 0})


def test_the_review_and_author_families_resolve_their_params():
    from expedistill.methods.author import AuthoringParams
    from expedistill.methods.review import ReviewParams

    assert resolve_params("review", {"name": "review", "K": 5,
                                     "retry_limit": 2}) == \
        ReviewParams(K=5, retry_limit=2)
    assert resolve_params("author", {"name": "distill_diagnose"}) == \
        AuthoringParams()


def test_the_cluster_authoring_family_resolves_its_params():
    from expedistill.methods.author import ClusterAuthoringParams

    assert resolve_params(
        "author", {"name": "cluster_by_trigger"}) == \
        ClusterAuthoringParams()
    assert resolve_params(
        "author", {"name": "cluster_by_trigger",
                   "max_cards_per_prompt": 12,
                   "max_chars_per_card": 700,
                   "min_cluster_size": 2,
                   "classify_retry_limit": 1,
                   "cluster_retry_limit": 3,
                   "skill_retry_limit": 4}) == ClusterAuthoringParams(
        max_cards_per_prompt=12, max_chars_per_card=700,
        min_cluster_size=2, classify_retry_limit=1,
        cluster_retry_limit=3, skill_retry_limit=4)
    with pytest.raises(ConfigError, match="must be <class 'int'>"):
        resolve_params(
            "author", {"name": "cluster_by_trigger",
                       "max_cards_per_prompt": "many"})


def test_the_review_family_resolves_its_window_params():
    from expedistill.methods.review import ReviewParams

    assert resolve_params("review", {"name": "review"}) == ReviewParams()
    assert resolve_params(
        "review", {"name": "review", "before_steps": 3,
                   "after_steps": 4}) == \
        ReviewParams(before_steps=3, after_steps=4)
    with pytest.raises(ConfigError, match="must be <class 'int'>"):
        resolve_params("review", {"name": "review",
                                  "before_steps": "wide"})
