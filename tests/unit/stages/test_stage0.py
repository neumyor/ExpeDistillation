"""Stage 0 unit tests: freeze write, resume stability, mismatch refusal.

Fake plugin registered through the real registry/import machinery (the stub
finder pattern of tests/unit/blocks/test_execute.py); no real plugins, no
network, no model calls (stage 0 executes nothing)."""

import copy
import importlib
import importlib.abc
import importlib.machinery
import hashlib
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
from expedistill.blocks.tasks import tasks_digest
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


#: The exam-style method every fixture carries (an A phase so stage 0
#: resolves the exam execution and the merge_check: exam constraint).
SEQUENCE = ["D", "L", "A", "L", "C"]
PHASES = {
    "D": {"kind": "execute", "role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]}},
    "L": {"kind": "learn",
          "evidence": {"name": "segment", "K": 4},
          "author": {"name": "distill_diagnose"},
          "curate": {"name": "agentbrew", "token_budget": 4096,
                     "rounds": 3, "merge_check": "exam"}},
    "A": {"kind": "execute", "role": "exam", "tasks": "train",
          "delivery": {"name": "per_skill"},
          "execution": {"name": "exam"}},
    "C": {"kind": "execute", "role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "fresh_bare"}},
}


def make_config(**overrides):
    defaults = dict(
        benchmark=_FAKE,
        mode="split",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1,
        n_attempts_weak=2,
        sequence=list(SEQUENCE),
        phases=copy.deepcopy(PHASES),
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
    # its ledger), so its exact key set is part of the contract. All three
    # split keys are always present (ruling 7); a two-key spec means
    # reserved is [], never absent.
    assert set(result) == {"tasks", "train", "test", "reserved"}
    assert result["reserved"] == []
    json.dumps(result)

    manifest = load_manifest(ctx.run_dir)
    assert manifest["benchmark"] == _FAKE
    assert manifest["mode"] == "split"
    assert manifest["split"] == {"train": ["t1", "t2"], "test": ["t3"],
                                "reserved": []}
    assert manifest["model_roles"]["executor_strong"] == "big-model"
    assert manifest["rules"] == config.rules
    # The fake plugin declares no auxiliary metric: frozen as null.
    assert manifest["aux_metric"] is None
    assert manifest["prompt_digests"]["SEGMENT"]
    # The frozen config copy carries the digests (core.config contract).
    assert manifest["config"]["prompts_digests"] == manifest["prompt_digests"]
    assert (manifest["config"]["phases"]["L"]["evidence"]["K"]
            == config.phases["L"].evidence["K"])
    # Explicit-list configs load exactly the union of the lists (reserved
    # included -- held-back tasks are still loaded).
    assert plugin.load_specs == [["t1", "t2", "t3"]]

    summary = read_json(ctx.run_dir / "stage0_summary.json")
    assert summary == {"tasks": 3, "train": ["t1", "t2"], "test": ["t3"],
                       "reserved": []}


def test_full_mode_freezes_whole_set_as_train_and_test(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    config = make_config(mode="full", split=None)
    ctx = make_ctx(tmp_path, config)
    result = stage0_freeze.run(ctx)
    assert result["tasks"] == 3
    assert result["train"] == list(TASK_IDS)
    assert result["test"] == list(TASK_IDS)
    assert result["reserved"] == []
    manifest = load_manifest(ctx.run_dir)
    assert manifest["split"] == {
        "train": list(TASK_IDS), "test": list(TASK_IDS), "reserved": []}


# ---------------------------------------------------------------------------
# reserved: the third split list is loaded, frozen, held out of both arms
# ---------------------------------------------------------------------------

def test_reserved_flow_freezes_three_keys_and_loads_reserved(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    config = make_config(
        split={"train": ["t1"], "test": ["t2"], "reserved": ["t3"]})
    ctx = make_ctx(tmp_path, config)
    result = stage0_freeze.run(ctx)

    # reserved is LOADED (the lists must cover the task set exactly) but
    # lands in neither arm's list.
    assert result == {"tasks": 3, "train": ["t1"], "test": ["t2"],
                      "reserved": ["t3"]}
    # The union of all three lists is what D1 loaded.
    assert plugin.load_specs == [["t1", "t2", "t3"]]

    manifest = load_manifest(ctx.run_dir)
    assert manifest["split"] == {"train": ["t1"], "test": ["t2"],
                                "reserved": ["t3"]}
    summary = read_json(ctx.run_dir / "stage0_summary.json")
    assert summary == result


def test_reserved_with_config_tasks_must_cover_exactly(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, StagePlugin())
    # Reserved counts toward coverage like any other list: a task set
    # missing the reserved task is a ConfigError naming it.
    config = make_config(
        split={"train": ["t1"], "test": ["t2"], "reserved": ["t3"]},
        tasks=["t1", "t2"])
    ctx = make_ctx(tmp_path, config)
    with pytest.raises(ConfigError, match="t3"):
        stage0_freeze.run(ctx)
    assert not (ctx.run_dir / "manifest.json").exists()
    assert plugin.load_specs == [["t1", "t2"]]


def test_two_key_spec_regression_freezes_empty_reserved(monkeypatch, tmp_path):
    # The pre-v2 shape keeps working and freezes reserved: [] -- old
    # configs are historical records that must still load and resume.
    _install_plugin(monkeypatch, StagePlugin())
    config = make_config()
    ctx = make_ctx(tmp_path, config)
    stage0_freeze.run(ctx)
    # A resume of the same two-key run is byte-stable.
    manifest_bytes = (ctx.run_dir / "manifest.json").read_bytes()
    stage0_freeze.run(make_ctx(tmp_path, config))
    assert (ctx.run_dir / "manifest.json").read_bytes() == manifest_bytes


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

    phases = copy.deepcopy(PHASES)
    phases["L"]["evidence"]["K"] = 9
    changed = make_ctx(tmp_path, make_config(phases=phases))
    with pytest.raises(FrozenProtocolChanged,
                       match=r"config\.phases\.L\.evidence\.K"):
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

    forward = tasks_digest(tasks(["t1", "t2"], "/x/g.tw"))
    backward = tasks_digest(tasks(["t2", "t1"], "/x/g.tw"))
    assert forward == backward
    assert len(forward) == 64
    assert tasks_digest(tasks(["t1", "t2"], "/x/h.tw")) \
        != forward


def test_object_repr_fallback_is_deterministic():
    class Opaque:
        pass

    def digest():
        return tasks_digest(
            [Task(task_id="t1", instruction="x", meta={"o": Opaque()})])

    # Default reprs embed a memory address; two fresh objects must still
    # hash the same, or every resume would refuse spuriously.
    assert digest() == digest()


def test_manifest_freezes_tasks_digest(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)
    manifest = load_manifest(ctx.run_dir)
    expected = tasks_digest(
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
                      "test": ["t1", "t3"], "reserved": []}
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
    assert result == {"tasks": 2, "train": ["t2"], "test": ["t1"],
                      "reserved": []}


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
# ---------------------------------------------------------------------------
# The external-campaign control's score file: verified at freeze
# ---------------------------------------------------------------------------

def test_external_control_file_verified_at_freeze(monkeypatch, tmp_path):
    """A C phase comparing against the external campaign pins its score
    file by sha256; the freeze step refuses a missing or drifted file
    BEFORE anything is written -- the file is part of the frozen
    method's identity, not a runtime lookup."""
    _install_plugin(monkeypatch, StagePlugin())
    payload = {"tasks": {"qwen:t3": {"n": 5, "rewards": [1.0] * 5,
                                    "success_rate": 1.0}}}
    score_path = tmp_path / "scores.json"
    score_path.write_text(json.dumps(payload), encoding="utf-8")
    sha = hashlib.sha256(score_path.read_bytes()).hexdigest()
    phases = copy.deepcopy(PHASES)
    phases["C"]["compare"] = {
        "against": "external_campaign",
        "external": {"path": str(score_path), "sha256": sha,
                     "model_key": "qwen"}}
    ctx = make_ctx(tmp_path, make_config(phases=phases))
    stage0_freeze.run(ctx)
    manifest = load_manifest(ctx.run_dir)
    assert manifest["config"]["phases"]["C"]["compare"] == {
        "against": "external_campaign", "rule": None,
        "external": {"path": str(score_path), "sha256": sha,
                     "model_key": "qwen"}}

    # A drifted file refuses the freeze before anything is written.
    score_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    run_dir2 = tmp_path / "run2"
    ctx2 = make_ctx(tmp_path, make_config(phases=phases),
                    run_name="run2")
    with pytest.raises(ConfigError, match="hashes to sha256"):
        stage0_freeze.run(ctx2)
    assert not (run_dir2 / "manifest.json").exists()

    # A missing file refuses the same way.
    score_path.unlink()
    ctx3 = make_ctx(tmp_path, make_config(phases=phases),
                    run_name="run3")
    with pytest.raises(ConfigError, match="does not exist"):
        stage0_freeze.run(ctx3)


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


# ---------------------------------------------------------------------------
# Acceptance protocol (decision doc 2026-10-14): the manifest golden, the
# live_run digest branch and the improve-rule name check
# ---------------------------------------------------------------------------

#: Golden baselines for OLD-STYLE configs (no acceptance key), captured from
#: a pre-acceptance-code freeze of this exact fixture and hardcoded as
#: literals -- never regenerated at run time, or the test would bless
#: whatever the new code produces (the same discipline as the to_dict
#: golden in tests/unit/core/test_config.py). Key set, every value and the
#: nested prompts_digests map must stay identical: this is the frozen
#: manifest surface an in-flight exam run byte-compares on every resume.
#: A deliberate prompt-text change is a new experiment and must
#: consciously update these digests. Two shapes because the trap is
#: shape-specific: full mode freezes split/tasks as null (a generic None
#: filter would delete them and kill the resume); the split shape pins the
#: explicit-list form.
_GOLDEN_PROMPT_DIGESTS = {
    "ANSWER": "35bc0de88ed2152f1605c01da693cddf976733995950f10919dd146bc6113f42",
    "DEFAULT_SYSTEM": "8eefa21c49e174e2e48ea6834a1511e58039bda895dea12a3653a19198c73bb0",
    "EXAM_GEN": "54e6f98deed982d76bd652c23170ca73a9f090a22790c703f297294b331aa3b6",
    "FAKE_TASK": None,
    "GROUP": "b92d616f6cc04c7463b9943372de340186417bc77f9149bbb4ad803bd6c30dd0",
    "JUDGE": "7d84174e22ddabd93743d1715a6602332c881995508b82c089d38d033af236fb",
    "LEAK_AUDIT_JUDGE": "526ccb6d5a54b34264edf2cf2cde2f4d0ece8e4888647887b33db5bda85b508b",
    "MERGE": "46844826e71fe7c5c403ba6ccfe192ef17e89122d5e7fb15597421c9842f3bfc",
    "REPAIR_INSTRUCTION": "dea3ada843291850d76759cd9a476f8b4a3a9b357e4ca60b3cf785b9afbc727c",
    "REWRITE": "5221c24ced0a12f5d965bb923d23daf463efdb25a26729b798c18ef75da7d69b",
    "SEGMENT": "19c5562a02b93c11a8c0475d057154e17bd0c8f6b1bf017f1f274cdea798cc8b",
    "SIMILARITY": "fd5ff69610c2c1047d3eee012a503002d12cc2e859707b4d6bc88536cdd4e610",
    "SKILL_DIAGNOSE": "0d5b9416627d27f430bfa1b7cbe4458000f13cb839c0989e0f9e5a0c74e4609c",
    "SKILL_DISTILL": "48bfbaea7780ad51b4e1e586aef843b140a0e7157187086f99188da61830b00d",
    # v3 full freeze: the two live prompts register at prompts/__init__
    # import like every method prompt, so their digests are frozen on
    # every run -- exam runs included.
    "LIVE_RUN_ATTRIBUTION": prompts.digest("LIVE_RUN_ATTRIBUTION"),
    "LIVE_SKILL_REVISION": prompts.digest("LIVE_SKILL_REVISION"),
}

_GOLDEN_MODEL_ROLES = {
    "executor_strong": "big-model",
    "executor_weak": "weak-7b",
    "segmenter": "big-model",
    "author": "big-model",
    "examiner": "big-model",
    "answerer": "weak-7b",
    "judge": "big-model",
    "rewriter": "big-model",
    "curator": "big-model",
    "auditor": "big-model",
}

_GOLDEN_RULES = {
    "task_select": "all_valid_failed",
    "segment_select": "strong_success_weak_failed",
    "trajectory_select": "all",
}


def _golden_config(split):
    return {
        "benchmark": "fakebench",
        "mode": "full" if split is None else "split",
        "models": dict(_GOLDEN_MODEL_ROLES),
        "weak_profile": "a weak model",
        "n_attempts_strong": 1,
        "n_attempts_weak": 2,
        "split": split,
        "tasks": None,
        "sequence": ["D", "L", "A", "L", "C"],
        "phases": {
            "D": {"role": "baseline", "tasks": "train",
                  "delivery": {"name": "none"},
                  "execution": {"name": "real",
                                "arms": ["executor_strong",
                                         "executor_weak"]},
                  "compare": None, "acceptance": None,
                  "kind": "execute"},
            "L": {"evidence": {"name": "segment", "K": 4},
                  "author": {"name": "distill_diagnose"},
                  "curate": {"name": "agentbrew", "token_budget": 4096,
                             "rounds": 3, "merge_check": "exam"},
                  "kind": "learn", "role": "learn"},
            "A": {"role": "exam", "tasks": "train",
                  "delivery": {"name": "per_skill"},
                  "execution": {"name": "exam"},
                  "compare": None, "acceptance": None,
                  "kind": "execute"},
            "C": {"role": "test", "tasks": "test",
                  "delivery": {"name": "whole_bank"},
                  "execution": {"name": "real"},
                  "compare": {"against": "fresh_bare", "rule": None,
                              "external": None},
                  "acceptance": None, "kind": "execute"},
        },
        "invalid_redispatch_limit": 1,
        "rules": dict(_GOLDEN_RULES),
        "prompts_digests": dict(_GOLDEN_PROMPT_DIGESTS),
        "model_options": {},
        "token_budgets": {},
        "schema_version": 4,
    }


def _golden_manifest(split, train, test):
    return {
        "benchmark": "fakebench",
        "mode": "full" if split is None else "split",
        "split": {"train": train, "test": test, "reserved": []},
        "config": _golden_config(split),
        "prompt_digests": dict(_GOLDEN_PROMPT_DIGESTS),
        "rules": dict(_GOLDEN_RULES),
        "model_roles": dict(_GOLDEN_MODEL_ROLES),
        "tasks_digest": "79e8c92ae85de702de682e2eb31790859c41340d6d7f296b041cd3bd6d003553",
        "aux_metric": None,
    }


@pytest.mark.parametrize("shape", ["full", "split"])
def test_exam_config_freeze_mapping_matches_the_fixed_baseline(
        monkeypatch, tmp_path, shape):
    """The frozen manifest matches the pinned v4 literal: the parsed
    phase definitions (canonical form), and the prompt_digests KEY SET
    of every registered method prompt (the full freeze -- the two live
    names included by design, their digests never called by an exam
    run)."""
    _install_plugin(monkeypatch, StagePlugin())
    if shape == "full":
        config = make_config(mode="full", split=None)
        expected = _golden_manifest(None, list(TASK_IDS), list(TASK_IDS))
    else:
        config = make_config()
        expected = _golden_manifest({"train": ["t1", "t2"],
                                     "test": ["t3"]},
                                    ["t1", "t2"], ["t3"])
    ctx = make_ctx(tmp_path, config)

    stage0_freeze.run(ctx)

    assert load_manifest(ctx.run_dir) == expected
    # The pinned name set, spelled out: the two live prompts ARE part of
    # every manifest now (the full freeze), with their real digests.
    frozen = load_manifest(ctx.run_dir)["prompt_digests"]
    assert frozen["LIVE_RUN_ATTRIBUTION"] == prompts.digest(
        "LIVE_RUN_ATTRIBUTION")
    assert frozen["LIVE_SKILL_REVISION"] == prompts.digest(
        "LIVE_SKILL_REVISION")
    # The A phase's exam execution freezes like every phase definition.
    assert (load_manifest(ctx.run_dir)["config"]["phases"]["A"]
            ["execution"] == {"name": "exam"})


def test_the_full_prompt_freeze_covers_the_live_digests(
        monkeypatch, tmp_path):
    # Since v3 the freeze is FULL: an exam-sequence run still freezes the
    # two live prompts' digests (they register at prompts/__init__ import
    # like every method prompt) -- a manifest listing digests this run
    # never calls is noise, not risk.
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())

    stage0_freeze.run(ctx)

    manifest = load_manifest(ctx.run_dir)
    frozen = manifest["prompt_digests"]
    assert set(frozen) == set(prompts.METHOD_PROMPT_NAMES) | {"FAKE_TASK"}
    assert frozen["LIVE_RUN_ATTRIBUTION"] == prompts.digest(
        "LIVE_RUN_ATTRIBUTION")
    assert frozen["LIVE_SKILL_REVISION"] == prompts.digest(
        "LIVE_SKILL_REVISION")
    assert manifest["config"]["prompts_digests"] == frozen


def test_unknown_compare_rule_stops_stage0(monkeypatch, tmp_path):
    """The comparison criterion is part of the experiment identity: a
    typo in an execute phase's compare.rule name is a ConfigError
    before anything is loaded or frozen, exactly like the three
    task/segment/trajectory rule names."""
    plugin = _install_plugin(monkeypatch, StagePlugin())
    config = make_config(
        sequence=["D", "L", "B", "L", "C"],
        phases={
            "D": PHASES["D"],
            # A live-only sequence: merge_check flips to 'none' (the
            # load-time checklist requires an exam phase otherwise).
            "L": {**PHASES["L"],
                  "curate": {"name": "agentbrew", "token_budget": 4096,
                             "rounds": 3, "merge_check": "none"}},
            "B": {"kind": "execute", "role": "live", "tasks": "train",
                  "delivery": {"name": "group_by_task"},
                  "execution": {"name": "real", "n_attempts": 2},
                  "compare": {"against": "D", "rule": "no_such_rule"}},
            "C": PHASES["C"],
        })
    ctx = make_ctx(tmp_path, config)
    with pytest.raises(ConfigError, match="no_such_rule"):
        stage0_freeze.run(ctx)
    assert plugin.load_specs == []
    assert not (ctx.run_dir / "manifest.json").exists()
    assert not (ctx.run_dir / "stage0_summary.json").exists()


def test_unknown_acceptance_rule_stops_stage0(monkeypatch, tmp_path):
    """C11: an execute phase's named verdict rule (``acceptance``) is
    part of the experiment identity and validates against the verdict
    registry before anything is loaded or frozen."""
    plugin = _install_plugin(monkeypatch, StagePlugin())
    phases = copy.deepcopy(PHASES)
    phases["A"]["acceptance"] = "no_such_verdict_rule"
    ctx = make_ctx(tmp_path, make_config(phases=phases))
    with pytest.raises(ConfigError, match="no_such_verdict_rule"):
        stage0_freeze.run(ctx)
    assert plugin.load_specs == []
    assert not (ctx.run_dir / "manifest.json").exists()


@pytest.mark.parametrize("frozen_version", [2, 3])
def test_pre_v4_run_directory_gets_the_maintenance_branch_hint(
        monkeypatch, tmp_path, frozen_version):
    """Schema v4 code cannot resume a pre-v4-frozen run (the key sets
    differ by design: the v3 ``methods`` slots are gone); the refusal
    points at the maintenance-branch rule instead of burying the reason
    in the generic manifest diff (reviewer A). One gate, both eras --
    the third place the version gate is unified with load_config and
    from_dict."""
    plugin = _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config())
    stage0_freeze.run(ctx)
    manifest_path = ctx.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config"]["schema_version"] = frozen_version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigError, match="maintenance"):
        stage0_freeze.run(ctx)
    # Exactly the FIRST call's load: the refused resume loads nothing.
    assert plugin.load_specs == [["t1", "t2", "t3"]]
