"""Unit tests for ExperimentConfig and its YAML round trip."""
import dataclasses

import pytest

from expedistill.core.config import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_RULES,
    MODEL_ROLES,
    RULE_KEYS,
    ConfigError,
    ExperimentConfig,
    acceptance_mode,
    load_config,
    save_config,
)

MIN_YAML = """
benchmark: tb
mode: split
models:
  executor_strong: model-s
  executor_weak: model-w
weak_profile: "model-w is easily confused by multi-step file operations."
n_attempts_strong: 3
n_attempts_weak: 5
K: 6
split:
  ratio: 0.8
  seed: 7
rules:
  task_select: all_weak_fail
  segment_select: strong_all_weak_fail
  trajectory_select: all_attempts
"""


def make_config(**overrides):
    base = dict(
        benchmark="tb",
        mode="full",
        models={"executor_strong": "model-s", "executor_weak": "model-w"},
    )
    base.update(overrides)
    return ExperimentConfig(**base)


class TestRoleTable:
    def test_roles_default_to_strong_except_answerer(self):
        cfg = make_config()
        for role in MODEL_ROLES:
            expected = "model-w" if role in ("executor_weak", "answerer") else "model-s"
            assert cfg.models[role] == expected, role

    def test_auditor_role_defaults_to_strong(self):
        cfg = make_config()
        assert "auditor" in MODEL_ROLES
        assert cfg.models["auditor"] == "model-s"

    def test_auditor_override_wins(self):
        cfg = make_config(models={
            "executor_strong": "model-s", "executor_weak": "model-w",
            "auditor": "model-a"})
        assert cfg.models["auditor"] == "model-a"

    def test_user_override_wins(self):
        cfg = make_config(models={
            "executor_strong": "model-s", "executor_weak": "model-w",
            "judge": "model-j"})
        assert cfg.models["judge"] == "model-j"
        assert cfg.models["answerer"] == "model-w"

    def test_model_for_reports_typo(self):
        cfg = make_config()
        with pytest.raises(KeyError, match="known"):
            cfg.model_for("juge")

    def test_unknown_role_rejected(self):
        with pytest.raises(ValueError, match="model roles"):
            make_config(models={"executor_strong": "s", "executor_weak": "w",
                                "oracle": "o"})

    def test_missing_anchor_roles_rejected(self):
        with pytest.raises(ValueError, match="executor_strong"):
            make_config(models={"executor_strong": "s"})


class TestValidation:
    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            make_config(mode="hybrid")

    def test_leak_threshold_field_is_gone(self):
        with pytest.raises(TypeError):
            make_config(leak_threshold=0.3)

    def test_unknown_top_level_key_in_file_rejected(self, tmp_path):
        # Schema v2 closes the silent-drop leniency: a config FILE with an
        # unknown top-level key used to load with the stray key ignored,
        # which let "new config + old code" fall back to dead defaults (the
        # 5120 cap) with no trace. A typo must stop the run instead.
        cfg_file = tmp_path / "old.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "leak_threshold: 0.3\n",
            encoding="utf-8")
        with pytest.raises(ConfigError, match="leak_threshold"):
            load_config(str(cfg_file))

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_attempts_must_be_positive(self, attempts):
        with pytest.raises(ValueError, match="n_attempts"):
            make_config(n_attempts_weak=attempts)

    def test_split_explicit_lists(self):
        cfg = make_config(mode="split",
                          split={"train": ["tb:1"], "test": ["tb:2"]})
        assert cfg.split == {"train": ["tb:1"], "test": ["tb:2"]}

    def test_split_ratio_and_seed(self):
        cfg = make_config(split={"ratio": 0.75, "seed": 3})
        assert cfg.split == {"ratio": 0.75, "seed": 3}

    def test_split_rejects_mixed_shape(self):
        with pytest.raises(ValueError, match="mixes ratio"):
            make_config(split={"ratio": 0.5, "train": ["tb:1"]})

    def test_split_rejects_unknown_shape(self):
        with pytest.raises(ValueError, match="split"):
            make_config(split={"folds": 4})

    def test_split_ratio_bounds(self):
        with pytest.raises(ValueError, match="ratio"):
            make_config(split={"ratio": 1.0})

    def test_unknown_rule_key_rejected(self):
        with pytest.raises(ValueError, match="rule keys"):
            make_config(rules={"pick": "x"})

    def test_defaults(self):
        cfg = make_config()
        assert cfg.n_attempts_strong == 1
        assert cfg.K == 5
        assert cfg.rules == DEFAULT_RULES
        assert cfg.prompts_digests == {}
        assert cfg.split is None
        assert cfg.tasks is None
        assert cfg.token_budgets == {}


class TestModelOptions:
    def test_default_is_empty(self):
        assert make_config().model_options == {}
        assert make_config().to_dict()["model_options"] == {}

    def test_valid_options_frozen_into_to_dict(self):
        cfg = make_config(model_options={
            "model-s": {"reasoning_effort": "max"}})
        assert cfg.model_options == {"model-s": {"reasoning_effort": "max"}}
        # Part of the frozen manifest: to_dict carries it verbatim.
        assert cfg.to_dict()["model_options"] == {
            "model-s": {"reasoning_effort": "max"}}

    def test_options_keyed_by_overridden_role_model(self):
        # Any model name reachable through the role table is valid, including
        # one only a non-anchor role uses.
        cfg = make_config(models={"executor_strong": "model-s",
                                  "executor_weak": "model-w",
                                  "judge": "model-j"},
                          model_options={"model-j": {"top_p": 0.9}})
        assert cfg.to_dict()["model_options"] == {"model-j": {"top_p": 0.9}}

    def test_unknown_model_name_rejected(self):
        with pytest.raises(ValueError, match="model_options"):
            make_config(model_options={"no-such-model": {"reasoning_effort": "max"}})

    def test_non_dict_value_rejected(self):
        with pytest.raises(ValueError, match="model_options"):
            make_config(model_options={"model-s": "max"})

    def test_empty_dict_value_rejected(self):
        with pytest.raises(ValueError, match="model_options"):
            make_config(model_options={"model-s": {}})

    def test_non_json_serialisable_value_rejected(self):
        with pytest.raises(ValueError, match="model_options"):
            make_config(model_options={"model-s": {"effort": object()}})

    def test_options_not_shared_with_caller_mapping(self):
        given = {"model-s": {"reasoning_effort": "max"}}
        cfg = make_config(model_options=given)
        given["model-s"]["reasoning_effort"] = "mutated"
        assert cfg.model_options == {"model-s": {"reasoning_effort": "max"}}

    def test_yaml_round_trip_carries_model_options(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "model_options:\n"
            "  s:\n"
            "    reasoning_effort: max\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.model_options == {"s": {"reasoning_effort": "max"}}
        out = tmp_path / "out.yaml"
        save_config(cfg, str(out))
        assert load_config(str(out)) == cfg


class TestModelOptionsReservedKeys:
    """Schema v2 kills the dual-source pattern: request-shape keys are
    reserved. ``max_tokens`` used to ride inside model_options, and whether
    it won over the handle default depended on merge order -- the effective
    budget was invisible to anything that read the config. Now it must live
    in token_budgets, the single source."""

    def test_max_tokens_rejected_with_migration_guidance(self):
        with pytest.raises(ConfigError, match="token_budgets"):
            make_config(model_options={
                "model-s": {"reasoning_effort": "max", "max_tokens": 65536}})

    def test_max_tokens_alone_rejected(self):
        with pytest.raises(ConfigError, match="max_tokens"):
            make_config(model_options={"model-s": {"max_tokens": 5120}})

    @pytest.mark.parametrize("key,value", [
        ("temperature", 0.7), ("model", "other-model"),
        ("messages", []), ("stream", True)])
    def test_non_provider_knobs_rejected(self, key, value):
        with pytest.raises(ConfigError, match="not provider knobs"):
            make_config(model_options={"model-s": {key: value}})

    def test_clean_options_still_pass(self):
        # reasoning_effort is the intended use of model_options: a sampling
        # knob the provider accepts per model.
        cfg = make_config(
            model_options={"model-s": {"reasoning_effort": "max"}},
            token_budgets={"default_max_output_tokens": 65536})
        assert cfg.model_options == {"model-s": {"reasoning_effort": "max"}}


#: The pinned schema-v2 shape with every role explicit. 5120 is what the
#: answerer needs; 65536 is the endpoint-verified safe ceiling for
#: reasoning-max models (5120 burned entirely on thinking, 2026-10-11).
FULL_BUDGETS = {
    "default_max_output_tokens": 65536,
    "max_input_tokens": None,
    "roles": {
        "executor_strong": {"max_output_tokens": 65536,
                            "max_input_tokens": None},
        "executor_weak": {"max_output_tokens": 65536},
        "segmenter": {"max_output_tokens": 32768},
        "author": {"max_output_tokens": 65536},
        "examiner": {"max_output_tokens": 32768},
        "answerer": {"max_output_tokens": 5120, "max_input_tokens": 65536},
        "judge": {"max_output_tokens": 65536},
        "rewriter": {"max_output_tokens": 65536},
        "curator": {"max_output_tokens": 65536},
        "auditor": {"max_output_tokens": 65536},
    },
}


class TestTokenBudgets:
    def test_full_schema_all_roles_explicit_loads(self):
        cfg = make_config(token_budgets=FULL_BUDGETS)
        for role, entry in FULL_BUDGETS["roles"].items():
            assert cfg.max_output_tokens_for(role) == entry["max_output_tokens"]

    def test_roles_fall_back_to_default(self):
        cfg = make_config(token_budgets={
            "default_max_output_tokens": 65536,
            "roles": {"answerer": {"max_output_tokens": 5120}}})
        assert cfg.max_output_tokens_for("answerer") == 5120
        for role in MODEL_ROLES:
            if role != "answerer":
                assert cfg.max_output_tokens_for(role) == 65536, role

    def test_default_only_budgets_cover_every_role(self):
        cfg = make_config(token_budgets={"default_max_output_tokens": 65536})
        for role in MODEL_ROLES:
            assert cfg.max_output_tokens_for(role) == 65536, role

    def test_yaml_pinned_shape_loads(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "token_budgets:\n"
            "  default_max_output_tokens: 65536\n"
            "  max_input_tokens: null\n"
            "  roles:\n"
            "    executor_strong: {max_output_tokens: 65536}\n"
            "    answerer: {max_output_tokens: 5120}\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.max_output_tokens_for("executor_strong") == 65536
        assert cfg.max_output_tokens_for("answerer") == 5120
        assert cfg.token_budgets["max_input_tokens"] is None
        for role in MODEL_ROLES:
            if role not in ("executor_strong", "answerer"):
                assert cfg.max_output_tokens_for(role) == 65536, role

    def test_absent_budgets_leave_helper_fail_closed(self):
        cfg = make_config()
        assert cfg.token_budgets == {}
        with pytest.raises(ConfigError, match="token_budgets not configured"):
            cfg.max_output_tokens_for("executor_strong")

    def test_empty_budgets_are_unconfigured(self):
        cfg = make_config(token_budgets={})
        with pytest.raises(ConfigError, match="token_budgets not configured"):
            cfg.max_output_tokens_for("judge")

    def test_unknown_helper_role_is_a_caller_bug(self):
        # A typo'd role silently falling back to the default cap would make
        # a budget bug invisible; like model_for, it is a KeyError.
        cfg = make_config(token_budgets=FULL_BUDGETS)
        with pytest.raises(KeyError, match="known"):
            cfg.max_output_tokens_for("juge")

    def test_missing_default_rejected(self):
        with pytest.raises(ConfigError, match="default_max_output_tokens"):
            make_config(token_budgets={
                "roles": {"answerer": {"max_output_tokens": 5120}}})
        with pytest.raises(ConfigError, match="default_max_output_tokens"):
            make_config(token_budgets={"max_input_tokens": 100000})

    @pytest.mark.parametrize("bad", [0, -1, True, None, "65536", 65536.5])
    def test_default_must_be_positive_int(self, bad):
        with pytest.raises(ConfigError, match="default_max_output_tokens"):
            make_config(token_budgets={"default_max_output_tokens": bad})

    def test_unknown_budget_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown keys"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536, "max_output": 512})

    def test_unknown_role_rejected_with_valid_roles_listed(self):
        with pytest.raises(ConfigError, match="oracle") as excinfo:
            make_config(token_budgets={
                "default_max_output_tokens": 65536,
                "roles": {"oracle": {"max_output_tokens": 1}}})
        assert "executor_strong" in str(excinfo.value)

    def test_role_entry_requires_max_output_tokens(self):
        with pytest.raises(ConfigError, match="max_output_tokens"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536,
                "roles": {"answerer": {"max_input_tokens": 4096}}})

    @pytest.mark.parametrize("bad", [0, -5, True, None, "5120", 5120.5])
    def test_role_output_must_be_positive_int(self, bad):
        with pytest.raises(ConfigError, match="max_output_tokens"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536,
                "roles": {"answerer": {"max_output_tokens": bad}}})

    def test_global_max_input_tokens_null_or_positive_int(self):
        for value in (None, 131072):
            cfg = make_config(token_budgets={
                "default_max_output_tokens": 65536, "max_input_tokens": value})
            assert cfg.token_budgets["max_input_tokens"] == value

    @pytest.mark.parametrize("bad", [0, -1, True, "x", 1.5])
    def test_global_max_input_tokens_positive_int_or_null(self, bad):
        with pytest.raises(ConfigError, match="max_input_tokens"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536, "max_input_tokens": bad})

    def test_role_max_input_tokens_optional(self):
        cfg = make_config(token_budgets={
            "default_max_output_tokens": 65536,
            "roles": {"answerer": {"max_output_tokens": 5120,
                                   "max_input_tokens": None}}})
        assert cfg.token_budgets["roles"]["answerer"]["max_input_tokens"] is None

    def test_role_max_input_tokens_invalid_rejected(self):
        with pytest.raises(ConfigError, match="max_input_tokens"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536,
                "roles": {"answerer": {"max_output_tokens": 5120,
                                        "max_input_tokens": 0}}})

    def test_role_entry_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown keys"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536,
                "roles": {"answerer": {"max_output_tokens": 5120,
                                        "temperature": 0}}})

    @pytest.mark.parametrize("bad", [None, [], "big"])
    def test_token_budgets_must_be_a_mapping(self, bad):
        with pytest.raises(ConfigError, match="mapping"):
            make_config(token_budgets=bad)

    @pytest.mark.parametrize("bad", [None, [], "big"])
    def test_roles_must_be_a_mapping(self, bad):
        with pytest.raises(ConfigError, match="roles"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536, "roles": bad})

    def test_role_entry_must_be_a_mapping(self):
        with pytest.raises(ConfigError, match="mapping"):
            make_config(token_budgets={
                "default_max_output_tokens": 65536,
                "roles": {"answerer": 5120}})

    def test_budgets_not_shared_with_caller_mapping(self):
        given = {"default_max_output_tokens": 65536,
                 "roles": {"answerer": {"max_output_tokens": 5120}}}
        cfg = make_config(token_budgets=given)
        given["roles"]["answerer"]["max_output_tokens"] = 1
        assert cfg.max_output_tokens_for("answerer") == 5120

    def test_to_dict_carries_budgets_verbatim(self):
        # Stage 0 freezes config.to_dict() into the manifest, so the frozen
        # copy must carry exactly what the config file said.
        cfg = make_config(token_budgets=FULL_BUDGETS)
        assert cfg.to_dict()["token_budgets"] == FULL_BUDGETS

    def test_from_dict_round_trip_with_budgets(self):
        cfg = make_config(token_budgets=FULL_BUDGETS)
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg

    def test_yaml_round_trip_carries_budgets(self, tmp_path):
        cfg = make_config(token_budgets=FULL_BUDGETS)
        out = tmp_path / "out.yaml"
        save_config(cfg, str(out))
        assert load_config(str(out)) == cfg

    def test_dataclasses_replace_keeps_budgets(self):
        # Stage 0 freezes via dataclasses.replace(config, prompts_digests=..):
        # the copy must keep the budgets (and re-validate them harmlessly).
        cfg = make_config(token_budgets=FULL_BUDGETS)
        frozen = dataclasses.replace(cfg, prompts_digests={"p": "d"})
        assert frozen.token_budgets == FULL_BUDGETS
        assert frozen.max_output_tokens_for("answerer") == 5120


class TestRuleDefaults:
    def test_default_rules_cover_every_rule_key(self):
        assert set(DEFAULT_RULES) == set(RULE_KEYS)
        assert DEFAULT_RULES == {
            "task_select": "any_weak_failed",
            "segment_select": "strong_success_weak_failed",
            "trajectory_select": "all",
        }

    def test_missing_rule_keys_filled_explicit_ones_kept(self):
        cfg = make_config(rules={"trajectory_select": "custom"})
        assert cfg.rules == {
            "task_select": "any_weak_failed",
            "segment_select": "strong_success_weak_failed",
            "trajectory_select": "custom",
        }

    def test_filled_rules_are_in_to_dict(self):
        # The frozen config must carry the effective names, not the raw ones.
        assert make_config().to_dict()["rules"] == DEFAULT_RULES

    def test_default_rules_not_shared_between_configs(self):
        first = make_config()
        first.rules["task_select"] = "mutated"
        assert make_config().rules["task_select"] == "any_weak_failed"
        assert DEFAULT_RULES["task_select"] == "any_weak_failed"

    def test_rule_name_must_be_non_empty_string(self):
        with pytest.raises(ValueError, match="task_select"):
            make_config(rules={"task_select": ""})
        with pytest.raises(ValueError, match="segment_select"):
            make_config(rules={"segment_select": 3})


#: The pinned retrieval-delivery shape (protocol v2 / retrieval split).
RETRIEVAL_DELIVERY = {
    "mode": "retrieval", "top_k": 5,
    "max_categories": 3, "min_score": 0.05,
}


class TestSkillDelivery:
    def test_absent_means_whole_bank_and_is_completed(self):
        # The frozen config names the effective delivery like it names the
        # effective rule selections: absent is completed, not left empty.
        cfg = make_config()
        assert cfg.skill_delivery == {"mode": "whole_bank"}
        assert cfg.to_dict()["skill_delivery"] == {"mode": "whole_bank"}
        assert make_config(skill_delivery={}).skill_delivery == \
            {"mode": "whole_bank"}

    def test_explicit_whole_bank_loads(self):
        cfg = make_config(skill_delivery={"mode": "whole_bank"})
        assert cfg.skill_delivery == {"mode": "whole_bank"}

    def test_retrieval_with_all_params_loads(self):
        cfg = make_config(skill_delivery=RETRIEVAL_DELIVERY)
        assert cfg.skill_delivery == RETRIEVAL_DELIVERY
        # Part of the frozen manifest: to_dict carries it verbatim.
        assert cfg.to_dict()["skill_delivery"] == RETRIEVAL_DELIVERY

    def test_illegal_mode_rejected(self):
        with pytest.raises(ConfigError, match="mode"):
            make_config(skill_delivery={"mode": "top3"})

    @pytest.mark.parametrize("bad", [None, [], "retrieval", 5])
    def test_must_be_a_mapping(self, bad):
        with pytest.raises(ConfigError, match="mapping"):
            make_config(skill_delivery=bad)

    def test_unknown_keys_rejected(self):
        with pytest.raises(ConfigError, match="unknown keys"):
            make_config(skill_delivery={
                **RETRIEVAL_DELIVERY, "temperature": 0})

    def test_retrieval_requires_all_three_params(self):
        for missing in ("top_k", "max_categories", "min_score"):
            delivery = {key: value for key, value in RETRIEVAL_DELIVERY.items()
                        if key != missing}
            with pytest.raises(ConfigError, match=missing):
                make_config(skill_delivery=delivery)

    @pytest.mark.parametrize("key,bad", [
        ("top_k", 0), ("top_k", -1), ("top_k", True), ("top_k", "5"),
        ("top_k", 5.0), ("top_k", None),
        ("max_categories", 0), ("max_categories", -2),
        ("max_categories", True), ("max_categories", "3"),
        ("max_categories", None),
    ])
    def test_retrieval_counts_must_be_ints_at_least_one(self, key, bad):
        with pytest.raises(ConfigError, match=key):
            make_config(skill_delivery={**RETRIEVAL_DELIVERY, key: bad})

    @pytest.mark.parametrize("bad", [-0.1, 1.0, 1.5, True, "0.05", None])
    def test_min_score_must_be_a_number_below_one(self, bad):
        with pytest.raises(ConfigError, match="min_score"):
            make_config(skill_delivery={**RETRIEVAL_DELIVERY,
                                        "min_score": bad})

    def test_min_score_bounds_are_inclusive_zero_exclusive_one(self):
        # The interval is [0, 1): the grid's lower bound is legal here
        # (the calibration protocol rejects 0 on its own grounds).
        cfg = make_config(skill_delivery={**RETRIEVAL_DELIVERY, "min_score": 0})
        assert cfg.skill_delivery["min_score"] == 0

    def test_whole_bank_rejects_retrieval_params(self):
        # An ineffective key under whole_bank would mislead about what the
        # run injects; the config is the experiment's identity.
        for key in ("top_k", "max_categories", "min_score"):
            with pytest.raises(ConfigError, match="whole_bank"):
                make_config(skill_delivery={"mode": "whole_bank",
                                            key: RETRIEVAL_DELIVERY[key]})

    def test_not_shared_with_caller_mapping(self):
        given = dict(RETRIEVAL_DELIVERY)
        cfg = make_config(skill_delivery=given)
        given["top_k"] = 99
        assert cfg.skill_delivery["top_k"] == 5

    def test_from_dict_round_trip(self):
        cfg = make_config(skill_delivery=RETRIEVAL_DELIVERY)
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg

    def test_yaml_round_trip(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "skill_delivery:\n"
            "  mode: retrieval\n"
            "  top_k: 5\n"
            "  max_categories: 3\n"
            "  min_score: 0.05\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.skill_delivery == RETRIEVAL_DELIVERY
        out = tmp_path / "out.yaml"
        save_config(cfg, str(out))
        assert load_config(str(out)) == cfg


class TestSplitKeySpec:
    """The explicit-list ``split`` accepts exactly the two-key or the
    three-key form (same contract as D2 in blocks/tasks.py, one layer up:
    core validates the shape, D2 validates the lists against the loaded
    task set). A subset like ``{"train": [...]}`` used to pass silently."""

    def test_two_key_split_is_valid(self):
        cfg = make_config(mode="split",
                          split={"train": ["tb:1"], "test": ["tb:2"]})
        assert cfg.split == {"train": ["tb:1"], "test": ["tb:2"]}

    def test_three_key_split_is_valid(self):
        # "reserved" is the frozen partition held back from both arms.
        cfg = make_config(mode="split", split={
            "train": ["tb:1"], "test": ["tb:2"], "reserved": ["tb:3"]})
        assert cfg.split == {"train": ["tb:1"], "test": ["tb:2"],
                             "reserved": ["tb:3"]}

    def test_extra_key_rejected(self):
        with pytest.raises(ValueError, match="split"):
            make_config(split={"train": ["tb:1"], "test": ["tb:2"],
                              "val": ["tb:3"]})

    def test_missing_train_rejected(self):
        with pytest.raises(ValueError, match="split"):
            make_config(split={"test": ["tb:2"]})

    def test_missing_test_rejected(self):
        with pytest.raises(ValueError, match="split"):
            make_config(split={"train": ["tb:1"]})

    def test_reserved_must_be_a_task_id_list(self):
        with pytest.raises(ValueError, match="reserved"):
            make_config(split={"train": ["tb:1"], "test": ["tb:2"],
                              "reserved": "tb:3"})


class TestTaskSet:
    def test_task_id_list_kept(self):
        cfg = make_config(tasks=["a", "b"])
        assert cfg.tasks == ["a", "b"]
        assert cfg.to_dict()["tasks"] == ["a", "b"]

    def test_task_id_tuple_normalised_to_list(self):
        # A list round-trips through the JSON manifest; a tuple would not.
        assert make_config(tasks=("a", "b")).tasks == ["a", "b"]

    def test_task_file_path_kept(self):
        assert make_config(tasks="/abs/tasks.txt").tasks == "/abs/tasks.txt"

    @pytest.mark.parametrize("bad", [[], "", "  ", ["a", 1], 5, {"a": 1}])
    def test_bad_task_specs_rejected(self, bad):
        with pytest.raises(ValueError, match="tasks"):
            make_config(tasks=bad)

    def test_duplicate_task_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            make_config(tasks=["a", "b", "a"])


class TestSerialisation:
    def test_to_dict_covers_every_field(self):
        # Decision doc 2026-10-14, ruling A1: to_dict drops None ONLY for
        # _MANIFEST_OPTIONAL_FIELDS (acceptance), so an old-style config's
        # frozen manifest keeps its exact key set and an in-flight old run
        # still resumes on this code. Every other field is always present
        # -- split/tasks freeze as null in full mode, and a generic "skip
        # None" would delete exactly those.
        from dataclasses import fields
        payload = make_config().to_dict()
        assert "acceptance" not in payload
        assert set(payload) == ({f.name for f in fields(ExperimentConfig)}
                                - {"acceptance"})
        # A non-None acceptance is part of the frozen config like any field.
        cfg = make_config(acceptance={"mode": "exam"})
        assert set(cfg.to_dict()) == {f.name for f in fields(ExperimentConfig)}

    def test_from_dict_rejects_unknown_keys(self):
        # Unlike the data model's from_dict (lenient for stored records from
        # newer code), the config IS the experiment's identity: an unknown
        # key is a typo or a version mismatch, and dropping it silently once
        # ran a new config on old code with dead defaults. Defense in depth
        # on top of load_config's own check.
        payload = {**make_config().to_dict(), "future_field": 1}
        with pytest.raises(ConfigError, match="future_field"):
            ExperimentConfig.from_dict(payload)

    def test_schema_version_is_two(self):
        # v2: token_budgets added, model_options reserved keys, config files
        # fail loud on unknown top-level keys.
        assert CONFIG_SCHEMA_VERSION == 2

    def test_future_schema_version_rejected_by_load_config(self, tmp_path):
        cfg_file = tmp_path / "future.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "schema_version: %d\n" % (CONFIG_SCHEMA_VERSION + 1),
            encoding="utf-8")
        with pytest.raises(ConfigError, match="newer"):
            load_config(str(cfg_file))

    def test_future_schema_version_rejected_by_from_dict(self):
        payload = make_config().to_dict()
        payload["schema_version"] = CONFIG_SCHEMA_VERSION + 1
        with pytest.raises(ConfigError, match="newer"):
            ExperimentConfig.from_dict(payload)

    def test_explicit_current_schema_version_loads(self, tmp_path):
        cfg_file = tmp_path / "v2.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "schema_version: %d\n" % CONFIG_SCHEMA_VERSION,
            encoding="utf-8")
        assert load_config(str(cfg_file)).schema_version == CONFIG_SCHEMA_VERSION

    def test_older_schema_version_still_loads(self, tmp_path):
        # Only versions from the future are rejected; the current code knows
        # how to read everything it has ever written.
        cfg_file = tmp_path / "v1.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "schema_version: 1\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.schema_version == 1
        assert cfg.token_budgets == {}

    def test_non_int_schema_version_rejected(self):
        payload = make_config().to_dict()
        payload["schema_version"] = "2"
        with pytest.raises(ConfigError, match="schema_version"):
            ExperimentConfig.from_dict(payload)

    def test_unknown_key_error_lists_unknown_and_known_keys(self, tmp_path):
        # The exact trap v2 closes: "token_budget" (missing the s) silently
        # vanished under v1 leniency and the run continued uncapped at the
        # dead 5120 default. The error must name the unknown key and list
        # what a config file accepts.
        cfg_file = tmp_path / "typo.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "token_budget: {default_max_output_tokens: 65536}\n",
            encoding="utf-8")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(cfg_file))
        message = str(excinfo.value)
        assert "token_budget" in message
        assert "token_budgets" in message
        assert "weak_profile_file" in message

    def test_weak_profile_file_still_a_file_key(self, tmp_path):
        # weak_profile_file is consumed by load_config itself, so it stays a
        # known FILE key even though it is not a dataclass field.
        profile = tmp_path / "p.md"
        profile.write_text("profile", encoding="utf-8")
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "weak_profile_file: p.md\n"
            "token_budgets:\n"
            "  default_max_output_tokens: 65536\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.weak_profile == "profile"
        assert cfg.max_output_tokens_for("judge") == 65536

    def test_yaml_round_trip(self, tmp_path):
        path = tmp_path / "exp.yaml"
        path.write_text(MIN_YAML, encoding="utf-8")
        cfg = load_config(str(path))
        assert cfg.benchmark == "tb"
        assert cfg.mode == "split"
        assert cfg.split == {"ratio": 0.8, "seed": 7}
        assert cfg.rules["task_select"] == "all_weak_fail"
        assert cfg.models["judge"] == "model-s"
        assert cfg.models["answerer"] == "model-w"

        out = tmp_path / "out.yaml"
        save_config(cfg, str(out))
        assert load_config(str(out)) == cfg

    def test_weak_profile_file_resolved_relative_to_config(self, tmp_path):
        (tmp_path / "profiles").mkdir()
        profile = tmp_path / "profiles" / "weak.md"
        profile.write_text("profile text", encoding="utf-8")
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "weak_profile_file: profiles/weak.md\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.weak_profile == "profile text"
        assert "weak_profile_file" not in cfg.to_dict()

    def test_relative_tasks_path_resolved_against_config_dir(
            self, tmp_path, monkeypatch):
        conf_dir = tmp_path / "configs" / "experiments"
        conf_dir.mkdir(parents=True)
        (conf_dir / "tasks").mkdir()
        task_file = conf_dir / "tasks" / "tb_subset.txt"
        task_file.write_text("task-a\n", encoding="utf-8")
        cfg_file = conf_dir / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "tasks: tasks/tb_subset.txt\n",
            encoding="utf-8")
        # Load through a relative config path from an unrelated cwd: the
        # result must still point at the file next to the config, and be
        # absolute so a resume from another cwd freezes the same value.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        cfg = load_config("../configs/experiments/exp.yaml")
        assert cfg.tasks == str(task_file.resolve())

    def test_absolute_tasks_path_and_id_list_untouched(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "tasks: /data/tb/tasks.txt\n",
            encoding="utf-8")
        assert load_config(str(cfg_file)).tasks == "/data/tb/tasks.txt"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "tasks: [task-a, task-b]\n",
            encoding="utf-8")
        assert load_config(str(cfg_file)).tasks == ["task-a", "task-b"]

    def test_tasks_path_round_trips_through_save(self, tmp_path):
        (tmp_path / "t.txt").write_text("task-a\n", encoding="utf-8")
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "tasks: t.txt\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        out = tmp_path / "saved" / "out.yaml"
        out.parent.mkdir()
        save_config(cfg, str(out))
        assert load_config(str(out)) == cfg

    def test_relative_paths_resolve_against_real_config_through_symlink(
            self, tmp_path):
        real_dir = tmp_path / "real"
        (real_dir / "tasks").mkdir(parents=True)
        (real_dir / "tasks" / "t.txt").write_text("a\n", encoding="utf-8")
        (real_dir / "weak.md").write_text("real profile", encoding="utf-8")
        real_cfg = real_dir / "exp.yaml"
        real_cfg.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "tasks: tasks/t.txt\nweak_profile_file: weak.md\n",
            encoding="utf-8")
        # A decoy next to the symlink: it must NOT be picked up.
        link_dir = tmp_path / "links"
        (link_dir / "tasks").mkdir(parents=True)
        (link_dir / "tasks" / "t.txt").write_text("b\n", encoding="utf-8")
        (link_dir / "weak.md").write_text("decoy profile", encoding="utf-8")
        link = link_dir / "exp.yaml"
        link.symlink_to(real_cfg)
        cfg = load_config(str(link))
        assert cfg.tasks == str((real_dir / "tasks" / "t.txt").resolve())
        assert cfg.weak_profile == "real profile"

    def test_weak_profile_given_twice_rejected(self, tmp_path):
        profile = tmp_path / "p.md"
        profile.write_text("x", encoding="utf-8")
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "weak_profile: inline\nweak_profile_file: p.md\n",
            encoding="utf-8")
        with pytest.raises(ValueError, match="both"):
            load_config(str(cfg_file))

    def test_non_mapping_yaml_rejected(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            load_config(str(cfg_file))


#: Golden baselines for OLD-STYLE configs (no acceptance key), captured from
#: ``to_dict`` before the acceptance field existed (decision doc
#: 2026-10-14, ruling A1) and hardcoded as literals -- never regenerated at
#: run time, or the test would bless whatever the new code produces. Key
#: set, key order and every value must stay byte-identical: this is the
#: frozen-manifest surface, and a resumed in-flight run compares against it.
#: Two shapes, because the trap is shape-specific: in full mode split/tasks
#: are None (a generic None filter would delete them and kill the resume);
#: the split shape pins the explicit-list form.
GOLDEN_TO_DICT_KEY_ORDER = [
    "benchmark", "mode", "models", "weak_profile",
    "n_attempts_strong", "n_attempts_weak", "K", "split", "tasks",
    "segment_retry_limit", "retry_limit", "bank_token_budget",
    "curation_rounds", "rules", "skill_delivery", "prompts_digests",
    "model_options", "token_budgets", "schema_version",
]

GOLDEN_TO_DICT_FULL_FORM = {
    "benchmark": "tb",
    "mode": "full",
    "models": {
        "executor_strong": "model-s",
        "executor_weak": "model-w",
        "segmenter": "model-s",
        "author": "model-s",
        "examiner": "model-s",
        "answerer": "model-w",
        "judge": "model-s",
        "rewriter": "model-s",
        "curator": "model-s",
        "auditor": "model-s",
    },
    "weak_profile": "",
    "n_attempts_strong": 1,
    "n_attempts_weak": 1,
    "K": 5,
    "split": None,
    "tasks": None,
    "segment_retry_limit": 2,
    "retry_limit": 2,
    "bank_token_budget": 4096,
    "curation_rounds": 3,
    "rules": {
        "task_select": "any_weak_failed",
        "segment_select": "strong_success_weak_failed",
        "trajectory_select": "all",
    },
    "skill_delivery": {"mode": "whole_bank"},
    "prompts_digests": {},
    "model_options": {},
    "token_budgets": {},
    "schema_version": 2,
}

GOLDEN_TO_DICT_SPLIT_FORM = {
    "benchmark": "tb",
    "mode": "split",
    "models": {
        "executor_strong": "model-s",
        "executor_weak": "model-w",
        "segmenter": "model-s",
        "author": "model-s",
        "examiner": "model-s",
        "answerer": "model-w",
        "judge": "model-s",
        "rewriter": "model-s",
        "curator": "model-s",
        "auditor": "model-s",
    },
    "weak_profile": "model-w is easily confused by multi-step operations.",
    "n_attempts_strong": 3,
    "n_attempts_weak": 5,
    "K": 6,
    "split": {"train": ["tb:1"], "test": ["tb:2"], "reserved": ["tb:3"]},
    "tasks": ["tb:1", "tb:2", "tb:3"],
    "segment_retry_limit": 2,
    "retry_limit": 2,
    "bank_token_budget": 4096,
    "curation_rounds": 3,
    "rules": {
        "task_select": "custom",
        "segment_select": "strong_success_weak_failed",
        "trajectory_select": "all",
    },
    "skill_delivery": {"mode": "whole_bank"},
    "prompts_digests": {},
    "model_options": {},
    "token_budgets": {"default_max_output_tokens": 65536},
    "schema_version": 2,
}


class TestToDictGolden:
    def test_full_form_freezes_byte_identically(self):
        payload = make_config().to_dict()
        assert payload == GOLDEN_TO_DICT_FULL_FORM
        assert list(payload) == GOLDEN_TO_DICT_KEY_ORDER

    def test_split_form_freezes_byte_identically(self):
        cfg = make_config(
            mode="split",
            weak_profile="model-w is easily confused by multi-step "
                         "operations.",
            n_attempts_strong=3,
            n_attempts_weak=5,
            K=6,
            split={"train": ["tb:1"], "test": ["tb:2"],
                   "reserved": ["tb:3"]},
            tasks=["tb:1", "tb:2", "tb:3"],
            rules={"task_select": "custom"},
            token_budgets={"default_max_output_tokens": 65536},
        )
        payload = cfg.to_dict()
        assert payload == GOLDEN_TO_DICT_SPLIT_FORM
        assert list(payload) == GOLDEN_TO_DICT_KEY_ORDER


#: The pinned live_run acceptance shape (decision doc 2026-10-14 section
#: 2.1); n_attempts 3 = the user-agreed TB depth (= n_attempts_weak). The
#: improvement name is pre-registered in ACCEPTANCE_IMPROVE (blocks/rules.py);
#: stage 0 is what resolves it against the registry.
LIVE_RUN_ACCEPTANCE = {
    "mode": "live_run", "n_attempts": 3,
    "improvement": "strict_rate_gain",
}


class TestAcceptance:
    def test_absent_none_and_empty_all_mean_exam(self):
        assert make_config().acceptance is None
        assert make_config(acceptance=None).acceptance is None
        assert make_config(acceptance={}).acceptance == {}

    def test_acceptance_mode_resolves_the_selected_protocol(self):
        assert acceptance_mode(make_config()) == "exam"
        assert acceptance_mode(make_config(acceptance={})) == "exam"
        assert acceptance_mode(
            make_config(acceptance={"mode": "exam"})) == "exam"
        assert acceptance_mode(
            make_config(acceptance=LIVE_RUN_ACCEPTANCE)) == "live_run"

    def test_explicit_exam_mode_loads(self):
        cfg = make_config(acceptance={"mode": "exam"})
        assert cfg.acceptance == {"mode": "exam"}

    def test_live_run_loads_and_freezes_verbatim(self):
        cfg = make_config(acceptance=LIVE_RUN_ACCEPTANCE)
        assert cfg.acceptance == LIVE_RUN_ACCEPTANCE
        assert cfg.to_dict()["acceptance"] == LIVE_RUN_ACCEPTANCE

    # None is NOT in this list: it is the default and means exam.
    @pytest.mark.parametrize("bad", [[], "live_run", 5])
    def test_must_be_a_mapping(self, bad):
        with pytest.raises(ConfigError, match="mapping"):
            make_config(acceptance=bad)

    def test_unknown_keys_rejected(self):
        with pytest.raises(ConfigError, match="unknown keys"):
            make_config(acceptance={**LIVE_RUN_ACCEPTANCE, "max_rounds": 2})

    def test_keys_without_mode_rejected(self):
        with pytest.raises(ConfigError, match="mode"):
            make_config(acceptance={"n_attempts": 3,
                                    "improvement": "strict_rate_gain"})

    def test_unknown_mode_rejected(self):
        with pytest.raises(ConfigError, match="one of"):
            make_config(acceptance={"mode": "retake"})

    def test_exam_rejects_any_other_key(self):
        for key, value in (("n_attempts", 3),
                           ("improvement", "strict_rate_gain"),
                           ("max_injection_tokens", None)):
            with pytest.raises(ConfigError, match="exam"):
                make_config(acceptance={"mode": "exam", key: value})

    def test_live_run_missing_n_attempts_rejected(self):
        with pytest.raises(ConfigError, match="n_attempts"):
            make_config(acceptance={"mode": "live_run",
                                    "improvement": "strict_rate_gain"})

    def test_live_run_missing_improvement_rejected(self):
        with pytest.raises(ConfigError, match="improvement"):
            make_config(acceptance={"mode": "live_run", "n_attempts": 3})

    @pytest.mark.parametrize("bad", [1, 0, -1, True, "3", 2.5, None])
    def test_n_attempts_must_be_int_at_least_two(self, bad):
        # 1 is rejected on purpose: one attempt per arm makes the
        # comparison's false pass rate the model's own success rate.
        with pytest.raises(ConfigError, match="n_attempts"):
            make_config(acceptance={**LIVE_RUN_ACCEPTANCE,
                                    "n_attempts": bad})

    @pytest.mark.parametrize("bad", ["", None, 3])
    def test_improvement_must_be_nonempty_rule_name(self, bad):
        with pytest.raises(ConfigError, match="improvement"):
            make_config(acceptance={**LIVE_RUN_ACCEPTANCE,
                                    "improvement": bad})

    @pytest.mark.parametrize("bad", [0, -1, True, "1000", 1000.5])
    def test_max_injection_tokens_positive_int_or_null(self, bad):
        with pytest.raises(ConfigError, match="max_injection_tokens"):
            make_config(acceptance={**LIVE_RUN_ACCEPTANCE,
                                    "max_injection_tokens": bad})

    def test_max_injection_tokens_null_and_positive_int_load(self):
        for value in (None, 4096):
            cfg = make_config(acceptance={**LIVE_RUN_ACCEPTANCE,
                                          "max_injection_tokens": value})
            assert cfg.acceptance["max_injection_tokens"] == value

    def test_none_dropped_from_to_dict_everything_else_kept(self):
        assert "acceptance" not in make_config().to_dict()
        # Only None is dropped: a written {} or {mode: exam} freezes as is,
        # so the manifest shows what the source config actually said.
        assert make_config(acceptance={}).to_dict()["acceptance"] == {}
        assert (make_config(acceptance={"mode": "exam"}).to_dict()
                ["acceptance"] == {"mode": "exam"})
        assert (make_config(acceptance=LIVE_RUN_ACCEPTANCE).to_dict()
                ["acceptance"] == LIVE_RUN_ACCEPTANCE)

    def test_not_shared_with_caller_mapping(self):
        given = dict(LIVE_RUN_ACCEPTANCE)
        cfg = make_config(acceptance=given)
        given["n_attempts"] = 99
        assert cfg.acceptance["n_attempts"] == 3

    def test_from_dict_round_trip_exam(self):
        cfg = make_config()
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg

    def test_from_dict_round_trip_live_run(self):
        cfg = make_config(acceptance=LIVE_RUN_ACCEPTANCE)
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg

    def test_yaml_round_trip_live_run(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "acceptance:\n"
            "  mode: live_run\n"
            "  n_attempts: 3\n"
            "  improvement: strict_rate_gain\n"
            "  max_injection_tokens: null\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.acceptance == {**LIVE_RUN_ACCEPTANCE,
                                  "max_injection_tokens": None}
        out = tmp_path / "out.yaml"
        save_config(cfg, str(out))
        assert load_config(str(out)) == cfg
