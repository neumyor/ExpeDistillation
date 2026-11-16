"""Unit tests for ExperimentConfig, its YAML round trip and the load-time
phase-sequence checklist (design spec 2.3, schema v9)."""
import copy
import dataclasses
from pathlib import Path

import pytest

from expedistill.core.config import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_RULES,
    EXECUTE_PHASE_ROLES,
    FRESH_BARE,
    FREEZE_LABEL,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_LEARN,
    PHASE_ROLE_PRACTICE,
    PHASE_ROLE_TEST,
    ROLE_DEFAULTS,
    SPLIT_LIST_KEYS,
    CompareSpec,
    ConfigError,
    ExecutePhaseSpec,
    ExperimentConfig,
    ExternalControlSpec,
    LearnPhaseSpec,
    load_config,
    parse_phases,
    phase_label,
    phase_spec_to_raw,
)
from expedistill.core.ids import phase_tag
from tests.helpers.configs import (
    REFERENCE_SEQUENCE,
    minimal_method,
    reference_method,
)

_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _ROOT / "configs" / "experiments"

MIN_SEQUENCE = minimal_method()["sequence"]

MIN_PHASES = minimal_method()["phases"]

#: The sequence/phases block every loadable YAML fixture carries, in the
#: same inline spelling a config file uses.
YAML_METHOD = (
    "sequence: [baseline, learn, test]\n"
    "phases:\n"
    "  baseline: {kind: execute, role: baseline, tasks: train,"
    " delivery: {name: none},"
    " execution: {name: real, arms: [executor_strong, executor_weak]}}\n"
    "  learn: {kind: learn, review: {name: review, K: 5, retry_limit: 2},"
    " author: {name: subtask_cards}}\n"
    "  test: {kind: execute, role: test, tasks: test,"
    " delivery: {name: whole_bank}, execution: {name: real},"
    " compare: {against: fresh_bare}}\n"
)

MIN_YAML = f"""
benchmark: tb
models:
  executor_strong: model-s
  executor_weak: model-w
n_attempts_strong: 3
n_attempts_weak: 5
schema_version: {CONFIG_SCHEMA_VERSION}
{YAML_METHOD}split:
  ratio: 0.8
  seed: 7
rules:
  segment_select: strong_all_weak_fail
  trajectory_select: all_attempts
"""

#: The version gate's refusal: every schema but the current one.
_ONLY_CURRENT = f"reads schema {CONFIG_SCHEMA_VERSION} only"


def make_config(**overrides):
    base = dict(
        benchmark="tb",
        models={"executor_strong": "model-s", "executor_weak": "model-w"},
        sequence=list(MIN_SEQUENCE),
        phases=copy.deepcopy(MIN_PHASES),
    )
    base.update(overrides)
    return ExperimentConfig(**base)


def write_yaml(tmp_path, tail="", *, method=YAML_METHOD,
               version=CONFIG_SCHEMA_VERSION, name="exp.yaml"):
    """A minimal config file; ``version=None`` leaves schema_version out."""
    version_line = "" if version is None else f"schema_version: {version}\n"
    path = tmp_path / name
    path.write_text(
        "benchmark: tb\n"
        "models: {executor_strong: s, executor_weak: w}\n"
        + version_line + method + tail,
        encoding="utf-8")
    return path


class TestRoleTable:
    def test_roles_default_to_strong_except_the_weak_executor(self):
        cfg = make_config()
        for role in ROLE_DEFAULTS:
            expected = "model-w" if role == "executor_weak" else "model-s"
            assert cfg.models[role] == expected, role

    def test_user_override_wins(self):
        cfg = make_config(models={"executor_strong": "model-s",
                                  "executor_weak": "model-w",
                                  "judge": "model-w"})
        assert cfg.models["judge"] == "model-w"

    @pytest.mark.parametrize("role", ["oracle", "examiner", "answerer",
                                      "curator"])
    def test_unknown_role_rejected(self, role):
        with pytest.raises(ValueError, match=role):
            make_config(models={"executor_strong": "model-s",
                                "executor_weak": "model-w",
                                role: "model-o"})

    def test_missing_anchor_roles_rejected(self):
        with pytest.raises(ValueError, match="executor_strong"):
            make_config(models={"executor_weak": "model-w"})


class TestValidation:
    @pytest.mark.parametrize("attempts", [0, -1, True, 1.5, "2"])
    def test_attempts_must_be_positive(self, attempts):
        with pytest.raises(ValueError, match="n_attempts"):
            make_config(n_attempts_weak=attempts)

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

    def test_unknown_rule_key_rejected(self):
        with pytest.raises(ValueError, match="unknown rule keys"):
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

    @pytest.mark.parametrize("bad", [0, -1, True, "1", 1.5, None])
    def test_invalid_redispatch_limit_must_be_a_positive_int(self, bad):
        with pytest.raises(ConfigError, match="invalid_redispatch"):
            make_config(invalid_redispatch_limit=bad)


class TestModelOptions:
    def test_default_is_empty(self):
        assert make_config().model_options == {}
        assert make_config().to_dict()["model_options"] == {}

    def test_valid_options_frozen_into_to_dict(self):
        cfg = make_config(model_options={
            "model-s": {"reasoning_effort": "max", "top_p": 0.9}})
        assert cfg.model_options == {
            "model-s": {"reasoning_effort": "max", "top_p": 0.9}}
        # Part of the frozen manifest: to_dict carries it verbatim.
        assert cfg.to_dict()["model_options"] == cfg.model_options

    def test_options_keyed_by_overridden_role_model(self):
        # Any model name reachable through the role table is valid, including
        # one only a non-anchor role uses.
        cfg = make_config(models={"executor_strong": "model-s",
                                  "executor_weak": "model-w",
                                  "judge": "model-j"},
                          model_options={"model-j": {"top_p": 0.9}})
        assert cfg.to_dict()["model_options"] == {"model-j": {"top_p": 0.9}}

    @pytest.mark.parametrize("options", [
        {"no-such-model": {"reasoning_effort": "max"}},
        {"model-s": "max"},
        {"model-s": {}},
        {"model-s": {"effort": object()}},
    ], ids=["unknown-model", "non-dict-value", "empty-dict-value",
            "non-json-value"])
    def test_malformed_options_rejected(self, options):
        with pytest.raises(ValueError, match="model_options"):
            make_config(model_options=options)

    def test_yaml_round_trip_carries_model_options(self, tmp_path):
        path = write_yaml(tmp_path, "model_options:\n"
                                    "  s:\n"
                                    "    reasoning_effort: max\n")
        cfg = load_config(str(path))
        assert cfg.model_options == {"s": {"reasoning_effort": "max"}}
        assert cfg.to_dict()["model_options"] == cfg.model_options

    # ``max_tokens`` is owned by ``token_budgets`` -- a second source
    # silently won or lost against the real one depending on merge order --
    # and the rest are not provider knobs: ``model``/``messages``/``stream``
    # ARE the request, and ``temperature`` belongs to the client.
    @pytest.mark.parametrize("key,value,guidance", [
        ("max_tokens", 512, "max_tokens lives in token_budgets"),
        ("model", "other-model", "not provider knobs"),
        ("messages", [], "not provider knobs"),
        ("stream", True, "not provider knobs"),
        ("temperature", 0.7, "not provider knobs"),
    ])
    def test_reserved_keys_rejected_with_guidance(self, key, value, guidance):
        with pytest.raises(ConfigError, match="reserved key") as excinfo:
            make_config(model_options={"model-s": {key: value}})
        assert guidance in str(excinfo.value)


#: The pinned token-budget shape with every role explicit. 65536 is the
#: endpoint-verified safe ceiling for reasoning-max models (5120 burned
#: entirely on thinking, 2026-10-11); 5120 marks one role's own cap.
FULL_BUDGETS = {
    "default_max_output_tokens": 65536,
    "max_input_tokens": None,
    "roles": {
        "executor_strong": {"max_output_tokens": 65536,
                            "max_input_tokens": None},
        "executor_weak": {"max_output_tokens": 65536},
        "segmenter": {"max_output_tokens": 32768},
        "author": {"max_output_tokens": 65536},
        "rewriter": {"max_output_tokens": 5120, "max_input_tokens": 65536},
        "judge": {"max_output_tokens": 65536},
    },
}


def _budgets(**overrides):
    return {"default_max_output_tokens": 65536, **overrides}


def _role_budget(**entry):
    return _budgets(roles={"segmenter": entry})


#: (token_budgets, expected error substring): every malformed shape.
BAD_BUDGETS = [
    pytest.param({"roles": {"segmenter": {"max_output_tokens": 5120}}},
                 "default_max_output_tokens", id="default-missing"),
    pytest.param({"max_input_tokens": 100000},
                 "default_max_output_tokens", id="default-missing-input-only"),
    *[pytest.param({"default_max_output_tokens": bad},
                   "default_max_output_tokens", id=f"default-{bad!r}")
      for bad in (0, -1, True, None, "65536", 65536.5)],
    pytest.param(_budgets(max_output=512), "unknown keys",
                 id="unknown-budget-key"),
    pytest.param(_budgets(roles={"oracle": {"max_output_tokens": 1}}),
                 "oracle", id="unknown-role"),
    pytest.param(_role_budget(max_input_tokens=4096), "max_output_tokens",
                 id="role-entry-without-output"),
    *[pytest.param(_role_budget(max_output_tokens=bad), "max_output_tokens",
                   id=f"role-output-{bad!r}")
      for bad in (0, -5, True, None, "5120", 5120.5)],
    *[pytest.param(_budgets(max_input_tokens=bad), "max_input_tokens",
                   id=f"global-input-{bad!r}")
      for bad in (0, -1, True, "x", 1.5)],
    pytest.param(_role_budget(max_output_tokens=5120, max_input_tokens=0),
                 "max_input_tokens", id="role-input-zero"),
    pytest.param(_role_budget(max_output_tokens=5120, temperature=0),
                 "unknown keys", id="role-entry-unknown-key"),
    *[pytest.param(bad, "mapping", id=f"budgets-{bad!r}")
      for bad in (None, [], "big")],
    *[pytest.param(_budgets(roles=bad), "roles", id=f"roles-{bad!r}")
      for bad in (None, [], "big")],
    pytest.param(_budgets(roles={"segmenter": 5120}), "mapping",
                 id="role-entry-not-a-mapping"),
]


class TestTokenBudgets:
    def test_full_schema_all_roles_explicit_loads(self):
        cfg = make_config(token_budgets=FULL_BUDGETS)
        for role, entry in FULL_BUDGETS["roles"].items():
            assert cfg.max_output_tokens_for(role) == entry["max_output_tokens"]

    def test_roles_fall_back_to_default(self):
        cfg = make_config(token_budgets=_role_budget(max_output_tokens=5120))
        assert cfg.max_output_tokens_for("segmenter") == 5120
        for role in ROLE_DEFAULTS:
            if role != "segmenter":
                assert cfg.max_output_tokens_for(role) == 65536, role

    def test_yaml_pinned_shape_loads(self, tmp_path):
        path = write_yaml(
            tmp_path,
            "token_budgets:\n"
            "  default_max_output_tokens: 65536\n"
            "  max_input_tokens: null\n"
            "  roles:\n"
            "    executor_strong: {max_output_tokens: 65536}\n"
            "    segmenter: {max_output_tokens: 5120}\n")
        cfg = load_config(str(path))
        assert cfg.max_output_tokens_for("executor_strong") == 65536
        assert cfg.max_output_tokens_for("segmenter") == 5120
        assert cfg.token_budgets["max_input_tokens"] is None
        for role in ROLE_DEFAULTS:
            if role not in ("executor_strong", "segmenter"):
                assert cfg.max_output_tokens_for(role) == 65536, role

    @pytest.mark.parametrize("budgets", [None, {}], ids=["absent", "empty"])
    def test_unconfigured_budgets_leave_helper_fail_closed(self, budgets):
        cfg = make_config() if budgets is None else make_config(
            token_budgets=budgets)
        assert cfg.token_budgets == {}
        with pytest.raises(ConfigError, match="token_budgets not configured"):
            cfg.max_output_tokens_for("executor_strong")

    def test_unknown_helper_role_is_a_caller_bug(self):
        # A typo'd role silently falling back to the default cap would make
        # a budget bug invisible; it is a KeyError.
        cfg = make_config(token_budgets=FULL_BUDGETS)
        with pytest.raises(KeyError, match="known"):
            cfg.max_output_tokens_for("juge")

    @pytest.mark.parametrize("budgets,match", BAD_BUDGETS)
    def test_malformed_budgets_rejected(self, budgets, match):
        with pytest.raises(ConfigError, match=match):
            make_config(token_budgets=budgets)

    def test_unknown_role_error_lists_the_valid_roles(self):
        with pytest.raises(ConfigError, match="oracle") as excinfo:
            make_config(token_budgets=_budgets(
                roles={"oracle": {"max_output_tokens": 1}}))
        assert "executor_strong" in str(excinfo.value)

    @pytest.mark.parametrize("value", [None, 131072])
    def test_global_max_input_tokens_null_or_positive_int(self, value):
        cfg = make_config(token_budgets=_budgets(max_input_tokens=value))
        assert cfg.token_budgets["max_input_tokens"] == value

    def test_role_max_input_tokens_optional(self):
        cfg = make_config(token_budgets=_role_budget(
            max_output_tokens=5120, max_input_tokens=None))
        assert cfg.token_budgets["roles"]["segmenter"]["max_input_tokens"] is None

    def test_budgets_survive_to_dict_from_dict_and_replace(self):
        # Stage 0 freezes config.to_dict() into the manifest, and via
        # dataclasses.replace(config, prompts_digests=..): the copies must
        # keep exactly what the config file said.
        cfg = make_config(token_budgets=FULL_BUDGETS)
        assert cfg.to_dict()["token_budgets"] == FULL_BUDGETS
        assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg
        frozen = dataclasses.replace(cfg, prompts_digests={"p": "d"})
        assert frozen.token_budgets == FULL_BUDGETS
        assert frozen.max_output_tokens_for("rewriter") == 5120


class TestRuleDefaults:
    def test_missing_rule_keys_filled_explicit_ones_kept(self):
        cfg = make_config(rules={"trajectory_select": "custom"})
        assert cfg.rules == {
            "segment_select": "strong_success_weak_failed",
            "trajectory_select": "custom",
        }

    def test_filled_rules_are_in_to_dict(self):
        # The frozen config must carry the effective names, not the raw ones.
        assert make_config().to_dict()["rules"] == DEFAULT_RULES

    def test_default_rules_not_shared_between_configs(self):
        first = make_config()
        first.rules["segment_select"] = "mutated"
        assert make_config().rules["segment_select"] \
            == "strong_success_weak_failed"
        assert DEFAULT_RULES["segment_select"] == "strong_success_weak_failed"

    def test_rule_name_must_be_non_empty_string(self):
        with pytest.raises(ValueError, match="segment_select"):
            make_config(rules={"segment_select": ""})


class TestConstructorOwnsItsInputs:
    def test_parsed_specs_accepted_back(self):
        # dataclasses.replace on a loaded config re-parses harmlessly.
        cfg = make_config()
        assert ExperimentConfig(**cfg.to_dict()) == cfg

    @pytest.mark.parametrize("field,given,mutate,observe,expected", [
        ("model_options",
         lambda: {"model-s": {"reasoning_effort": "max"}},
         lambda g: g["model-s"].update(reasoning_effort="mutated"),
         lambda cfg: cfg.model_options,
         {"model-s": {"reasoning_effort": "max"}}),
        ("token_budgets",
         lambda: _role_budget(max_output_tokens=5120),
         lambda g: g["roles"]["segmenter"].update(max_output_tokens=1),
         lambda cfg: cfg.max_output_tokens_for("segmenter"),
         5120),
        ("phases",
         lambda: copy.deepcopy(MIN_PHASES),
         lambda g: g["learn"]["review"].update(K=99),
         lambda cfg: cfg.phases["learn"].review["K"],
         5),
    ])
    def test_config_does_not_alias_the_callers_mapping(
            self, field, given, mutate, observe, expected):
        mapping = given()
        cfg = make_config(**{field: mapping})
        mutate(mapping)
        assert observe(cfg) == expected


class TestSplit:
    """The explicit-list ``split``: ``train`` and ``test`` required, ``val``
    and ``reserved`` optional, the four lists pairwise disjoint (same
    contract as D2 in blocks/tasks.py, one layer up: core validates the
    shape, D2 validates the lists against the loaded task set)."""

    def test_two_key_split_is_valid(self):
        cfg = make_config(split={"train": ["tb:1"], "test": ["tb:2"]})
        assert cfg.split == {"train": ["tb:1"], "test": ["tb:2"]}

    def test_four_key_split_is_valid(self):
        split = {"train": ["tb:1"], "val": ["tb:4"], "test": ["tb:2"],
                 "reserved": ["tb:3"]}
        assert make_config(split=split).split == split
        assert tuple(split) == SPLIT_LIST_KEYS

    def test_split_ratio_and_seed(self):
        cfg = make_config(split={"ratio": 0.75, "seed": 3})
        assert cfg.split == {"ratio": 0.75, "seed": 3}

    @pytest.mark.parametrize("key", ["val", "reserved"])
    def test_optional_lists_may_be_null(self, key):
        make_config(split={"train": ["a"], "test": ["b"], key: None})

    @pytest.mark.parametrize("split,match", [
        ({"train": None, "test": ["a"]}, "must be a list"),
        ({"train": ["a"], "test": None}, "must be a list"),
        ({"ratio": 0.5, "train": ["tb:1"]}, "mixes ratio"),
        ({"folds": 4}, "split"),
        ({"ratio": 1.0}, "ratio"),
        ({"train": ["tb:1"], "test": ["tb:2"], "dev": ["tb:3"]}, "split"),
        ({"test": ["tb:2"]}, "split"),
        ({"train": ["tb:1"]}, "split"),
        ({"train": ["tb:1"], "test": ["tb:2"], "val": "tb:3"}, "val"),
        ({"train": ["tb:1"], "test": ["tb:2"], "reserved": "tb:3"},
         "reserved"),
    ], ids=["train-null", "test-null", "mixed-shape", "unknown-shape",
            "ratio-bound", "extra-key", "train-missing", "test-missing",
            "val-not-a-list", "reserved-not-a-list"])
    def test_malformed_splits_rejected(self, split, match):
        with pytest.raises(ValueError, match=match):
            make_config(split=split)

    @pytest.mark.parametrize("first,second", [
        ("train", "val"), ("train", "test"), ("train", "reserved"),
        ("val", "test"), ("val", "reserved"), ("test", "reserved")])
    def test_the_lists_are_pairwise_disjoint(self, first, second):
        split = {"train": ["tb:1"], "val": ["tb:2"], "test": ["tb:3"],
                 "reserved": ["tb:4"]}
        split[second] = split[second] + split[first]
        with pytest.raises(ConfigError, match=f"'{first}' and '{second}'"):
            make_config(split=split)


class TestPracticeOnVal:
    """A practice phase on ``val`` needs an explicit, non-empty val list."""

    def _config(self, split):
        method = reference_method()
        method["phases"]["practice"]["tasks"] = "val"
        return make_config(split=split, **method)

    def test_a_non_empty_val_list_is_accepted(self):
        cfg = self._config({"train": ["tb:1"], "val": ["tb:3"],
                            "test": ["tb:2"]})
        assert cfg.phases["practice"].tasks == "val"

    @pytest.mark.parametrize("split", [
        None,
        {"ratio": 0.5, "seed": 1},
        {"train": ["tb:1"], "test": ["tb:2"]},
        {"train": ["tb:1"], "val": [], "test": ["tb:2"]},
        {"train": ["tb:1"], "val": None, "test": ["tb:2"]},
    ])
    def test_no_val_tasks_is_refused(self, split):
        with pytest.raises(ConfigError, match="non-empty explicit 'val'"):
            self._config(split)

    def test_practice_on_train_needs_no_val(self):
        cfg = make_config(split={"ratio": 0.5}, **reference_method())
        assert cfg.phases["practice"].tasks == "train"


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
        # Every field freezes, always -- a None must not be filtered out,
        # or a resumed run would compare against a different key set.
        payload = make_config().to_dict()
        assert set(payload) == {
            f.name for f in dataclasses.fields(ExperimentConfig)}
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

    @pytest.mark.parametrize("version,match", [
        (CONFIG_SCHEMA_VERSION + 1, _ONLY_CURRENT),
        *[(older, _ONLY_CURRENT) for older in range(2, CONFIG_SCHEMA_VERSION)],
        (str(CONFIG_SCHEMA_VERSION), "schema_version"),
    ])
    def test_from_dict_refuses_every_other_schema_version(self, version, match):
        payload = make_config().to_dict()
        payload["schema_version"] = version
        with pytest.raises(ConfigError, match=match) as excinfo:
            ExperimentConfig.from_dict(payload)
        if isinstance(version, int):
            assert "build_paper_configs" in str(excinfo.value)

    @pytest.mark.parametrize("version,method", [
        (CONFIG_SCHEMA_VERSION + 1, ""),
        (3, YAML_METHOD),
        (CONFIG_SCHEMA_VERSION - 1, YAML_METHOD),
        (None, YAML_METHOD),
    ], ids=["future", "old", "previous", "unversioned"])
    def test_load_config_refuses_every_other_schema_version(
            self, tmp_path, version, method):
        # The gate runs before any other parsing, so the future file need
        # carry no method at all.
        path = write_yaml(tmp_path, method=method, version=version)
        with pytest.raises(ConfigError, match=_ONLY_CURRENT) as excinfo:
            load_config(str(path))
        assert "build_paper_configs" in str(excinfo.value)

    def test_unknown_key_error_lists_unknown_and_known_keys(self, tmp_path):
        # The exact trap the loud loader closes: "token_budget" (missing
        # the s) silently vanished under v1 leniency and the run continued
        # uncapped at the dead 5120 default. The error must name the
        # unknown key and list what a config file accepts.
        path = write_yaml(
            tmp_path, "token_budget: {default_max_output_tokens: 65536}\n")
        with pytest.raises(ConfigError) as excinfo:
            load_config(str(path))
        message = str(excinfo.value)
        assert "token_budget" in message
        assert "token_budgets" in message

    def test_yaml_round_trip(self, tmp_path):
        path = tmp_path / "exp.yaml"
        path.write_text(MIN_YAML, encoding="utf-8")
        cfg = load_config(str(path))
        assert cfg.benchmark == "tb"
        assert cfg.split == {"ratio": 0.8, "seed": 7}
        assert cfg.sequence == ["baseline", "learn", "test"]
        assert cfg.phases["learn"].review["K"] == 5
        assert cfg.phases["test"].delivery == {"name": "whole_bank"}
        assert cfg.rules["segment_select"] == "strong_all_weak_fail"
        assert cfg.models["judge"] == "model-s"
        assert set(cfg.models) == set(ROLE_DEFAULTS)

        reloaded = ExperimentConfig.from_dict(cfg.to_dict())
        assert reloaded == cfg
        # B4's resume comparison is on the raw dict, not on the objects.
        assert reloaded.to_dict() == cfg.to_dict()

    def test_relative_tasks_path_resolved_against_config_dir(
            self, tmp_path, monkeypatch):
        conf_dir = tmp_path / "configs" / "experiments"
        (conf_dir / "tasks").mkdir(parents=True)
        task_file = conf_dir / "tasks" / "tb_subset.txt"
        task_file.write_text("task-a\n", encoding="utf-8")
        write_yaml(conf_dir, "tasks: tasks/tb_subset.txt\n")
        # Load through a relative config path from an unrelated cwd: the
        # result must still point at the file next to the config, and be
        # absolute so a resume from another cwd freezes the same value.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        cfg = load_config("../configs/experiments/exp.yaml")
        assert cfg.tasks == str(task_file.resolve())

    def test_absolute_tasks_path_and_id_list_untouched(self, tmp_path):
        path = write_yaml(tmp_path, "tasks: /data/tb/tasks.txt\n")
        assert load_config(str(path)).tasks == "/data/tb/tasks.txt"
        write_yaml(tmp_path, "tasks: [task-a, task-b]\n")
        assert load_config(str(path)).tasks == ["task-a", "task-b"]

    def test_relative_paths_resolve_against_real_config_through_symlink(
            self, tmp_path):
        real_dir = tmp_path / "real"
        (real_dir / "tasks").mkdir(parents=True)
        (real_dir / "tasks" / "t.txt").write_text("a\n", encoding="utf-8")
        real_cfg = write_yaml(real_dir, "tasks: tasks/t.txt\n")
        # A decoy next to the symlink: it must NOT be picked up.
        link_dir = tmp_path / "links"
        (link_dir / "tasks").mkdir(parents=True)
        (link_dir / "tasks" / "t.txt").write_text("b\n", encoding="utf-8")
        link = link_dir / "exp.yaml"
        link.symlink_to(real_cfg)
        cfg = load_config(str(link))
        assert cfg.tasks == str((real_dir / "tasks" / "t.txt").resolve())

    def test_non_mapping_yaml_rejected(self, tmp_path):
        cfg_file = tmp_path / "exp.yaml"
        cfg_file.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            load_config(str(cfg_file))


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

    def test_load_config_resolves_the_relative_path(self, tmp_path):
        seed = tmp_path / "seed" / "scores.json"
        seed.parent.mkdir()
        seed.write_text("{}", encoding="utf-8")
        compare = ("compare: {against: external_campaign, external: "
                   f"{{path: ../seed/scores.json, sha256: {self.SHA}, "
                   "model_key: qwen}}")
        method = YAML_METHOD.replace("compare: {against: fresh_bare}", compare)
        (tmp_path / "configs").mkdir()
        path = write_yaml(tmp_path / "configs", method=method)
        cfg = load_config(str(path))
        assert cfg.phases["test"].compare.external.path == str(
            (tmp_path / "seed" / "scores.json").resolve())


# ---------------------------------------------------------------------------
# parse_phases: labels, parsed shapes and the load-time checklist
# ---------------------------------------------------------------------------

@pytest.fixture()
def method():
    return reference_method()


def _parse(method):
    return parse_phases(method["phases"], method["sequence"])


_DEL = object()


def _edit(method, edits):
    """Apply ``(path, value)`` edits to the method; ``_DEL`` deletes."""
    for path, value in edits:
        node = method
        for key in path[:-1]:
            node = node[key]
        if value is _DEL:
            del node[path[-1]]
        else:
            node[path[-1]] = copy.deepcopy(value)


class TestPhaseLabels:
    def test_the_label_shape_is_what_the_id_rules_accept(self):
        label = phase_label(0, "baseline")
        assert label == "p01:baseline"
        assert phase_tag(label) == "p01_baseline"
        assert phase_tag(FREEZE_LABEL) == "p00_freeze"
        assert phase_label(11, "a_late.phase") == "p12:a_late.phase"


class TestParsedShapes:
    def test_execute_phase_fields(self, method):
        specs = _parse(method)
        baseline = specs["baseline"]
        assert isinstance(baseline, ExecutePhaseSpec)
        assert baseline.kind == "execute"
        assert baseline.role == PHASE_ROLE_BASELINE
        assert baseline.tasks == "train"
        assert baseline.delivery == {"name": "none"}
        assert baseline.execution == {
            "name": "real", "arms": ["executor_strong", "executor_weak"]}
        assert baseline.compare is None and baseline.acceptance is None
        assert baseline.playbook == "off"
        practice = specs["practice"]
        assert practice.role == PHASE_ROLE_PRACTICE
        assert practice.delivery == {"name": "folder"}
        assert practice.execution == {"name": "real_folder", "attempts": 3}
        assert practice.playbook == "evolved"
        assert practice.compare is None
        test = specs["test"]
        assert test.role == PHASE_ROLE_TEST
        assert test.tasks == "test"
        assert test.compare == CompareSpec(against="fresh_bare")

    def test_learn_phase_fields(self, method):
        learn = _parse(method)["learn"]
        assert isinstance(learn, LearnPhaseSpec)
        assert learn.kind == "learn"
        assert learn.role == PHASE_ROLE_LEARN
        assert learn.review == {"name": "review", "K": 5, "retry_limit": 2}
        assert learn.author == {"name": "subtask_cards"}
        assert set(phase_spec_to_raw(learn)) == {
            "review", "author", "kind", "role"}

    def test_round_trip_through_the_freezable_form(self, method):
        # The raw form of a parsed spec re-parses to the same spec (the
        # manifest comparison needs it).
        specs = _parse(method)
        raw = {name: phase_spec_to_raw(spec)
               for name, spec in specs.items()}
        assert parse_phases(raw, method["sequence"]) == specs


#: Every load-time refusal of design spec 2.3 as (edits, expected message):
#: one negative case per checklist item, ids name the item.
REFUSED_METHODS = [
    # 1. sequence
    pytest.param([(("sequence",), [])], "sequence must be a",
                 id="1-empty-sequence"),
    # The hard prerequisite of the pNN:<name> label: no colons.
    pytest.param([(("sequence",), ["baseline:", "learn"])], "never a colon",
                 id="1-colon-in-a-name"),
    pytest.param([(("sequence",), ["baseline", "X"])], "no definition",
                 id="1-name-without-definition"),
    # 2. field sets
    pytest.param([(("phases", "baseline", "kind"), "study")],
                 "'execute' or 'learn'", id="2-unknown-kind"),
    pytest.param([(("phases", "learn", "delivery"), {"name": "none"})],
                 "kind 'learn' but carries", id="2-execute-key-on-learn"),
    pytest.param([(("phases", "baseline", "review"), {"name": "review"})],
                 "kind 'execute' but carries", id="2-learn-key-on-execute"),
    pytest.param([(("phases", "baseline", "delivery"), {"arms": 1})],
                 r"phases.baseline.delivery needs a non-empty",
                 id="2-nested-mapping-needs-a-name"),
    pytest.param([(("phases", "learn", "author"), "subtask_cards")],
                 "phases.learn.author must be a mapping",
                 id="2-nested-method-is-not-a-mapping"),
    # 3. role consistency
    pytest.param([(("phases", "baseline", "role"), "exam")],
                 "declared, never inferred", id="3-unknown-role"),
    pytest.param([(("phases", "test", "tasks"), "train")],
                 "role 'test' with tasks 'train'",
                 id="3-test-role-runs-the-test-list"),
    pytest.param([(("phases", "baseline", "tasks"), "test")],
                 "role 'baseline' with tasks 'test'",
                 id="3-baseline-never-runs-the-test-list"),
    pytest.param([(("phases", "practice", "tasks"), "test")],
                 "role 'practice' with tasks 'test'",
                 id="3-practice-never-runs-the-test-list"),
    pytest.param([(("phases", "baseline", "tasks"), "val")],
                 "role 'baseline' with tasks 'val'",
                 id="3-baseline-runs-train-only"),
    pytest.param([(("phases", "practice", "tasks"), "reserved")],
                 "tasks must be one of", id="3-unknown-task-set"),
    pytest.param([(("phases", "baseline", "delivery"), {"name": "whole_bank"})],
                 "the baseline is the bare run", id="3-baseline-delivery"),
    pytest.param([(("phases", "baseline", "delivery"), {"name": "folder"}),
                  (("phases", "baseline", "execution"),
                   {"name": "real_folder",
                    "arms": ["executor_strong", "executor_weak"]})],
                 "the baseline is the bare run", id="3-baseline-execution"),
    pytest.param([(("phases", "baseline", "playbook"), "evolved")],
                 "baseline phase runs without", id="3-baseline-no-playbook"),
    pytest.param([(("phases", "baseline", "execution", "arms"),
                   ["executor_weak"])],
                 "must be exactly", id="3-baseline-arms-fixed"),
    pytest.param([(("phases", "baseline", "execution", "arms"), _DEL)],
                 "must be exactly", id="3-baseline-arms-required"),
    pytest.param([(("phases", "practice", "execution", "arms"),
                   ["executor_weak"])],
                 "only a baseline", id="3-practice-is-single-armed"),
    pytest.param([(("phases", "test", "execution", "arms"),
                   ["executor_weak"])],
                 "only a baseline", id="3-test-is-single-armed"),
    pytest.param([(("phases", "practice", "delivery"), {"name": "whole_bank"}),
                  (("phases", "practice", "execution"), {"name": "real"})],
                 "a practice phase runs the way the final test",
                 id="3-practice-uses-the-folder-delivery"),
    pytest.param([(("phases", "test", "execution"), {"name": "exam"})],
                 "execution name 'exam' is not one of",
                 id="3-unknown-execution-name"),
    pytest.param([(("phases", "practice", "acceptance"), "")],
                 "verdict rule", id="3-empty-acceptance-rule"),
    # 4. baseline first
    pytest.param([(("sequence",), ["practice", "baseline", "learn", "test"])],
                 "must start with the 'baseline'", id="4-practice-first"),
    pytest.param([(("sequence",), ["learn", *REFERENCE_SEQUENCE])],
                 "must start with the 'baseline'", id="4-learn-first"),
    pytest.param([(("sequence",), ["test", "baseline"]),
                  (("phases", "practice"), _DEL), (("phases", "learn"), _DEL),
                  (("phases", "test", "playbook"), _DEL)],
                 "only the first entry may be the baseline",
                 id="4-baseline-after-a-test"),
    pytest.param([(("sequence",), ["baseline", "learn", "practice",
                                   "baseline", "learn", "test"])],
                 "only the first entry may be the baseline",
                 id="4-baseline-twice"),
    # 5. compare
    pytest.param([(("phases", "baseline", "compare"), {"against": FRESH_BARE})],
                 "only a 'test' phase", id="5-baseline-has-no-control"),
    pytest.param([(("phases", "practice", "compare"), {"against": FRESH_BARE})],
                 "only a 'test' phase", id="5-practice-has-no-control"),
    *[pytest.param([(("phases", "test", "compare"), {"against": against})],
                   "against must be one of",
                   id=f"5-compare-against-{against}")
      for against in ("baseline", "learn", "test", None)],
    pytest.param([(("phases", "test", "compare"),
                   {"against": FRESH_BARE, "rule": "x"})],
                 "unknown key", id="5-compare-unknown-key"),
    # 6. learn reads learnable phases
    pytest.param([(("sequence",), [*REFERENCE_SEQUENCE, "learn"])],
                 "role 'test'", id="6-learn-after-the-test"),
    # 7. folder pairing
    pytest.param([(("phases", "test", "execution"), {"name": "real"})],
                 "pairs with the 'real_folder' execution",
                 id="7-folder-delivery-needs-real-folder"),
    pytest.param([(("phases", "test", "delivery"), {"name": "whole_bank"})],
                 "pairs with the 'real_folder' execution",
                 id="7-real-folder-needs-folder-delivery"),
    # 8. playbook switch
    pytest.param([(("phases", "test", "playbook"), "learned")],
                 "playbook must be one of", id="8-unknown-switch-value"),
    pytest.param([(("sequence",), ["baseline", "test"]),
                  (("phases", "learn"), _DEL), (("phases", "practice"), _DEL)],
                 "no learn phase runs before",
                 id="8-evolved-playbook-needs-a-learn-phase"),
]


class TestParsePhasesChecklist:
    @pytest.mark.parametrize("edits,match", REFUSED_METHODS)
    def test_a_violation_is_a_loud_config_error(self, method, edits, match):
        _edit(method, edits)
        with pytest.raises(ConfigError, match=match):
            _parse(method)

    def test_an_unused_definition_is_refused(self, method):
        method["phases"]["X"] = copy.deepcopy(method["phases"]["practice"])
        with pytest.raises(ConfigError, match="never runs"):
            _parse(method)

    def test_acceptance_names_a_rule_or_stays_absent(self, method):
        method["phases"]["practice"]["acceptance"] = "scorer"
        assert _parse(method)["practice"].acceptance == "scorer"

    def test_a_test_only_sequence_needs_no_baseline(self, method):
        method["sequence"] = ["test"]
        method["phases"] = {"test": method["phases"]["test"]}
        del method["phases"]["test"]["playbook"]
        assert set(_parse(method)) == {"test"}

    def test_learn_right_after_the_baseline_is_legal(self, method):
        del method["phases"]["practice"]
        method["sequence"] = ["baseline", "learn", "test"]
        assert set(_parse(method)) == {"baseline", "learn", "test"}

    def test_a_test_phase_may_run_whole_bank_with_real(self, method):
        method["phases"]["test"]["delivery"] = {"name": "whole_bank"}
        method["phases"]["test"]["execution"] = {"name": "real"}
        assert _parse(method)["test"].execution == {"name": "real"}

    def test_the_playbook_switch_defaults_to_off(self, method):
        for name in ("practice", "test"):
            del method["phases"][name]["playbook"]
        specs = _parse(method)
        assert {specs[name].playbook
                for name in ("baseline", "practice", "test")} == {"off"}

    def test_practice_and_test_may_show_the_evolved_playbook(self, method):
        specs = _parse(method)
        assert specs["practice"].playbook == specs["test"].playbook \
            == "evolved"
        assert phase_spec_to_raw(specs["test"])["playbook"] == "evolved"


class TestCheckedInConfigs:
    def test_every_config_loads_and_round_trips(self):
        paths = sorted(_CONFIG_DIR.glob("*.yaml"))
        assert paths, f"no experiment configs under {_CONFIG_DIR}"
        for path in paths:
            cfg = load_config(str(path))
            assert cfg.schema_version == CONFIG_SCHEMA_VERSION, path.name
            assert set(cfg.phases) == set(cfg.sequence), path.name
            for spec in cfg.phases.values():
                assert spec.role in EXECUTE_PHASE_ROLES + (
                    PHASE_ROLE_LEARN,)
            # The canonical (all-keys) to_dict round-trips: the frozen
            # manifest compares equal on resume.
            assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg, \
                path.name
