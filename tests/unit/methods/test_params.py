"""The method slots' param contracts (methods layer; the v2-era validators
this replaces lived in core.config, which cannot know a method's params).

Every case here was a core-level validation test before the v3 switch;
they moved wholesale so the contracts live with their owners.
"""

import pytest

from expedistill.core.config import ConfigError, ExperimentConfig
from expedistill.methods import resolve_params
from expedistill.methods.params import build_params


def make_config(**overrides):
    base = dict(
        benchmark="tb",
        mode="full",
        models={"executor_strong": "model-s", "executor_weak": "model-w"},
        n_attempts_weak=3,
    )
    base.update(overrides)
    return ExperimentConfig(**base)


RETRIEVAL = {"name": "retrieval", "top_k": 5,
             "max_categories": 3, "min_score": 0.05}


class TestRetrievalParams:
    def test_all_params_load(self):
        params = resolve_params(
            "delivery", make_config(methods={"delivery": RETRIEVAL}))
        assert params.top_k == 5
        assert params.max_categories == 3
        assert params.min_score == 0.05

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("delivery", make_config(methods={
                "delivery": {**RETRIEVAL, "temperature": 0}}))

    def test_missing_required_key_rejected(self):
        for missing in ("top_k", "max_categories", "min_score"):
            delivery = {k: v for k, v in RETRIEVAL.items() if k != missing}
            with pytest.raises(ConfigError, match=missing):
                resolve_params("delivery", make_config(
                    methods={"delivery": delivery}))

    @pytest.mark.parametrize("key,bad", [
        ("top_k", 0), ("top_k", -1), ("top_k", True), ("top_k", "5"),
        ("top_k", 5.0), ("top_k", None),
        ("max_categories", 0), ("max_categories", -2),
        ("max_categories", True), ("max_categories", "3"),
        ("max_categories", None),
    ])
    def test_counts_must_be_ints_at_least_one(self, key, bad):
        with pytest.raises(ConfigError, match=key):
            resolve_params("delivery", make_config(
                methods={"delivery": {**RETRIEVAL, key: bad}}))

    @pytest.mark.parametrize("bad", [-0.1, 1.0, 1.5, True, "0.05", None])
    def test_min_score_must_be_a_number_below_one(self, bad):
        with pytest.raises(ConfigError, match="min_score"):
            resolve_params("delivery", make_config(
                methods={"delivery": {**RETRIEVAL, "min_score": bad}}))

    def test_min_score_bounds_inclusive_zero_exclusive_one(self):
        # The interval is [0, 1): the grid's lower bound is legal here.
        params = resolve_params("delivery", make_config(
            methods={"delivery": {**RETRIEVAL, "min_score": 0}}))
        assert params.min_score == 0

    def test_whole_bank_rejects_retrieval_params(self):
        # An ineffective key under whole_bank would mislead about what
        # the run injects; the config is the experiment's identity.
        for key in ("top_k", "max_categories", "min_score"):
            with pytest.raises(ConfigError, match="whole_bank"):
                resolve_params("delivery", make_config(
                    methods={"delivery": {"name": "whole_bank",
                                         key: RETRIEVAL[key]}}))


LIVE = {"name": "live_run", "n_attempts": 3,
        "improvement": "strict_rate_gain"}


class TestLiveRunParams:
    def test_all_params_load(self):
        params = resolve_params(
            "acceptance", make_config(methods={"acceptance": LIVE}))
        assert params.n_attempts == 3
        assert params.improvement == "strict_rate_gain"
        assert params.max_injection_tokens is None

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown parameter"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {**LIVE, "max_rounds": 2}}))

    def test_missing_required_keys_rejected(self):
        for missing in ("n_attempts", "improvement"):
            acceptance = {k: v for k, v in LIVE.items() if k != missing}
            with pytest.raises(ConfigError, match=missing):
                resolve_params("acceptance", make_config(
                    methods={"acceptance": acceptance}))

    @pytest.mark.parametrize("bad", [1, 0, -1, True, "3", 2.5, None])
    def test_n_attempts_must_be_int_at_least_two(self, bad):
        # 1 is rejected on purpose: one attempt per arm makes the
        # comparison's false pass rate the model's own success rate.
        with pytest.raises(ConfigError, match="n_attempts"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {**LIVE, "n_attempts": bad}}))

    @pytest.mark.parametrize("bad", ["", None, 3])
    def test_improvement_must_be_nonempty_rule_name(self, bad):
        with pytest.raises(ConfigError, match="improvement"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {**LIVE, "improvement": bad}}))

    @pytest.mark.parametrize("bad", [0, -1, True, "1000", 1000.5])
    def test_max_injection_tokens_positive_int_or_null(self, bad):
        with pytest.raises(ConfigError, match="max_injection_tokens"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {**LIVE,
                                        "max_injection_tokens": bad}}))

    def test_max_injection_tokens_null_and_positive_int_load(self):
        for value in (None, 4096):
            params = resolve_params("acceptance", make_config(
                methods={"acceptance": {**LIVE,
                                        "max_injection_tokens": value}}))
            assert params.max_injection_tokens == value

    def test_unknown_improvement_rule_name_rejected(self):
        # The registry check lives in blocks.rules, which raises infra's
        # ConfigError (a ReliabilityError, not core's ValueError subclass).
        from expedistill.infra.reliability import ConfigError as InfraError
        with pytest.raises(InfraError, match="unknown acceptance improve"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {**LIVE,
                                        "improvement": "no_such_rule"}}))

    def test_n_attempts_may_not_exceed_the_weak_depth(self):
        # The cross-constraint (decision doc 2026-10-14): the stage-1
        # baseline units exist by id, so a deeper live arm would report
        # missing baseline units as infrastructure errors.
        with pytest.raises(ConfigError, match="n_attempts_weak"):
            resolve_params("acceptance", make_config(
                n_attempts_weak=2,
                methods={"acceptance": LIVE}))

    def test_exam_rejects_live_params(self):
        for key, value in (("n_attempts", 3),
                           ("improvement", "strict_rate_gain"),
                           ("max_injection_tokens", None)):
            with pytest.raises(ConfigError, match="unknown parameter"):
                resolve_params("acceptance", make_config(
                    methods={"acceptance": {"name": "exam",
                                            key: value}}))

    def test_unknown_method_name_lists_the_known_ones(self):
        with pytest.raises(ConfigError, match="live_run"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {"name": "retake"}}))

    def test_two_method_slot_without_a_name_rejected(self):
        with pytest.raises(ConfigError, match="needs a name"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {"n_attempts": 3}}))


class TestExamAndOtherSlots:
    def test_exam_retry_limit_must_be_non_negative(self):
        with pytest.raises(ConfigError, match="retry_limit"):
            resolve_params("acceptance", make_config(
                methods={"acceptance": {"name": "exam",
                                        "retry_limit": -1}}))

    def test_segmentation_params(self):
        params = resolve_params("segmentation", make_config(
            methods={"segmentation": {"K": 1, "retry_limit": 0}}))
        assert (params.K, params.retry_limit) == (1, 0)
        with pytest.raises(ConfigError, match="K"):
            resolve_params("segmentation", make_config(
                methods={"segmentation": {"K": 0}}))

    def test_curation_params(self):
        params = resolve_params("curation", make_config(
            methods={"curation": {"name": "agentbrew",
                                  "token_budget": 96000, "rounds": 3}}))
        assert (params.token_budget, params.rounds) == (96000, 3)
        with pytest.raises(ConfigError, match="token_budget"):
            resolve_params("curation", make_config(
                methods={"curation": {"name": "agentbrew",
                                      "token_budget": 0}}))
        none = resolve_params("curation", make_config(
            methods={"curation": {"name": "none",
                                   "token_budget": 96000}}))
        assert none.token_budget == 96000

    def test_defaults_satisfy_every_contract(self):
        # METHOD_DEFAULTS is what an absent slot completes to; if a
        # contract tightened, this pins the mismatch at the default.
        config = make_config()
        for slot in ("segmentation", "authoring", "curation", "delivery"):
            resolve_params(slot, config)

    def test_build_params_rejects_unsupported_annotations(self):
        from dataclasses import dataclass
        from typing import List

        @dataclass(frozen=True)
        class _Bad:
            tags: List[str] = None

        with pytest.raises(ConfigError, match="unsupported"):
            build_params(_Bad, {"tags": ["a"]}, "x", "y")


class TestAuthoringExamsSwitch:
    """P4: the exams-off ablation pair (curation: none + live_run) and the
    centralized conflicts the slot's check refuses."""

    def test_conflict_with_exam_acceptance(self):
        with pytest.raises(ConfigError, match="acceptance method"):
            resolve_params("authoring", make_config(
                n_attempts_weak=3,
                methods={"authoring": {"exams": False}}))

    def test_conflict_with_agentbrew_curation(self):
        # Acceptance must be live_run first, or the acceptance conflict
        # fires before the curation one (checks refuse in order).
        with pytest.raises(ConfigError, match="curation method"):
            resolve_params("authoring", make_config(
                n_attempts_weak=3,
                methods={"authoring": {"exams": False},
                         "acceptance": {"name": "live_run",
                                        "n_attempts": 3,
                                        "improvement": "strict_rate_gain"},
                         "curation": {"name": "agentbrew",
                                      "token_budget": 96000, "rounds": 3}}))

    def test_the_ablation_pair_resolves(self):
        params = resolve_params("authoring", make_config(
            n_attempts_weak=3,
            methods={"authoring": {"exams": False},
                     "curation": {"name": "none", "token_budget": 96000},
                     "acceptance": {"name": "live_run", "n_attempts": 3,
                                    "improvement": "strict_rate_gain"}}))
        assert params.exams is False


class TestProviderRegistry:
    """P5's provider seam: a plain name -> builder table, the environment
    names the provider, unknown names are loud."""

    def test_default_is_openai_compat(self, monkeypatch):
        monkeypatch.delenv("EXPEDISTILL_LLM_PROVIDER", raising=False)
        from expedistill.llm.providers import PROVIDERS, default_client
        import expedistill.llm.providers as providers_mod
        assert set(providers_mod.PROVIDERS) == {"openai_compat"}
        seen = []
        monkeypatch.setitem(PROVIDERS, "openai_compat",
                            lambda timeout_sec: seen.append(timeout_sec))
        client = default_client(timeout_sec=12.0)
        assert seen == [12.0]
        assert client is None  # the stub's return

    def test_unknown_provider_is_a_config_error(self, monkeypatch):
        monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "quantum")
        from expedistill.infra.reliability import ConfigError as InfraError
        from expedistill.llm.providers import default_client
        with pytest.raises(InfraError, match="quantum"):
            default_client()

    def test_env_names_provider(self, monkeypatch):
        import expedistill.llm.providers as providers_mod
        monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "stub")
        monkeypatch.setitem(providers_mod.PROVIDERS, "stub",
                            lambda timeout_sec: "stub-client")
        assert providers_mod.default_client(timeout_sec=1.0) == "stub-client"
