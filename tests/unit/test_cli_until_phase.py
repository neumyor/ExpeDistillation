"""W3-B: ``--until-phase -1`` runs the freeze step and no phase.

The pre-run seeders (scripts/run/seed_tb_stage1.py) need a manifest and a
task table on disk BEFORE any phase dispatches -- under the stage world
that was ``--until-stage 0``; under the phase world the freeze step is
p00 and the sequence starts at p01, so "before the first phase" is -1
(the coordinator's 2026-10-16 ruling, authorizing this one cli.py
change on the retired W2-C file). Pinned here: -1 freezes and dispatches
nothing, the stop stays resumable, and the bound error explains -1.
"""

import importlib.abc
import importlib.machinery
import json
import sys
import types

import pytest

from expedistill import cli
from expedistill.benchmarks.base import BenchmarkPlugin, Task, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.core.config import PhaseResult
from expedistill.infra.reliability import ConfigError as InfraConfigError
from expedistill.infra.store import read_jsonl

_FAKE = "freezebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
SEQUENCE = ["baseline", "learn", "test"]
PHASES = {
    "baseline": {"kind": "execute", "role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]}},
    "learn": {"kind": "learn",
          "review": {"name": "review", "K": 1},
          "author": {"name": "distill_diagnose"},
          "curate": {"name": "none", "token_budget": 4096}},
    "test": {"kind": "execute", "role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "fresh_bare"}},
}


class FreezePlugin(BenchmarkPlugin):
    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        return [Task(task_id=i, instruction=f"do {i}")
                for i in (tasks_spec or ("t1", "t2"))]

    def run(self, task, handle, skills_block, workdir):
        raise AssertionError("the fake drivers must intercept every phase")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


@pytest.fixture()
def plugin(monkeypatch):
    fullname = _MODULE

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(FreezePlugin())

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    _reset_registry()
    sys.modules.pop(fullname, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    yield
    sys.modules.pop(fullname, None)
    _reset_registry()


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "exp.yaml"
    path.write_text(json.dumps({
        "benchmark": _FAKE,
        "mode": "split",
        "models": {"executor_strong": "strong-model",
                   "executor_weak": "weak-model"},
        "n_attempts_strong": 1,
        "n_attempts_weak": 1,
        "schema_version": 4,
        "sequence": SEQUENCE,
        "phases": PHASES,
        "split": {"train": ["t1"], "test": ["t2"]},
        "token_budgets": {"default_max_output_tokens": 65536},
    }), encoding="utf-8")
    return path


@pytest.fixture()
def drivers(monkeypatch):
    calls = []

    def install(kind):
        module = types.ModuleType(f"expedistill.stages.phase_{kind}")

        def run(ctx, phase):
            calls.append((kind, phase))
            return PhaseResult(label=phase.label, summary={})

        module.run = run
        monkeypatch.setitem(sys.modules, f"expedistill.stages.phase_{kind}",
                            module)

    install("execute")
    install("learn")
    return calls


class _FakeClient:
    def complete(self, prompt, max_tokens=None, extra=None, **kwargs):
        return ""


def _make_client(model_name):
    return _FakeClient()


def test_until_phase_minus_one_freezes_and_dispatches_no_phase(
        plugin, config_path, tmp_path, drivers):
    run_dir = tmp_path / "run"
    summary = cli.run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=_make_client, until_phase=-1)

    assert drivers == []  # no phase driver was ever called
    rows = read_jsonl(cli.ledger_path(run_dir))
    assert [row["stage"] for row in rows] == ["p00:freeze"]
    # The return value is the freeze summary (a real summary, not a
    # placeholder): the seeder reads the task table out of it.
    assert summary["train"] == ["t1"]
    assert summary["test"] == ["t2"]
    # The manifest is on disk: the whole point of the stop.
    assert (run_dir / "manifest.json").is_file()


def test_a_frozen_run_resumes_from_the_phase_after_the_freeze(
        plugin, config_path, tmp_path, drivers):
    run_dir = tmp_path / "run"
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=_make_client, until_phase=-1)
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=_make_client, until_phase=0)
    assert [phase.label for _, phase in drivers] == ["p01:baseline"]
    rows = read_jsonl(cli.ledger_path(run_dir))
    # The resume replays the freeze step (its row is appended again --
    # every start re-runs the manifest comparison) and then the phase.
    assert [row["stage"] for row in rows] == [
        "p00:freeze", "p00:freeze", "p01:baseline"]


def test_the_bound_error_explains_the_minus_one_meaning(
        plugin, config_path, tmp_path, drivers):
    with pytest.raises(InfraConfigError) as caught:
        cli.run_experiment(config_path=config_path,
                           run_dir=tmp_path / "run",
                           make_client=_make_client, until_phase=-2)
    assert "run the freeze step only" in str(caught.value)
