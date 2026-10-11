"""Unit tests for ExperimentConfig and its YAML round trip."""
import pytest

from expedistill.core.config import (
    DEFAULT_RULES,
    MODEL_ROLES,
    RULE_KEYS,
    ExperimentConfig,
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

    def test_old_config_with_leak_threshold_still_loads(self, tmp_path):
        # from_dict drops unknown keys, so a config written before the judge
        # audit (with leak_threshold) keeps loading; the stray key is ignored.
        cfg_file = tmp_path / "old.yaml"
        cfg_file.write_text(
            "benchmark: tb\nmode: full\n"
            "models: {executor_strong: s, executor_weak: w}\n"
            "leak_threshold: 0.3\n",
            encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.models["auditor"] == "s"

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


class TestRuleDefaults:
    def test_default_rules_cover_every_rule_key(self):
        assert set(DEFAULT_RULES) == set(RULE_KEYS)
        assert DEFAULT_RULES == {
            "task_select": "all_valid_failed",
            "segment_select": "strong_all_weak_failed",
            "trajectory_select": "all",
        }

    def test_missing_rule_keys_filled_explicit_ones_kept(self):
        cfg = make_config(rules={"trajectory_select": "custom"})
        assert cfg.rules == {
            "task_select": "all_valid_failed",
            "segment_select": "strong_all_weak_failed",
            "trajectory_select": "custom",
        }

    def test_filled_rules_are_in_to_dict(self):
        # The frozen config must carry the effective names, not the raw ones.
        assert make_config().to_dict()["rules"] == DEFAULT_RULES

    def test_default_rules_not_shared_between_configs(self):
        first = make_config()
        first.rules["task_select"] = "mutated"
        assert make_config().rules["task_select"] == "all_valid_failed"
        assert DEFAULT_RULES["task_select"] == "all_valid_failed"

    def test_rule_name_must_be_non_empty_string(self):
        with pytest.raises(ValueError, match="task_select"):
            make_config(rules={"task_select": ""})
        with pytest.raises(ValueError, match="segment_select"):
            make_config(rules={"segment_select": 3})


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
        from dataclasses import fields
        payload = make_config().to_dict()
        assert set(payload) == {f.name for f in fields(ExperimentConfig)}

    def test_from_dict_ignores_unknown_keys(self):
        cfg = make_config()
        payload = {**cfg.to_dict(), "future_field": 1}
        assert ExperimentConfig.from_dict(payload) == cfg

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
