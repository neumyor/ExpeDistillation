"""Unit tests for ExperimentConfig and its YAML round trip (schema v4)."""
import copy
import dataclasses

import pytest

from expedistill.core.config import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_RULES,
    ROLE_DEFAULTS,
    RULE_KEYS,
    ConfigError,
    ExperimentConfig,
    load_config,
    save_config,
    upgrade_v2_config,
    upgrade_v3_config,
)

#: The minimal v4 method the fixtures carry: a two-arm baseline, one learn
#: phase, one final test. Small on purpose -- the sequence/phase-shape
#: checklist has its own file (tests/unit/core/test_config_v4.py); here it
#: only needs to be legal.
MIN_SEQUENCE = ["baseline", "learn", "test"]

MIN_PHASES = {
    "baseline": {"kind": "execute", "role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]}},
    "learn": {"kind": "learn",
          "evidence": {"name": "segment", "K": 5},
          "author": {"name": "distill_diagnose"},
          "curate": {"name": "none", "token_budget": 4096}},
    "test": {"kind": "execute", "role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "fresh_bare"}},
}

#: The sequence/phases block every loadable YAML fixture carries, in the
#: same inline spelling a config file uses.
YAML_METHOD = (
    "sequence: [baseline, learn, test]\n"
    "phases:\n"
    "  baseline: {kind: execute, role: baseline, tasks: train,"
    " delivery: {name: none},"
    " execution: {name: real, arms: [executor_strong, executor_weak]}}\n"
    "  learn: {kind: learn, evidence: {name: segment, K: 5},"
    " author: {name: distill_diagnose},"
    " curate: {name: none, token_budget: 4096}}\n"
    "  test: {kind: execute, role: test, tasks: test,"
    " delivery: {name: whole_bank}, execution: {name: real},"
    " compare: {against: fresh_bare}}\n"
)

MIN_YAML = f"""
benchmark: tb
mode: split
models:
  executor_strong: model-s
  executor_weak: model-w
weak_profile: "model-w is easily confused by multi-step file operations."
n_attempts_strong: 3
n_attempts_weak: 5
schema_version: 4
{YAML_METHOD}split:
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
        sequence=list(MIN_SEQUENCE),
        phases=copy.deepcopy(MIN_PHASES),
    )
    base.update(overrides)
    return ExperimentConfig(**base)


class TestRoleTable:
    def test_roles_default_to_strong_except_answerer(self):
        cfg = make_config()
        for role in ROLE_DEFAULTS:
            expected = "model-w" if role in ("executor_weak", "answerer") else "model-s"
            assert cfg.models[role] == expected, role

    def test_auditor_role_defaults_to_strong(self):
        cfg = make_config()
        assert cfg.models["auditor"] == "model-s"

    def test_auditor_override_wins(self):
        cfg = make_config(models={"executor_strong": "model-s",
                                  "executor_weak": "model-w",
                                  "auditor": "model-a"})
        assert cfg.models["auditor"] == "model-a"

    def test_user_override_wins(self):
        cfg = make_config(models={"executor_strong": "model-s",
                                  "executor_weak": "model-w",
                                  "answerer": "model-s"})
        assert cfg.models["answerer"] == "model-s"

    def test_unknown_role_rejected(self):
        with pytest.raises(ValueError, match="oracle"):
            make_config(models={"executor_strong": "model-s",
                                "executor_weak": "model-w",
                                "oracle": "model-o"})

    def test_missing_anchor_roles_rejected(self):
        with pytest.raises(ValueError, match="executor_strong"):
            make_config(models={"executor_weak": "model-w"})


class TestValidation:
    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            make_config(mode="hybrid")

    def test_leak_threshold_field_is_gone(self):
        with pytest.raises(TypeError):
            make_config(leak_threshold=0.3)

    def test_methods_field_is_gone(self):
        # R9: the v3 five-slot mapping is not a valid constructor argument
        # any more -- the method lives in the phase definitions.
        with pytest.raises(TypeError):
            make_config(methods={"delivery": {"name": "whole_bank"}})

    def test_unknown_top_level_key_in_file_rejected(self, tmp_path):
        # A config FILE with an unknown top-level key used to load with
        # the stray key ignored, which let "new config + old code" fall
        # back to dead defaults with no trace. A typo must stop the run.
        cfg_file = tmp_path / "old.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "schema_version: 4\n"
            "leak_threshold: 0.3\n",
            encoding="utf-8")
        with pytest.raises(ConfigError, match="leak_threshold"):
            load_config(str(cfg_file))

    @pytest.mark.parametrize("attempts", [0, -1, True, 1.5, "2"])
    def test_attempts_must_be_positive(self, attempts):
        with pytest.raises(ValueError, match="n_attempts"):
            make_config(n_attempts_weak=attempts)

    @pytest.mark.parametrize("split", [
        {"train": None, "test": ["a"]},
        {"train": ["a"], "test": None},
    ])
    def test_split_train_and_test_lists_are_required(self, split):
        # Only the held-back reserved list may be null.
        with pytest.raises(ValueError, match="must be a list"):
            make_config(mode="split", split=split)

    def test_split_reserved_may_be_null(self):
        make_config(mode="split",
                    split={"train": ["a"], "test": ["b"], "reserved": None})

    @pytest.mark.parametrize("sequence", ["DLC", None, []])
    def test_sequence_must_be_a_list_before_parsing(self, sequence):
        # A string must not be split into one-letter phase names, and a
        # null must surface as a ConfigError, not a TypeError.
        with pytest.raises(ConfigError, match="sequence must be"):
            make_config(sequence=sequence)

    @pytest.mark.parametrize("phases", [[], None, {}])
    def test_phases_must_be_a_mapping_before_parsing(self, phases):
        with pytest.raises(ConfigError, match="phases must be"):
            make_config(phases=phases)

    def test_test_role_refuses_the_exam_execution(self):
        phases = copy.deepcopy(MIN_PHASES)
        phases["test"]["execution"] = {"name": "exam"}
        with pytest.raises(ConfigError,
                           match="that role runs 'real' or 'real_folder'"):
            make_config(phases=phases)

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
        assert cfg.sequence == MIN_SEQUENCE
        assert set(cfg.phases) == set(MIN_SEQUENCE)
        assert cfg.invalid_redispatch_limit == 1
        assert cfg.rules == DEFAULT_RULES
        assert cfg.prompts_digests == {}
        assert cfg.split is None
        assert cfg.tasks is None
        assert cfg.token_budgets == {}

    def test_invalid_redispatch_limit_must_be_a_positive_int(self):
        for bad in (0, -1, True, "1", 1.5, None):
            with pytest.raises(ConfigError, match="invalid_redispatch"):
                make_config(invalid_redispatch_limit=bad)


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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
            "model_options:\n"
            "  s:\n"
            "    reasoning_effort: max\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.model_options == {"s": {"reasoning_effort": "max"}}
        assert cfg.to_dict()["model_options"] == {"s": {
            "reasoning_effort": "max"}}


class TestModelOptionsReservedKeys:
    # ``max_tokens`` is owned by ``token_budgets`` -- a second source
    # silently won or lost against the real one depending on merge order --
    # and the rest are not provider knobs: ``model``/``messages``/``stream``
    # ARE the request, and ``temperature`` belongs to the client. Only
    # per-model sampling knobs (reasoning_effort) are forwarded.
    @pytest.mark.parametrize("key", ["max_tokens", "model", "messages",
                                     "stream", "temperature"])
    def test_reserved_keys_rejected(self, key):
        with pytest.raises(ConfigError, match="reserved key"):
            make_config(model_options={"model-s": {key: 1}})

    def test_max_tokens_rejected_with_migration_guidance(self):
        with pytest.raises(ConfigError, match="token_budgets"):
            make_config(model_options={"model-s": {"max_tokens": 512}})

    def test_max_tokens_alone_rejected(self):
        with pytest.raises(ConfigError, match="max_tokens lives"):
            make_config(model_options={"model-s": {"max_tokens": 512}})

    @pytest.mark.parametrize("key,value", [
        ("model", "other-model"),
        ("messages", []),
        ("stream", True),
        ("temperature", 0.7),
    ])
    def test_non_provider_knobs_rejected(self, key, value):
        with pytest.raises(ConfigError, match="not provider knobs"):
            make_config(model_options={"model-s": {key: value}})

    def test_clean_options_still_pass(self):
        cfg = make_config(model_options={"model-s": {
            "reasoning_effort": "max", "top_p": 0.9}})
        assert cfg.model_options == {"model-s": {
            "reasoning_effort": "max", "top_p": 0.9}}


#: The pinned token-budget shape with every role explicit. 5120 is what the
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
        for role in ROLE_DEFAULTS:
            if role != "answerer":
                assert cfg.max_output_tokens_for(role) == 65536, role

    def test_default_only_budgets_cover_every_role(self):
        cfg = make_config(token_budgets={"default_max_output_tokens": 65536})
        for role in ROLE_DEFAULTS:
            assert cfg.max_output_tokens_for(role) == 65536, role

    def test_yaml_pinned_shape_loads(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
        for role in ROLE_DEFAULTS:
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
        # a budget bug invisible; it is a KeyError.
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


class TestPhaseFields:
    """The v4 method surface on ExperimentConfig: the constructor accepts
    raw mappings OR already-parsed specs, everything re-parsed through
    the checklist, and the parsed phases never alias the caller's
    mappings. The checklist itself lives in
    tests/unit/core/test_config_v4.py."""

    def test_parsed_specs_are_the_dataclasses(self):
        from expedistill.core.config import (
            ExecutePhaseSpec,
            LearnPhaseSpec,
        )
        cfg = make_config()
        assert isinstance(cfg.phases["baseline"], ExecutePhaseSpec)
        assert isinstance(cfg.phases["learn"], LearnPhaseSpec)

    def test_parsed_specs_accepted_back(self):
        # dataclasses.replace on a loaded config re-parses harmlessly.
        cfg = make_config()
        cfg2 = ExperimentConfig(**cfg.to_dict())
        assert cfg2 == cfg

    def test_not_shared_with_caller_mapping(self):
        given = copy.deepcopy(MIN_PHASES)
        cfg = make_config(phases=given)
        given["learn"]["curate"]["token_budget"] = 99
        assert cfg.phases["learn"].curate["token_budget"] == 4096

    def test_from_dict_round_trip(self):
        cfg = make_config()
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg
        # The raw (freezable) form of the phases re-parses to the same
        # specs -- B4's resume comparison depends on that equality.
        assert ExperimentConfig.from_dict(cfg.to_dict()).to_dict() == \
            cfg.to_dict()

    def test_yaml_round_trip(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD,
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.sequence == ["baseline", "learn", "test"]
        assert cfg.phases["test"].delivery == {"name": "whole_bank"}
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
        # v4: every field freezes, always -- there is no optional-field
        # whitelist to keep an in-flight run's key set stable.
        from dataclasses import fields
        payload = make_config().to_dict()
        assert set(payload) == {f.name for f in fields(ExperimentConfig)}
        assert set(payload["phases"]) == set(MIN_SEQUENCE)

    def test_from_dict_rejects_unknown_keys(self):
        # Unlike the data model's from_dict (lenient for stored records from
        # newer code), the config IS the experiment's identity: an unknown
        # key is a typo or a version mismatch, and dropping it silently once
        # ran a new config on old code with dead defaults. Defense in depth
        # on top of load_config's own check.
        payload = {**make_config().to_dict(), "future_field": 1}
        with pytest.raises(ConfigError, match="future_field"):
            ExperimentConfig.from_dict(payload)

    def test_schema_version_is_four(self):
        # v4: the method is a phase sequence; every method parameter lives
        # in the phase definitions.
        assert CONFIG_SCHEMA_VERSION == 4

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
        cfg_file = tmp_path / "v4.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD,
            encoding="utf-8")
        assert load_config(str(cfg_file)).schema_version == CONFIG_SCHEMA_VERSION

    def test_v3_payload_rejected_with_converter_pointer(self):
        # v3 configs are refused with the upgrade mapping AND the
        # converter's name (the acceptance criterion for the version gate).
        payload = make_config().to_dict()
        payload["schema_version"] = 3
        with pytest.raises(ConfigError) as excinfo:
            ExperimentConfig.from_dict(payload)
        message = str(excinfo.value)
        assert "upgrade_v3_config" in message
        assert "convert_v3_configs" in message

    def test_v3_file_rejected_with_converter_pointer(self, tmp_path):
        cfg_file = tmp_path / "v3.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "schema_version: 3\n"
            "methods:\n  segmentation: {K: 6}\n",
            encoding="utf-8")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(cfg_file))
        message = str(excinfo.value)
        assert "upgrade_v3_config" in message
        assert "convert_v3_configs" in message

    def test_v2_file_rejected_with_converter_pointer(self, tmp_path):
        # v2 files are frozen experiment records; this code speaks v4 and
        # refuses the old shape with the converters' names instead of a
        # wall of unknown-key errors.
        cfg_file = tmp_path / "v2.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "K: 6\n",
            encoding="utf-8")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(cfg_file))
        assert "convert_v3_configs" in str(excinfo.value)

    def test_v2_payload_rejected_by_from_dict(self):
        payload = make_config().to_dict()
        payload["schema_version"] = 2
        with pytest.raises(ConfigError, match="convert"):
            ExperimentConfig.from_dict(payload)

    def test_non_int_schema_version_rejected(self):
        payload = make_config().to_dict()
        payload["schema_version"] = "4"
        with pytest.raises(ConfigError, match="schema_version"):
            ExperimentConfig.from_dict(payload)

    def test_unknown_key_error_lists_unknown_and_known_keys(self, tmp_path):
        # The exact trap the loud loader closes: "token_budget" (missing
        # the s) silently vanished under v1 leniency and the run continued
        # uncapped at the dead 5120 default. The error must name the
        # unknown key and list what a config file accepts.
        cfg_file = tmp_path / "typo.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
        assert cfg.phases["learn"].evidence["K"] == 5
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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
            "tasks: /data/tb/tasks.txt\n",
            encoding="utf-8")
        assert load_config(str(cfg_file)).tasks == "/data/tb/tasks.txt"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
            "tasks: [task-a, task-b]\n",
            encoding="utf-8")
        assert load_config(str(cfg_file)).tasks == ["task-a", "task-b"]

    def test_tasks_path_round_trips_through_save(self, tmp_path):
        (tmp_path / "t.txt").write_text("task-a\n", encoding="utf-8")
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
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
            f"schema_version: {CONFIG_SCHEMA_VERSION}\n"
            + YAML_METHOD +
            "weak_profile: inline\nweak_profile_file: p.md\n",
            encoding="utf-8")
        with pytest.raises(ValueError, match="both"):
            load_config(str(cfg_file))

    def test_non_mapping_yaml_rejected(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            load_config(str(cfg_file))


#: Golden baselines for the v4 to_dict payload, hardcoded as literals --
#: never regenerated at run time, or the test would bless whatever the new
#: code produces. Key set, key order and every value must stay
#: byte-identical: this is the frozen-manifest surface, and a resumed
#: in-flight run compares against it. Two shapes, because the trap is
#: shape-specific: in full mode split/tasks are None (a generic None
#: filter would delete them and kill the resume); the split shape pins the
#: explicit-list form.
GOLDEN_TO_DICT_KEY_ORDER = [
    "benchmark", "mode", "models", "weak_profile",
    "n_attempts_strong", "n_attempts_weak", "split", "tasks",
    "sequence", "phases", "invalid_redispatch_limit",
    "rules", "prompts_digests",
    "model_options", "token_budgets", "schema_version",
]

#: The canonical (parsed, all-keys) form of the fixture's phases.
GOLDEN_PHASES = {
    "baseline": {"role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]},
          "compare": None, "acceptance": None, "kind": "execute"},
    "learn": {"evidence": {"name": "segment", "K": 5},
          "author": {"name": "distill_diagnose"},
          "curate": {"name": "none", "token_budget": 4096},
          "kind": "learn", "role": "learn"},
    "test": {"role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "fresh_bare", "rule": None,
                      "external": None},
          "acceptance": None, "kind": "execute"},
}

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
    "split": None,
    "tasks": None,
    "sequence": ["baseline", "learn", "test"],
    "phases": GOLDEN_PHASES,
    "invalid_redispatch_limit": 1,
    "rules": {
        "task_select": "any_weak_failed",
        "segment_select": "strong_success_weak_failed",
        "trajectory_select": "all",
    },
    "prompts_digests": {},
    "model_options": {},
    "token_budgets": {},
    "schema_version": 4,
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
    "split": {"train": ["tb:1"], "test": ["tb:2"], "reserved": ["tb:3"]},
    "tasks": ["tb:1", "tb:2", "tb:3"],
    "sequence": ["baseline", "learn", "test"],
    "phases": {
        **GOLDEN_PHASES,
        # A GIVEN phase freezes verbatim -- no per-key merge with any
        # default (a merge would smuggle one method's params into
        # another's phase).
        "learn": {"evidence": {"name": "segment", "K": 6},
              "author": {"name": "distill_diagnose"},
              "curate": {"name": "none", "token_budget": 4096},
              "kind": "learn", "role": "learn"},
    },
    "invalid_redispatch_limit": 1,
    "rules": {
        "task_select": "custom",
        "segment_select": "strong_success_weak_failed",
        "trajectory_select": "all",
    },
    "prompts_digests": {},
    "model_options": {},
    "token_budgets": {"default_max_output_tokens": 65536},
    "schema_version": 4,
}


class TestToDictGolden:
    def test_full_form_freezes_byte_identically(self):
        payload = make_config().to_dict()
        assert payload == GOLDEN_TO_DICT_FULL_FORM
        assert list(payload) == GOLDEN_TO_DICT_KEY_ORDER

    def test_split_form_freezes_byte_identically(self):
        phases = copy.deepcopy(MIN_PHASES)
        phases["learn"]["evidence"]["K"] = 6
        cfg = make_config(
            mode="split",
            weak_profile="model-w is easily confused by multi-step "
                         "operations.",
            n_attempts_strong=3,
            n_attempts_weak=5,
            phases=phases,
            split={"train": ["tb:1"], "test": ["tb:2"],
                   "reserved": ["tb:3"]},
            tasks=["tb:1", "tb:2", "tb:3"],
            rules={"task_select": "custom"},
            token_budgets={"default_max_output_tokens": 65536},
        )
        payload = cfg.to_dict()
        assert payload == GOLDEN_TO_DICT_SPLIT_FORM
        assert list(payload) == GOLDEN_TO_DICT_KEY_ORDER


class TestUpgradeV2:
    """The one-time v2 -> v3 mapping (pure stdlib): the converter script and
    the reuse assembler's v2-source identity walk both go through it, so
    the mapping itself is pinned here. Its output is a v3 mapping; loading
    it needs the v3 -> v4 chain."""

    def test_three_exam_spellings_map_to_one_value(self):
        # v2 spelled "exam" three ways (absent, {}, {mode: exam}); all map
        # to the same v3 slot value.
        for acceptance in (None, {}, {"mode": "exam"}):
            v2 = {"K": 5, "retry_limit": 1}
            if acceptance is not None:
                v2["acceptance"] = acceptance
            v3 = upgrade_v2_config(v2)
            assert v3["methods"]["acceptance"] == {
                "name": "exam", "retry_limit": 1}

    def test_live_run_mapping(self):
        v3 = upgrade_v2_config({
            "acceptance": {"mode": "live_run", "n_attempts": 3,
                           "improvement": "strict_rate_gain"},
            "retry_limit": 2,   # unused by live_run: dropped (disclosed)
        })
        assert v3["methods"]["acceptance"] == {
            "name": "live_run", "n_attempts": 3,
            "improvement": "strict_rate_gain"}
        assert "retry_limit" not in v3

    def test_every_moved_key_lands_in_its_slot(self):
        v3 = upgrade_v2_config({
            "K": 1, "segment_retry_limit": 0,
            "bank_token_budget": 96000, "curation_rounds": 4,
            "skill_delivery": {"mode": "retrieval", "top_k": 5,
                               "max_categories": 3, "min_score": 0.02},
        })
        assert v3["methods"]["segmentation"] == {"K": 1, "retry_limit": 0}
        assert v3["methods"]["curation"] == {
            "name": "agentbrew", "token_budget": 96000, "rounds": 4}
        assert v3["methods"]["delivery"] == {
            "name": "retrieval", "top_k": 5,
            "max_categories": 3, "min_score": 0.02}
        assert v3["schema_version"] == 3

    def test_whole_bank_delivery_mapping(self):
        v3 = upgrade_v2_config({"skill_delivery": {"mode": "whole_bank"}})
        assert v3["methods"]["delivery"] == {"name": "whole_bank"}

    def test_upgrade_result_loads_through_the_v3_chain(self):
        v2 = {
            "benchmark": "tb", "mode": "split",
            "models": {"executor_strong": "s", "executor_weak": "w"},
            "n_attempts_strong": 3, "n_attempts_weak": 3,
            "K": 5, "segment_retry_limit": 2, "retry_limit": 2,
            "bank_token_budget": 96000, "curation_rounds": 3,
            "acceptance": {"mode": "live_run", "n_attempts": 3,
                           "improvement": "strict_rate_gain"},
            "skill_delivery": {"mode": "retrieval", "top_k": 5,
                               "max_categories": 3, "min_score": 0.02},
            "token_budgets": {"default_max_output_tokens": 65536},
        }
        cfg = ExperimentConfig.from_dict(
            upgrade_v3_config(upgrade_v2_config(v2)))
        assert cfg.sequence == ["baseline", "learn", "live", "learn", "live", "learn", "test"]
        assert cfg.phases["live"].execution["n_attempts"] == 3
        assert cfg.phases["learn"].curate["token_budget"] == 96000

    def test_input_mapping_untouched(self):
        v2 = {"K": 5, "token_budgets": {"default_max_output_tokens": 1},
              "models": {"executor_strong": "s", "executor_weak": "w"}}
        v3 = upgrade_v2_config(v2)
        assert v3["token_budgets"] == v2["token_budgets"]
        assert v3["models"] == v2["models"]
        assert v2 == {"K": 5, "token_budgets": {"default_max_output_tokens": 1},
                      "models": {"executor_strong": "s", "executor_weak": "w"}}


class TestUpgradeCoversEveryV2Config:
    """C's P1b precondition 3: upgrade_v2_config must handle every v2
    config in the repository (they are frozen experiment records). The
    three pre-schema pilots/tb21_full carry ``model_options.max_tokens``,
    invalid under v2's OWN rules -- they refused the v2 loader too, so
    the upgrade test checks their STRUCTURE and skips the load."""

    #: Historical configs invalid under v2's own reserved-key rule.
    PRE_V2_MODELS_OPTIONS = ("pilot_alfworld.yaml", "pilot_searchqa.yaml",
                             "tb21_full.yaml")

    #: The v3 slot set (the v4 code has no slot constant to import).
    V3_SLOTS = ("segmentation", "authoring", "acceptance", "curation",
                "delivery")

    def test_every_v2_config_upgrades(self):
        from pathlib import Path
        experiments = (Path(__file__).resolve().parents[3]
                       / "configs" / "experiments")
        checked = 0
        for path in sorted(experiments.glob("*.yaml")):
            if path.stem.endswith("_v3") or path.stem.endswith("_v2_v3"):
                continue
            import yaml
            with open(path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if raw.get("schema_version", 2) >= 3:
                continue  # an already-v3 file (or one produced later)
            upgraded = upgrade_v2_config(raw)
            # Every moved key landed in its slot (structure), and nothing
            # v2-named survives at the top level.
            for key in ("K", "segment_retry_limit", "bank_token_budget",
                        "curation_rounds", "skill_delivery", "acceptance"):
                assert key not in upgraded, (path.name, key)
            assert upgraded["schema_version"] == 3
            for slot in self.V3_SLOTS:
                assert slot in upgraded["methods"], (path.name, slot)
            if path.name not in self.PRE_V2_MODELS_OPTIONS:
                # The config was valid under v2; the chained upgrade loads.
                ExperimentConfig.from_dict(upgrade_v3_config(upgraded))
            checked += 1
        assert checked >= 8, f"expected the v2 record set, got {checked}"


class TestExternalCampaignCompare:
    """The third control source: ``compare.against: external_campaign``
    -- test-role only, the ``external`` block (path / sha256 / model_key)
    required and shape-checked, and the block refused on every other
    ``against`` value."""

    SHA = "a" * 64

    def _raw_phases(self, compare):
        phases = copy.deepcopy(MIN_PHASES)
        phases["test"]["compare"] = compare
        return phases

    def _config(self, compare):
        return make_config(phases=self._raw_phases(compare))

    def test_parses_on_a_test_phase(self):
        from expedistill.core.config import ExternalControlSpec
        cfg = self._config({
            "against": "external_campaign",
            "external": {"path": "/tmp/scores.json",
                         "sha256": self.SHA, "model_key": "qwen"},
        })
        assert cfg.phases["test"].compare.external == ExternalControlSpec(
            path="/tmp/scores.json", sha256=self.SHA, model_key="qwen")
        # The freezable form round-trips (B4's resume comparison).
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg

    def test_sha256_uppercase_normalised(self):
        cfg = self._config({
            "against": "external_campaign",
            "external": {"path": "/tmp/scores.json",
                         "sha256": "AB" * 32, "model_key": "qwen"},
        })
        assert cfg.phases["test"].compare.external.sha256 == "ab" * 32

    def test_refused_on_a_non_test_phase(self):
        phases = copy.deepcopy(MIN_PHASES)
        phases["baseline"]["compare"] = {
            "against": "external_campaign",
            "external": {"path": "/tmp/scores.json",
                         "sha256": self.SHA, "model_key": "qwen"},
        }
        with pytest.raises(ConfigError, match="role 'baseline'"):
            make_config(phases=phases)

    def test_external_block_required(self):
        with pytest.raises(ConfigError, match="compare.external"):
            self._config({"against": "external_campaign"})

    @pytest.mark.parametrize("bad", [
        {"path": "/tmp/scores.json", "sha256": "a" * 63, "model_key": "q"},
        {"path": "/tmp/scores.json", "sha256": "a" * 64},
        {"sha256": "a" * 64, "model_key": "q"},
        {"path": "", "sha256": "a" * 64, "model_key": "q"},
    ])
    def test_missing_or_bad_keys_refused(self, bad):
        with pytest.raises(ConfigError):
            self._config({"against": "external_campaign", "external": bad})

    def test_unknown_external_key_refused(self):
        with pytest.raises(ConfigError, match="unknown key"):
            self._config({"against": "external_campaign", "external": {
                "path": "/tmp/scores.json", "sha256": self.SHA,
                "model_key": "qwen", "extra": 1}})

    def test_external_block_refused_without_external_against(self):
        with pytest.raises(ConfigError, match="against is 'fresh_bare'"):
            self._config({
                "against": "fresh_bare",
                "external": {"path": "/tmp/scores.json",
                             "sha256": self.SHA, "model_key": "qwen"},
            })

    def test_live_phase_refuses_the_external_control(self):
        # A live phase's control must be a baseline phase with report
        # rows; the external control is a test-role control, so the
        # per-phase role check refuses it before the live-needs-control
        # checklist item ever sees it.
        phases = self._raw_phases({
            "against": "external_campaign",
            "external": {"path": "/tmp/scores.json",
                         "sha256": self.SHA, "model_key": "qwen"},
        })
        phases["D2"] = {"kind": "execute", "role": "baseline",
                        "tasks": "train", "delivery": {"name": "none"},
                        "execution": {"name": "real",
                                      "arms": ["executor_strong",
                                               "executor_weak"]}}
        phases["live"] = {"kind": "execute", "role": "live", "tasks": "train",
                       "delivery": {"name": "group_by_task"},
                       "execution": {"name": "real", "n_attempts": 3},
                       "compare": {"against": "external_campaign",
                                   "external": {
                                       "path": "/tmp/scores.json",
                                       "sha256": self.SHA,
                                       "model_key": "qwen"}}}
        with pytest.raises(ConfigError, match="role 'live'"):
            make_config(sequence=["baseline", "D2", "live", "learn", "test"], phases=phases)

    def test_load_config_resolves_the_relative_path(self, tmp_path):
        seed = tmp_path / "seed" / "scores.json"
        seed.parent.mkdir()
        seed.write_text("{}", encoding="utf-8")
        config_file = tmp_path / "configs" / "exp.yaml"
        config_file.parent.mkdir()
        config_file.write_text(f"""
benchmark: tb
mode: full
models:
  executor_strong: model-s
  executor_weak: model-w
schema_version: 4
sequence: [baseline, learn, test]
phases:
  baseline: {{kind: execute, role: baseline, tasks: train,
       delivery: {{name: none}},
       execution: {{name: real, arms: [executor_strong, executor_weak]}}}}
  learn: {{kind: learn, evidence: {{name: segment, K: 5}},
       author: {{name: distill_diagnose}},
       curate: {{name: none, token_budget: 4096}}}}
  test: {{kind: execute, role: test, tasks: test,
       delivery: {{name: whole_bank}}, execution: {{name: real}},
       compare: {{against: external_campaign,
                  external: {{path: ../seed/scores.json,
                             sha256: {self.SHA},
                             model_key: qwen}}}}}}
""", encoding="utf-8")
        cfg = load_config(str(config_file))
        assert cfg.phases["test"].compare.external.path == str(
            (tmp_path / "seed" / "scores.json").resolve())
