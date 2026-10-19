"""The method families' param contracts (methods layer; the v2-era
validators this replaces lived in core.config, which cannot know a
method's params). R8 re-keyed the registry to the five content families
and moved the config lookup out of the API: every test resolves a
family's RAW mapping, exactly what a v4 phase carries. The retired v3
slot keys (segmentation, authoring, acceptance) have no entries any
more -- their coverage moved to the evidence/author/execution families
here and to the load-time checklist in tests/unit/core/test_config_v4.py.
"""

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve, resolve_fixed, resolve_params
from expedistill.methods.author import AuthoringParams
from expedistill.methods.params import build_params


RETRIEVAL = {"name": "retrieval", "top_k": 5,
             "max_categories": 3, "min_score": 0.05}


class TestRetrievalParams:
    def test_all_params_load(self):
        params = resolve_params("delivery", RETRIEVAL)
        assert params.top_k == 5
        assert params.max_categories == 3
        assert params.min_score == 0.05

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("delivery", {**RETRIEVAL, "temperature": 0})

    def test_missing_required_key_rejected(self):
        for missing in ("top_k", "max_categories", "min_score"):
            delivery = {k: v for k, v in RETRIEVAL.items() if k != missing}
            with pytest.raises(ConfigError, match=missing):
                resolve_params("delivery", delivery)

    @pytest.mark.parametrize("key,bad", [
        ("top_k", 0), ("top_k", -1), ("top_k", True), ("top_k", "5"),
        ("top_k", 5.0), ("top_k", None),
        ("max_categories", 0), ("max_categories", -2),
        ("max_categories", True), ("max_categories", "3"),
        ("max_categories", None),
    ])
    def test_counts_must_be_ints_at_least_one(self, key, bad):
        with pytest.raises(ConfigError, match=key):
            resolve_params("delivery", {**RETRIEVAL, key: bad})

    @pytest.mark.parametrize("bad", [-0.1, 1.0, 1.5, True, "0.05", None])
    def test_min_score_must_be_a_number_below_one(self, bad):
        with pytest.raises(ConfigError, match="min_score"):
            resolve_params("delivery", {**RETRIEVAL, "min_score": bad})

    def test_min_score_bounds_inclusive_zero_exclusive_one(self):
        # The interval is [0, 1): the grid's lower bound is legal here.
        params = resolve_params("delivery", {**RETRIEVAL, "min_score": 0})
        assert params.min_score == 0

    def test_whole_bank_rejects_retrieval_params(self):
        # An ineffective key under whole_bank would mislead about what
        # the run injects; the config is the experiment's identity.
        for key in ("top_k", "max_categories", "min_score"):
            with pytest.raises(ConfigError, match="whole_bank"):
                resolve_params("delivery", {"name": "whole_bank",
                                             key: RETRIEVAL[key]})

    def test_two_method_family_without_a_name_rejected(self):
        with pytest.raises(ConfigError, match="needs a name"):
            resolve_params("delivery", {"top_k": 5})

    def test_unknown_method_name_lists_the_known_ones(self):
        with pytest.raises(ConfigError, match="retrieval"):
            resolve_params("delivery", {"name": "whole_bench"})


class TestRealExecutionParams:
    """The execution family's ``real`` entry (R8's new family): what a
    v4 execute phase's ``execution`` mapping validates at resolution.
    The role-consistency and depth rules around these values live in the
    config loader's checklist (tests/unit/core/test_config_v4.py)."""

    def test_all_params_load(self):
        params = resolve_params("execution", {
            "name": "real", "n_attempts": 3,
            "max_injection_tokens": 4096,
            "arms": ["executor_strong", "executor_weak"]})
        assert params.n_attempts == 3
        assert params.max_injection_tokens == 4096
        assert params.arms == ["executor_strong", "executor_weak"]

    def test_everything_defaults_to_none(self):
        # None means "the run-time global" (n_attempts -> n_attempts_weak;
        # no cap; single weak arm) -- the params object carries no policy.
        params = resolve_params("execution", {"name": "real"})
        assert params.n_attempts is None
        assert params.max_injection_tokens is None
        assert params.arms is None

    @pytest.mark.parametrize("bad", [0, -1, True, "3", 2.5])
    def test_n_attempts_must_be_a_positive_int(self, bad):
        with pytest.raises(ConfigError, match="n_attempts"):
            resolve_params("execution", {"name": "real",
                                          "n_attempts": bad})

    @pytest.mark.parametrize("bad", [0, -1, True, "4096", 4096.5])
    def test_max_injection_tokens_positive_int_or_none(self, bad):
        with pytest.raises(ConfigError, match="max_injection_tokens"):
            resolve_params("execution", {"name": "real",
                                          "max_injection_tokens": bad})

    @pytest.mark.parametrize("bad", ["both", 5, [], [""], ["a", 5]])
    def test_arms_must_be_a_list_of_role_names(self, bad):
        with pytest.raises(ConfigError, match="arms"):
            resolve_params("execution", {"name": "real", "arms": bad})

    def test_tuple_arms_rejected(self):
        # A tuple would not compare equal through the JSON manifest.
        with pytest.raises(ConfigError, match="arms"):
            resolve_params("execution", {
                "name": "real",
                "arms": ("executor_strong", "executor_weak")})

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("execution", {"name": "real", "depth": 3})


class TestExamExecutionParams:
    def test_the_exam_entry_has_no_parameters(self):
        params = resolve_params("execution", {"name": "exam"})
        assert params == resolve_params("execution", {"name": "exam"})

    def test_rejects_real_params(self):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("execution", {"name": "exam", "n_attempts": 1})


class TestEvidenceAndAuthorParams:
    def test_segment_params(self):
        params = resolve_params("evidence", {"name": "segment",
                                             "K": 1, "retry_limit": 0})
        assert (params.K, params.retry_limit) == (1, 0)
        with pytest.raises(ConfigError, match="K"):
            resolve_params("evidence", {"name": "segment", "K": 0})

    def test_author_params(self):
        # distill_diagnose is the author family's one method; its params
        # are empty (the v3 exams switch is gone from the config surface
        # AND from the params class -- a v4 config naming it is refused
        # at load, see test_config_v4).
        params = resolve_params("author", {"name": "distill_diagnose"})
        assert params == AuthoringParams()


class TestCurationParams:
    def test_agentbrew_params(self):
        params = resolve_params("curation", {
            "name": "agentbrew", "token_budget": 96000, "rounds": 3})
        assert (params.token_budget, params.rounds) == (96000, 3)
        with pytest.raises(ConfigError, match="token_budget"):
            resolve_params("curation", {"name": "agentbrew",
                                         "token_budget": 0})

    def test_none_params(self):
        none = resolve_params("curation", {"name": "none",
                                           "token_budget": 96000})
        assert none.token_budget == 96000

    def test_null_budget_is_the_no_fender_ruling(self):
        # The 2026-10-16 user ruling's v4 image: token ceilings left the
        # pre-registered parameter set, so null is a legal value for BOTH
        # curation methods -- the pre-v4 campaigns froze it, and the
        # reuse identity walk loads those manifests verbatim.
        assert resolve_params("curation", {
            "name": "agentbrew", "token_budget": None,
            "rounds": 3}).token_budget is None
        assert resolve_params("curation", {
            "name": "none", "token_budget": None}).token_budget is None
        for bad in (0, -1, "96000"):
            with pytest.raises(ConfigError, match="token_budget"):
                resolve_params("curation", {"name": "agentbrew",
                                             "token_budget": bad})
            with pytest.raises(ConfigError, match="token_budget"):
                resolve_params("curation", {"name": "none",
                                            "token_budget": bad})

    def test_agentbrew_merge_check_param(self):
        # "none" is the default since the 2026-10-19 ruling (it was
        # "exam"); "exam" runs K5's regression exams; anything else is
        # refused loudly.
        default = resolve_params("curation", {"name": "agentbrew",
                                              "token_budget": 96000})
        assert default.merge_check == "none"
        params = resolve_params("curation", {
            "name": "agentbrew", "token_budget": 96000,
            "merge_check": "exam"})
        assert params.merge_check == "exam"
        with pytest.raises(ConfigError, match="merge_check"):
            resolve_params("curation", {
                "name": "agentbrew", "token_budget": 96000,
                "merge_check": "regression"})


    def test_agentbrew_low_value_and_similarity_input_params(self):
        # The 2026-10-19 switches: both default to today's behaviour.
        default = resolve_params("curation", {"name": "agentbrew",
                                              "token_budget": None})
        assert default.drop_low_value is False
        assert default.similarity_input == "full"
        params = resolve_params("curation", {
            "name": "agentbrew", "token_budget": None,
            "drop_low_value": True, "similarity_input": "summary"})
        assert params.drop_low_value is True
        assert params.similarity_input == "summary"
        with pytest.raises(ConfigError, match="similarity_input"):
            resolve_params("curation", {
                "name": "agentbrew", "token_budget": None,
                "similarity_input": "titles"})
        with pytest.raises(ConfigError, match="drop_low_value"):
            resolve_params("curation", {
                "name": "agentbrew", "token_budget": None,
                "drop_low_value": "yes"})


class TestResolveFixed:
    """The by-name entry for call sites whose semantics are not a config
    choice: the bare control arm (``none``) and the live driver's group
    injection (``group_by_task``)."""

    def test_fixed_name_builds_empty_params_and_loads_the_driver(self):
        from expedistill.methods.delivery import NoneParams

        driver, params = resolve_fixed("delivery", "none")
        assert driver.__name__.endswith(".none")
        assert isinstance(params, NoneParams)

    def test_unknown_name_lists_the_known_ones(self):
        with pytest.raises(ConfigError, match="unknown methods.delivery"):
            resolve_fixed("delivery", "whole_bench")

    def test_required_params_still_fail_loudly(self):
        # The empty raw mapping is still validated: an entry with
        # required fields cannot be resolved by fixed name.
        with pytest.raises(ConfigError, match="missing required parameter"):
            resolve_fixed("delivery", "retrieval")

    def test_driver_less_family_refused_by_resolve_fixed(self):
        # execution/evidence/author have no driver to load; asking for one
        # is a construction bug in the caller. (evidence.segment cannot
        # stand in here: its K is required, so the refusal would fire in
        # the params build before the driver load.)
        with pytest.raises(ConfigError, match="no driver"):
            resolve_fixed("execution", "real")

    def test_resolve_imports_the_driver_on_demand(self):
        driver, params = resolve("curation", {"name": "none",
                                              "token_budget": 4096})
        assert driver.__name__.endswith(".none")
        assert params.token_budget == 4096

    def test_raw_mapping_must_be_a_mapping(self):
        with pytest.raises(ConfigError, match="mapping"):
            resolve_params("delivery", "retrieval")


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

    def test_build_params_still_rejects_other_annotations(self):
        from dataclasses import dataclass
        from typing import Dict

        @dataclass(frozen=True)
        class _Bad:
            tags: Dict[str, int] = None

        with pytest.raises(ConfigError, match="unsupported"):
            build_params(_Bad, {"tags": {"a": 1}}, "x", "y")
