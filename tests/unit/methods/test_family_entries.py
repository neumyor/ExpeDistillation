"""R8's registry re-key: the five content families, pinned as data.

The v3 slot keys (segmentation.llm, authoring.distill_diagnose,
acceptance.exam, acceptance.live_run) are retired -- asking for one is a
ConfigError naming the family, never a quiet fallback. The five families
(delivery, execution, evidence, author, curation) each offer exactly the
names their phase fields can name; the params classes behind the entries
are exercised by tests/unit/methods/test_params.py.
"""

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve_params
from expedistill.methods.registry import FAMILIES, _ENTRIES


def test_the_table_is_organised_by_the_five_families():
    # R8's acceptance: five families, no slots.
    assert FAMILIES == ("delivery", "execution", "evidence", "author",
                        "curation")
    by_family = {}
    for (family, name) in _ENTRIES:
        by_family.setdefault(family, []).append(name)
    assert set(by_family) == set(FAMILIES)
    assert sorted(by_family["delivery"]) == sorted([
        "whole_bank", "retrieval", "none", "per_skill", "group_by_task",
        "folder"])
    assert sorted(by_family["execution"]) == [
        "exam", "real", "real_folder"]
    assert by_family["evidence"] == ["segment"]
    assert by_family["author"] == ["distill_diagnose"]
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
    with pytest.raises(ConfigError, match=r"known: \['segment'\]"):
        resolve_params("evidence", {"name": "windows", "K": 5})
    with pytest.raises(ConfigError, match=r"known: \['distill_diagnose'\]"):
        resolve_params("author", {"name": "solo"})
    with pytest.raises(ConfigError, match="known:"):
        resolve_params("execution", {"name": "live_run"})


def test_required_params_still_fail_loudly():
    # The empty-mapping form validates too: an entry with required fields
    # cannot be resolved without them.
    with pytest.raises(ConfigError, match="missing required parameter"):
        resolve_params("evidence", {})


def test_the_evidence_and_author_families_resolve_their_params():
    from expedistill.methods.author import AuthoringParams
    from expedistill.methods.evidence import SegmentationParams

    assert resolve_params("evidence", {"name": "segment", "K": 5,
                                       "retry_limit": 2}) == \
        SegmentationParams(K=5, retry_limit=2)
    assert resolve_params("author", {"name": "distill_diagnose"}) == \
        AuthoringParams()
