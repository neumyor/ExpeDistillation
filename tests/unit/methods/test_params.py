"""The method families' param contracts (methods layer; core.config
cannot know a method's params). The registry is keyed by the four content
families (delivery, execution, review, author) and every test resolves a
family's RAW mapping, exactly what a phase carries. The role and pairing
rules around these values live in the load-time checklist
(tests/unit/core/test_config_v4.py).

Resolution must work while no driver module is importable, so the tests
that load a driver point a temporary table entry at a light module.
"""

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import registry
from expedistill.methods.registry import (
    _ENTRIES, resolve, resolve_fixed, resolve_params)
from expedistill.methods.cards import CardAuthoringParams
from expedistill.methods.execution import RealFolderExecutionParams
from expedistill.methods.params import build_params


class TestDeliveryParams:
    def test_whole_bank_rejects_unknown_params(self):
        # An ineffective key under whole_bank would mislead about what
        # the run injects; the config is the experiment's identity.
        for key in ("top_k", "max_categories", "min_score"):
            with pytest.raises(ConfigError, match="whole_bank"):
                resolve_params("delivery", {"name": "whole_bank", key: 1})

    def test_two_method_family_without_a_name_rejected(self):
        with pytest.raises(ConfigError, match="needs a name"):
            resolve_params("delivery", {"verbatim": True})

    def test_unknown_method_name_lists_the_known_ones(self):
        with pytest.raises(ConfigError, match="whole_bank") as excinfo:
            resolve_params("delivery", {"name": "whole_bench"})
        for name in ("none", "folder"):
            assert repr(name) in str(excinfo.value)

class TestRealExecutionParams:
    """The execution family's ``real`` entry: what an execute phase's
    ``execution`` mapping validates at resolution.
    The role-consistency rules around these values live in the config
    loader's checklist (tests/unit/core/test_config_v4.py)."""

    def test_all_params_load(self):
        params = resolve_params("execution", {
            "name": "real",
            "arms": ["executor_strong", "executor_weak"]})
        assert params.arms == ["executor_strong", "executor_weak"]

    def test_arms_default_to_none(self):
        # None means the single weak arm -- the params object carries no
        # policy.
        params = resolve_params("execution", {"name": "real"})
        assert params.arms is None

    # A tuple would not compare equal through the JSON manifest.
    @pytest.mark.parametrize("bad", ["both", 5, [], [""], ["a", 5],
                                     ("executor_strong", "executor_weak")])
    def test_arms_must_be_a_list_of_role_names(self, bad):
        with pytest.raises(ConfigError, match="arms"):
            resolve_params("execution", {"name": "real", "arms": bad})

    @pytest.mark.parametrize("key", ["depth", "n_attempts",
                                     "max_injection_tokens"])
    def test_unknown_key_rejected(self, key):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("execution", {"name": "real", key: 3})


class TestRealFolderExecutionParams:
    """The ``real_folder`` entry: the executor and the optional sampling
    depth a practice phase sets."""

    def test_defaults(self):
        params = resolve_params("execution", {"name": "real_folder"})
        assert params == RealFolderExecutionParams()
        assert (params.executor, params.attempts) == ("executor_weak", None)

    def test_all_params_load(self):
        params = resolve_params("execution", {
            "name": "real_folder", "executor": "executor_strong",
            "attempts": 3})
        assert (params.executor, params.attempts) == ("executor_strong", 3)
        # The lower bound is inclusive.
        assert resolve_params("execution", {
            "name": "real_folder", "attempts": 1}).attempts == 1

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3"])
    def test_attempts_must_be_an_int_at_least_one(self, bad):
        with pytest.raises(ConfigError, match="attempts"):
            resolve_params("execution", {"name": "real_folder",
                                         "attempts": bad})

    def test_an_unknown_executor_is_refused(self):
        with pytest.raises(ConfigError, match="executor"):
            resolve_params("execution", {"name": "real_folder",
                                         "executor": "judge"})

    @pytest.mark.parametrize("key", ["n_attempts", "max_injection_tokens",
                                     "arms"])
    def test_unknown_key_rejected(self, key):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("execution", {"name": "real_folder", key: 1})

class TestReviewAndAuthorParams:
    def test_review_params(self):
        params = resolve_params("review", {"name": "review",
                                           "K": 1, "retry_limit": 0})
        assert (params.K, params.retry_limit) == (1, 0)
        with pytest.raises(ConfigError, match="K"):
            resolve_params("review", {"name": "review", "K": 0})

    def test_review_and_author_have_no_driver(self):
        for family, raw in (("review", {"name": "review"}),
                            ("author", {"name": "subtask_cards"})):
            with pytest.raises(ConfigError, match="no driver"):
                resolve(family, raw)


@pytest.fixture()
def light_driver_entry(monkeypatch):
    """A delivery entry whose 'driver' is the light params module, so the
    on-demand driver import is exercised without the real drivers."""
    entry = registry._Entry("expedistill.methods.delivery.params",
                            "expedistill.methods.delivery.params",
                            "NoneParams")
    monkeypatch.setitem(registry._ENTRIES, ("delivery", "light"), entry)
    return entry


class TestResolveFixed:
    """The by-name entry for call sites whose semantics are not a config
    choice, such as the bare control arm (``none``)."""

    def test_fixed_name_builds_empty_params_and_loads_the_driver(
            self, light_driver_entry):
        from expedistill.methods.delivery.params import NoneParams

        driver, params = resolve_fixed("delivery", "light")
        assert driver.__name__ == light_driver_entry.driver
        assert isinstance(params, NoneParams)

    def test_unknown_name_lists_the_known_ones(self):
        with pytest.raises(ConfigError, match="unknown methods.delivery"):
            resolve_fixed("delivery", "whole_bench")

    def test_driver_less_family_refused_by_resolve_fixed(self):
        # execution/author have no driver to load; asking for one is a
        # construction bug in the caller.
        with pytest.raises(ConfigError, match="no driver"):
            resolve_fixed("execution", "real")

    def test_resolve_imports_the_driver_on_demand(self, light_driver_entry):
        driver, params = resolve("delivery", {"name": "light"})
        assert driver.__name__ == light_driver_entry.driver

    def test_raw_mapping_must_be_a_mapping(self):
        with pytest.raises(ConfigError, match="mapping"):
            resolve_params("delivery", "whole_bank")


class TestBuildParams:
    def test_homogeneous_string_lists_are_supported(self):
        # The baseline phase's arm-role names need exactly this.
        from dataclasses import dataclass
        from typing import List, Optional

        @dataclass(frozen=True)
        class _Arms:
            arms: Optional[List[str]] = None

        params = build_params(_Arms, {"arms": ["a", "b"]}, "x", "y")
        assert params.arms == ["a", "b"]

    def test_required_params_fail_loudly(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Required:
            budget: int

        with pytest.raises(ConfigError, match="missing required parameter"):
            build_params(_Required, {}, "x", "y")

    def test_build_params_still_rejects_other_annotations(self):
        from dataclasses import dataclass
        from typing import Dict

        @dataclass(frozen=True)
        class _Bad:
            tags: Dict[str, int] = None

        with pytest.raises(ConfigError, match="unsupported"):
            build_params(_Bad, {"tags": {"a": 1}}, "x", "y")


# ---------------------------------------------------------------------------
# The registry table
# ---------------------------------------------------------------------------

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


def test_the_card_authoring_family_resolves_its_params():

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
