"""Stage 0 unit tests: freeze write, resume stability, mismatch refusal.

Fake plugin registered through the real registry/import machinery (the stub
finder pattern of tests/unit/blocks/test_execute.py); no real plugins, no
network, no model calls (stage 0 executes nothing)."""

import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types

import pytest

from expedistill import prompts
from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import (
    ConfigError,
    _reset_registry,
    register_plugin,
)
from expedistill.blocks.rules import TASK_SELECT
from expedistill.core.config import DEFAULT_RULES, ExperimentConfig
from expedistill.infra.manifest import FrozenProtocolChanged, load as load_manifest
from expedistill.infra.store import Store, read_json
from expedistill.infra.usage import UsageRecorder
from expedistill.prompts import PromptSpec
from expedistill.stages import stage0_freeze
from expedistill.stages.context import StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")


class StagePlugin(BenchmarkPlugin):
    """Fake benchmark: serves the fixed task set; run() must never be called."""

    def __init__(self, instructions=None, metas=None):
        self.load_specs = []
        self.instructions = dict(instructions or {})
        self.metas = dict(metas or {})

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        self.load_specs.append(tasks_spec)
        if tasks_spec is None:
            ids = list(TASK_IDS)
        else:
            unknown = [i for i in tasks_spec if i not in TASK_IDS]
            if unknown:
                raise ConfigError(f"unknown task ids: {unknown}")
            ids = list(tasks_spec)
        return [Task(task_id=i,
                     instruction=self.instructions.get(i, f"do {i}"),
                     meta=dict(self.metas.get(i, {})))
                for i in ids]

    def run(self, task, handle, skills_block, workdir):
        raise AssertionError("stage 0 must not execute tasks")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


class TBLikePlugin(StagePlugin):
    """Like the TB plugin: ``None`` is rejected (the task set is always an
    explicit experiment input); a str is a task file, one id per line."""

    def load_tasks(self, tasks_spec):
        if tasks_spec is None:
            self.load_specs.append(tasks_spec)
            raise ConfigError("needs an explicit task list")
        if isinstance(tasks_spec, str):
            self.load_specs.append(tasks_spec)
            with open(tasks_spec, encoding="utf-8") as fh:
                ids = [line.strip() for line in fh if line.strip()]
            return [Task(task_id=i, instruction=f"do {i}") for i in ids]
        return super().load_tasks(tasks_spec)


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery
    (same contract as tests/unit/blocks/test_execute.py)."""
    fullname = f"expedistill.benchmarks.{plugin.name}"

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(plugin)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    sys.modules.pop(fullname, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    return plugin


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry()
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()


def make_config(**overrides):
    defaults = dict(
        benchmark=_FAKE,
        mode="split",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1,
        n_attempts_weak=2,
        K=4,
        split={"train": ["t1", "t2"], "test": ["t3"]},
        rules={"task_select": "all_valid_failed", "trajectory_select": "all"},
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_ctx(tmp_path, config, run_name="run"):
    run_dir = tmp_path / run_name
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={},
        workdir_root=run_dir / "workdirs",
    )


# ---------------------------------------------------------------------------
# Freeze write
# ---------------------------------------------------------------------------

def test_freeze_writes_manifest_and_summary(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    config = make_config()
    ctx = make_ctx(tmp_path, config)
    result = stage0_freeze.run(ctx)

    assert result["tasks"] == 3
    assert result["train"] == ["t1", "t2"]
    assert result["test"] == ["t3"]
    # The returned dict is id lists only (the pipeline JSON-dumps it into
    # its ledger), so its exact key set is part of the contract.
    assert set(result) == {"tasks", "train", "test"}
    json.dumps(result)

    manifest = load_manifest(ctx.run_dir)
    assert manifest["benchmark"] == _FAKE
    assert manifest["mode"] == "split"
    assert manifest["split"] == {"train": ["t1", "t2"], "test": ["t3"]}
    assert manifest["model_roles"]["executor_strong"] == "big-model"
    assert manifest["rules"] == config.rules
    # The fake plugin declares no auxiliary metric: frozen as null.
    assert manifest["aux_metric"] is None
    assert manifest["prompt_digests"]["SEGMENT"]
    # The frozen config copy carries the digests (core.config contract).
    assert manifest["config"]["prompts_digests"] == manifest["prompt_digests"]
    assert manifest["config"]["K"] == config.K
    # Explicit-list configs load exactly the union of the two lists.
    assert plugin.load_specs == [["t1", "t2", "t3"]]

    summary = read_json(ctx.run_dir / "stage0_summary.json")
    assert summary == {"tasks": 3, "train": ["t1", "t2"], "test": ["t3"]}


def test_full_mode_freezes_whole_set_as_train_and_test(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    config = make_config(mode="full", split=None)
    ctx = make_ctx(tmp_path, config)
    result = stage0_freeze.run(ctx)
    assert result["tasks"] == 3
    assert result["train"] == list(TASK_IDS)
    assert result["test"] == list(TASK_IDS)
    manifest = load_manifest(ctx.run_dir)
    assert manifest["split"] == {
        "train": list(TASK_IDS), "test": list(TASK_IDS)}


def test_ratio_split_derived_deterministically(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    config = make_config(split={"ratio": 0.34, "seed": 7})
    first = stage0_freeze.run(make_ctx(tmp_path, config, run_name="a"))
    second = stage0_freeze.run(make_ctx(tmp_path, config, run_name="b"))

    # The ratio config needs the whole task set before deriving.
    assert plugin.load_specs == [None, None]
    for result in (first, second):
        assert sorted(result["train"] + result["test"]) == sorted(TASK_IDS)
        assert not set(result["train"]) & set(result["test"])
        assert result["tasks"] == 3
    assert (first["train"], first["test"]) == (second["train"], second["test"])
    # The derived lists are what got frozen, not the ratio.
    manifest = load_manifest(tmp_path / "a")
    assert manifest["split"]["train"] == first["train"]
    assert set(manifest["split"]["train"]) | set(manifest["split"]["test"]) \
        == set(TASK_IDS)


# ---------------------------------------------------------------------------
# Resume and mismatch
# ---------------------------------------------------------------------------

def test_resume_rerun_is_stable_and_manifest_unchanged(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    config = make_config()
    ctx = make_ctx(tmp_path, config)
    first = stage0_freeze.run(ctx)
    manifest_bytes = (ctx.run_dir / "manifest.json").read_bytes()

    second = stage0_freeze.run(make_ctx(tmp_path, config))

    assert second == first
    assert (ctx.run_dir / "manifest.json").read_bytes() == manifest_bytes


def test_changed_config_on_frozen_run_raises(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)

    changed = make_ctx(tmp_path, make_config(K=9))
    with pytest.raises(FrozenProtocolChanged, match="config.K"):
        stage0_freeze.run(changed)


def test_changed_task_instruction_on_resume_raises(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    config = make_config()
    stage0_freeze.run(make_ctx(tmp_path, config))

    # Same ids, same config, but one task's text changed between resumes.
    plugin.instructions["t2"] = "do t2!"
    with pytest.raises(FrozenProtocolChanged, match="tasks_digest"):
        stage0_freeze.run(make_ctx(tmp_path, config))


def test_changed_task_meta_on_resume_raises(monkeypatch, tmp_path):
    plugin = _install_plugin(
        monkeypatch, StagePlugin(metas={"t1": {"gold": "a"}}))
    config = make_config()
    stage0_freeze.run(make_ctx(tmp_path, config))

    plugin.metas["t1"] = {"gold": "b"}
    with pytest.raises(FrozenProtocolChanged, match="tasks_digest"):
        stage0_freeze.run(make_ctx(tmp_path, config))


def test_tasks_digest_ignores_load_order_and_handles_non_json_meta(tmp_path):
    from pathlib import Path

    def tasks(order, meta_path):
        return [Task(task_id=i, instruction=f"do {i}",
                     meta={"file": Path(meta_path), "tags": {"b", "a"},
                           "raw": b"\x00"})
                for i in order]

    forward = stage0_freeze.tasks_digest(tasks(["t1", "t2"], "/x/g.tw"))
    backward = stage0_freeze.tasks_digest(tasks(["t2", "t1"], "/x/g.tw"))
    assert forward == backward
    assert len(forward) == 64
    assert stage0_freeze.tasks_digest(tasks(["t1", "t2"], "/x/h.tw")) \
        != forward


def test_object_repr_fallback_is_deterministic():
    class Opaque:
        pass

    def digest():
        return stage0_freeze.tasks_digest(
            [Task(task_id="t1", instruction="x", meta={"o": Opaque()})])

    # Default reprs embed a memory address; two fresh objects must still
    # hash the same, or every resume would refuse spuriously.
    assert digest() == digest()


def test_manifest_freezes_tasks_digest(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)
    manifest = load_manifest(ctx.run_dir)
    expected = stage0_freeze.tasks_digest(
        [Task(task_id=i, instruction=f"do {i}") for i in TASK_IDS])
    assert manifest["tasks_digest"] == expected


# ---------------------------------------------------------------------------
# Task set from config.tasks
# ---------------------------------------------------------------------------

def test_tb_like_plugin_full_mode_with_task_list(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, TBLikePlugin())
    config = make_config(mode="full", split=None, tasks=["t1", "t3"])
    ctx = make_ctx(tmp_path, config)
    result = stage0_freeze.run(ctx)
    assert plugin.load_specs == [["t1", "t3"]]
    assert result == {"tasks": 2, "train": ["t1", "t3"],
                      "test": ["t1", "t3"]}
    manifest = load_manifest(ctx.run_dir)
    assert manifest["config"]["tasks"] == ["t1", "t3"]


def test_tb_like_plugin_full_mode_with_task_file(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, TBLikePlugin())
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("t2\nt3\n", encoding="utf-8")
    config = make_config(mode="full", split=None, tasks=str(task_file))
    result = stage0_freeze.run(make_ctx(tmp_path, config))
    assert plugin.load_specs == [str(task_file)]
    assert result["train"] == ["t2", "t3"]


def test_tb_like_plugin_ratio_split_derives_from_task_set(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, TBLikePlugin())
    config = make_config(split={"ratio": 0.5, "seed": 1},
                         tasks=["t1", "t2"])
    result = stage0_freeze.run(make_ctx(tmp_path, config))
    assert plugin.load_specs == [["t1", "t2"]]
    assert sorted(result["train"] + result["test"]) == ["t1", "t2"]
    assert len(result["train"]) == 1


def test_tb_like_plugin_without_tasks_still_rejected(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, TBLikePlugin())
    config = make_config(mode="full", split=None)
    with pytest.raises(ConfigError, match="explicit task list"):
        stage0_freeze.run(make_ctx(tmp_path, config))


def test_explicit_split_must_cover_task_set_exactly(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    # Lists name t3, the task set does not (and the task set's t2 is
    # unlisted): both directions are named in the error.
    config = make_config(split={"train": ["t1"], "test": ["t3"]},
                         tasks=["t1", "t2"])
    ctx = make_ctx(tmp_path, config)
    with pytest.raises(ConfigError) as info:
        stage0_freeze.run(ctx)
    message = str(info.value)
    assert "t3" in message and "t2" in message
    assert "tasks" in message
    assert not (ctx.run_dir / "manifest.json").exists()
    assert plugin.load_specs == [["t1", "t2"]]


def test_explicit_split_matching_task_set_loads_task_set(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    config = make_config(split={"train": ["t2"], "test": ["t1"]},
                         tasks=["t1", "t2"])
    result = stage0_freeze.run(make_ctx(tmp_path, config))
    assert plugin.load_specs == [["t1", "t2"]]
    assert result == {"tasks": 2, "train": ["t2"], "test": ["t1"]}


# ---------------------------------------------------------------------------
# Named rules: defaults frozen, names validated before anything runs
# ---------------------------------------------------------------------------

def test_rule_defaults_are_frozen(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config(rules={}))
    stage0_freeze.run(ctx)
    manifest = load_manifest(ctx.run_dir)
    assert manifest["rules"] == DEFAULT_RULES
    assert manifest["config"]["rules"] == DEFAULT_RULES


@pytest.mark.parametrize("key", ["task_select", "segment_select",
                                 "trajectory_select"])
def test_unknown_rule_name_fails_at_stage0_before_anything(
        monkeypatch, tmp_path, key):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config(rules={key: "no_such_rule"}))
    with pytest.raises(ConfigError, match="no_such_rule"):
        stage0_freeze.run(ctx)
    # Nothing loaded, nothing frozen, nothing written.
    assert plugin.load_specs == []
    assert not (ctx.run_dir / "manifest.json").exists()
    assert not (ctx.run_dir / "stage0_summary.json").exists()


def test_registered_custom_rule_name_accepted(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    name = "stage0_test_only_rule"
    if name not in TASK_SELECT:
        TASK_SELECT.register(name)(lambda metrics, n_attempts: True)
    ctx = make_ctx(tmp_path, make_config(rules={"task_select": name}))
    stage0_freeze.run(ctx)
    assert load_manifest(ctx.run_dir)["rules"]["task_select"] == name


# ---------------------------------------------------------------------------
# Prompt digests: method prompts + exactly the configured plugin's prompt
# ---------------------------------------------------------------------------

def test_prompt_digests_are_method_prompts_plus_task_prompt(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)
    manifest = load_manifest(ctx.run_dir)
    frozen = manifest["prompt_digests"]
    assert set(frozen) == set(prompts.METHOD_PROMPT_NAMES) | {"FAKE_TASK"}
    # The fake's task prompt is not registered in B2: frozen as null.
    assert frozen["FAKE_TASK"] is None
    assert frozen["SEGMENT"] == prompts.digest("SEGMENT")
    assert manifest["config"]["prompts_digests"] == frozen


def test_registered_task_prompt_digest_is_frozen(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    monkeypatch.setitem(prompts.PROMPTS, "FAKE_TASK", PromptSpec(
        name="FAKE_TASK", template="do {instruction}", description="test"))
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)
    assert load_manifest(ctx.run_dir)["prompt_digests"]["FAKE_TASK"] \
        == prompts.digest("FAKE_TASK")


def test_unrelated_prompt_registration_does_not_change_digests(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    config = make_config()
    ctx = make_ctx(tmp_path, config)
    stage0_freeze.run(ctx)
    manifest_bytes = (ctx.run_dir / "manifest.json").read_bytes()

    # Another plugin's task prompt shows up in B2 between the two starts
    # (what importing an unrelated plugin package does); the resume must
    # neither see it nor refuse.
    monkeypatch.setitem(prompts.PROMPTS, "OTHER_BENCH_TASK", PromptSpec(
        name="OTHER_BENCH_TASK", template="x", description="test"))
    importlib.import_module("expedistill.benchmarks.searchqa")
    importlib.import_module("expedistill.benchmarks.tb")
    stage0_freeze.run(make_ctx(tmp_path, config))

    assert (ctx.run_dir / "manifest.json").read_bytes() == manifest_bytes
    frozen = load_manifest(ctx.run_dir)["prompt_digests"]
    assert "OTHER_BENCH_TASK" not in frozen
    assert "SEARCHQA_TASK_PROMPT" not in frozen
    assert "TB_TASK_PROMPT" not in frozen


def test_real_searchqa_plugin_freezes_its_task_prompt_digest(
        monkeypatch, tmp_path):
    importlib.import_module("expedistill.benchmarks.searchqa")
    from expedistill.benchmarks.searchqa import SearchQAPlugin

    # Real plugin class (its prompt registered at import); only task
    # loading is stubbed, since the eval repo is not present in unit tests.
    plugin = SearchQAPlugin()
    monkeypatch.setattr(plugin, "load_tasks", lambda spec: [
        Task(task_id=i, instruction=f"q {i}") for i in spec])
    _reset_registry()
    register_plugin(plugin)
    config = make_config(benchmark="searchqa", mode="full", split=None,
                         tasks=["q1", "q2"])
    ctx = make_ctx(tmp_path, config)
    stage0_freeze.run(ctx)
    manifest = load_manifest(ctx.run_dir)
    frozen = manifest["prompt_digests"]
    assert frozen["SEARCHQA_TASK_PROMPT"] == prompts.digest(
        "SEARCHQA_TASK_PROMPT")
    assert set(frozen) == (set(prompts.METHOD_PROMPT_NAMES)
                           | {"SEARCHQA_TASK_PROMPT"})
    # The plugin's declared auxiliary metric is frozen by name.
    assert manifest["aux_metric"] == "sub_em"


def test_declared_aux_metric_name_is_frozen_and_changing_it_refuses(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    plugin.aux_metric_name = "fake_aux"
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)
    assert load_manifest(ctx.run_dir)["aux_metric"] == "fake_aux"

    # Same run directory, the plugin's declaration changed: the frozen
    # protocol differs, so the resume is refused (pre-registration means
    # the metric is part of the experiment identity, not a free choice).
    plugin.aux_metric_name = None
    with pytest.raises(FrozenProtocolChanged, match="aux_metric"):
        stage0_freeze.run(make_ctx(tmp_path, make_config()))


# ---------------------------------------------------------------------------
# Duplicate task ids from the plugin (e.g. a task file listing one twice)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("split", [None, {"ratio": 0.5, "seed": 0},
                                   {"ratio": 0.5, "seed": 3}])
def test_duplicate_ids_in_task_file_rejected_before_split(
        monkeypatch, tmp_path, split):
    _install_plugin(monkeypatch, TBLikePlugin())
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("t1\nt2\nt3\nt2\nt1\n", encoding="utf-8")
    mode = "full" if split is None else "split"
    ctx = make_ctx(tmp_path, make_config(mode=mode, split=split,
                                         tasks=str(task_file)))
    with pytest.raises(ConfigError, match=r"duplicate.*\['t1', 't2'\]"):
        stage0_freeze.run(ctx)
    assert not (ctx.run_dir / "manifest.json").exists()
