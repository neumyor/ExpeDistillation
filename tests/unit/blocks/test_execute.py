"""E1 run_one unit tests: plugin wiring, correctness pass-through, bookkeeping.

Only a fake plugin registered through the real registry/import machinery;
no real plugin packages, no network, no disk writes by run_one itself
(AGENTS.md section 8). The lazy-import path is exercised for real: a stub
finder serves the plugin package, whose import performs the registration --
the same route a real plugin takes (benchmarks/registry.py contract)."""

import sys
from pathlib import Path

import pytest

from expedistill.benchmarks.base import RunOutcome, Task
from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks.execute import make_task_id, run_one
from expedistill.core.ids import make_trajectory_id
from expedistill.infra.reliability import ConfigError
from tests.helpers.fake_plugin import serve_plugin
from tests.helpers.stub_plugin import StubPlugin

pytestmark = pytest.mark.usefixtures("clean_registry")

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
PHASE = "p03:practice"


def _run(plugin, tmp_path, **overrides):
    kwargs = dict(
        phase_label=PHASE,
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
    plugin = serve_plugin(monkeypatch, StubPlugin(outcome=outcome))
    task = Task(task_id="t1", instruction="do the thing")
    handle = object()
    traj = _run(plugin, tmp_path, task=task, handle=handle)
    task_id = make_task_id(_FAKE, "t1")
    assert traj.trajectory_id == make_trajectory_id(PHASE, task_id, "weak", 3)
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
    plugin = serve_plugin(monkeypatch, StubPlugin(outcome=outcome))
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
    plugin = serve_plugin(monkeypatch, StubPlugin())
    traj = _run(plugin, tmp_path, skills_injected=skills_injected)
    assert traj.skills_injected == skills_injected


def test_lazy_import_triggers_registration(monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, StubPlugin())
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
        _run(StubPlugin("nosuchbench"), tmp_path)
    # The message names the plugin and the expected module path.
    assert "expedistill.benchmarks.nosuchbench" in str(excinfo.value)


def test_module_imported_but_not_registered_is_config_error(
        monkeypatch, tmp_path):
    # The package imports fine but registers nothing under this benchmark
    # name: the registry's unknown-name ConfigError fires, not an ImportError.
    serve_plugin(
        monkeypatch, StubPlugin(name="otherbench"), name=_FAKE)
    with pytest.raises(ConfigError, match="unknown benchmark plugin"):
        _run(StubPlugin(_FAKE), tmp_path)


def test_plugin_runtime_error_propagates(monkeypatch, tmp_path):
    plugin = serve_plugin(
        monkeypatch, StubPlugin(error=RuntimeError("plugin bug")))
    with pytest.raises(RuntimeError, match="plugin bug"):
        _run(plugin, tmp_path)


def test_run_one_never_writes_disk(monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, StubPlugin(outcome=RunOutcome(
        steps=[{"action": "ls", "observation": "a.txt"}], correctness=False)))
    _run(plugin, tmp_path)
    # The workdir is the caller's to create; E1 itself writes nothing.
    assert list(Path(tmp_path).rglob("*")) == []
