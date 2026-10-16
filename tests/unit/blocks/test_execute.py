"""E1 run_one unit tests: plugin wiring, status mapping, bookkeeping.

Only a fake plugin registered through the real registry/import machinery;
no real plugin packages, no network, no disk writes by run_one itself
(AGENTS.md section 8). The lazy-import path is exercised for real: a stub
finder serves the plugin package, whose import performs the registration --
the same route a real plugin takes (benchmarks/registry.py contract)."""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types
from pathlib import Path

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import (
    _reset_registry,
    get_plugin,
    register_plugin,
)
from expedistill.blocks.execute import make_task_id, run_one
from expedistill.core import ids
from expedistill.core.ids import make_trajectory_id
from expedistill.infra.reliability import ConfigError

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


class FakePlugin(BenchmarkPlugin):
    """Records its run() arguments and returns a canned outcome."""

    def __init__(self, name=_FAKE, outcome=None, error=None):
        self._name = name
        self.outcome = outcome if outcome is not None else RunOutcome()
        self.error = error
        self.calls = []

    @property
    def name(self):
        return self._name

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({
            "task": task,
            "handle": handle,
            "skills_block": skills_block,
            "workdir": workdir,
        })
        if self.error is not None:
            raise self.error
        return self.outcome

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _install_plugin(monkeypatch, plugin, module_name=None):
    """Serve ``plugin``'s package through the real import machinery.

    A stub finder/loader stands in for the plugin package on disk; the
    module body (``exec_module``) registers the plugin, exactly like a real
    plugin package's ``__init__``. ``run_one`` must therefore actually
    import the module for the plugin to resolve -- no shortcut around
    importlib would pass these tests. ``module_name`` overrides the package
    the stub is served under (defaults to the plugin's own name).
    """
    fullname = f"expedistill.benchmarks.{module_name or plugin.name}"

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
    # The import inside run_one caches the stub module in sys.modules; drop
    # it so the next test's import re-executes (and re-registers) instead of
    # silently hitting a stale registration from a reset registry.
    sys.modules.pop(_MODULE, None)
    _reset_registry()


def _run(plugin, tmp_path, **overrides):
    kwargs = dict(
        benchmark=plugin.name,
        task=Task(task_id="t1", instruction="do the thing", meta={"gold": "x"}),
        model_role="weak",
        model_name="weak-7b",
        attempt_index=3,
        skills_injected=None,
        skills_block="",
        handle=object(),
        workdir=tmp_path,
    )
    kwargs.update(overrides)
    return run_one(**kwargs)


def test_success_assembles_full_trajectory(monkeypatch, tmp_path):
    outcome = RunOutcome(
        steps=[
            {"action": "ls", "observation": "a.txt"},
            {"action": "finish", "observation": "done"},
        ],
        correctness=True,
        meta={"termination": "terminated", "steps_used": 2},
    )
    plugin = _install_plugin(monkeypatch, FakePlugin(outcome=outcome))
    task = Task(task_id="t1", instruction="do the thing")
    handle = object()
    traj = run_one(
        benchmark=_FAKE,
        task=task,
        model_role="weak",
        model_name="weak-7b",
        attempt_index=3,
        skills_injected=None,
        skills_block="",
        handle=handle,
        workdir=tmp_path,
    )
    task_id = make_task_id(_FAKE, "t1")
    assert traj.trajectory_id == make_trajectory_id(task_id, "weak", 3)
    assert traj.task_id == task_id == "fakebench:t1"
    assert traj.benchmark == _FAKE
    assert traj.model_role == "weak"
    assert traj.model_name == "weak-7b"
    assert traj.attempt_index == 3
    assert traj.skills_injected is None
    assert [(s.action, s.observation) for s in traj.steps] == [
        ("ls", "a.txt"), ("finish", "done")]
    assert traj.correctness is True
    # meta: the plugin's dict plus workdir; the plugin's own dict untouched.
    assert traj.meta == {
        "termination": "terminated",
        "steps_used": 2,
        "workdir": str(tmp_path),
    }
    assert outcome.meta == {"termination": "terminated", "steps_used": 2}
    # run() received every argument verbatim.
    assert plugin.calls == [{
        "task": task,
        "handle": handle,
        "skills_block": "",
        "workdir": tmp_path,
    }]


@pytest.mark.parametrize("correctness", [False, None])
def test_correctness_passes_through_verbatim(monkeypatch, tmp_path,
                                             correctness):
    # Whatever the plugin reported -- the scorer said wrong (False) or said
    # nothing (None) -- E1 records it as the fact it is, no conclusion drawn.
    outcome = RunOutcome(
        steps=[{"action": "ls", "observation": "empty"}],
        correctness=correctness,
        meta={"error": "boom"},
    )
    plugin = _install_plugin(monkeypatch, FakePlugin(outcome=outcome))
    traj = _run(plugin, tmp_path)
    assert traj.correctness is correctness
    assert traj.meta == {"error": "boom", "workdir": str(tmp_path)}


@pytest.mark.parametrize(
    "skills_injected",
    [
        None,
        [{"skill_id": "traj/x/weak/00/seg01/skill", "version": 2}],
        ["skill_a@v1", "skill_b@v3"],
        "bank:9f2c1a",
    ],
)
def test_skills_injected_passthrough_verbatim(
        monkeypatch, tmp_path, skills_injected):
    plugin = _install_plugin(monkeypatch, FakePlugin())
    traj = _run(plugin, tmp_path, skills_injected=skills_injected)
    assert traj.skills_injected == skills_injected


def test_strong_role_and_high_attempt_index_in_id(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, FakePlugin())
    traj = _run(
        plugin, tmp_path,
        model_role="strong", model_name="big-model", attempt_index=11,
    )
    task_id = make_task_id(_FAKE, "t1")
    assert traj.trajectory_id == f"traj/{task_id}/strong/11"


def test_lazy_import_triggers_registration(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, FakePlugin())
    # Before run_one: the package is not imported and nothing is registered.
    assert _MODULE not in sys.modules
    with pytest.raises(ConfigError):
        get_plugin(_FAKE)
    _run(plugin, tmp_path)
    # The import really ran, and it is what registered the plugin.
    assert _MODULE in sys.modules
    assert get_plugin(_FAKE) is plugin


def test_missing_plugin_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="nosuchbench") as excinfo:
        run_one(
            benchmark="nosuchbench",
            task=Task(task_id="t1", instruction="do the thing"),
            model_role="weak",
            model_name="weak-7b",
            attempt_index=0,
            skills_injected=None,
            skills_block="",
            handle=object(),
            workdir=tmp_path,
        )
    # The message names the plugin and the expected module path.
    assert "expedistill.benchmarks.nosuchbench" in str(excinfo.value)


def test_module_imported_but_not_registered_is_config_error(
        monkeypatch, tmp_path):
    # The package imports fine but registers nothing under this benchmark
    # name: the registry's unknown-name ConfigError fires, not an ImportError.
    _install_plugin(
        monkeypatch, FakePlugin(name="otherbench"), module_name=_FAKE)
    with pytest.raises(ConfigError, match="unknown benchmark plugin"):
        run_one(
            benchmark=_FAKE,
            task=Task(task_id="t1", instruction="do the thing"),
            model_role="weak",
            model_name="weak-7b",
            attempt_index=0,
            skills_injected=None,
            skills_block="",
            handle=object(),
            workdir=tmp_path,
        )


def test_plugin_runtime_error_propagates(monkeypatch, tmp_path):
    plugin = _install_plugin(
        monkeypatch, FakePlugin(error=RuntimeError("plugin bug")))
    with pytest.raises(RuntimeError, match="plugin bug"):
        _run(plugin, tmp_path)


def test_run_one_never_writes_disk(monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, FakePlugin(outcome=RunOutcome(
        steps=[{"action": "ls", "observation": "a.txt"}], correctness=False)))
    run_dir = tmp_path / "my_run"
    _run(plugin, tmp_path, usage=object(), run_dir=run_dir)
    # Neither the workdir nor the (accepted-but-unused) run_dir exist on disk.
    assert list(Path(tmp_path).rglob("*")) == []


def test_make_task_id_is_the_core_ids_rule():
    assert make_task_id is ids.make_task_id
    assert make_task_id("tb", "problem-01") == "tb:problem-01"
