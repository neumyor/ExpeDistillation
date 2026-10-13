"""K1 batch_execute composite tests: resume, per-unit persistence, retries.

Fake plugin registered through the real registry/import machinery (the stub
finder pattern of tests/unit/blocks/test_execute.py), fake model handle from
llm.fake, real Store on a temp run directory. No network, no real plugins
(AGENTS.md section 8). The interrupt-resume tests simulate a crash by letting
one unit raise a code-bug exception (which propagates, exactly like a real
crash would) and check that the next call redoes only the missing unit and
ends with the same result as an uninterrupted run.
"""

import importlib
import importlib.abc
import urllib.parse
import importlib.machinery
import os
import sys
import threading
import time
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
    ConfigError,
    _reset_registry,
    get_plugin,
    register_plugin,
)
from expedistill.composites.batch_execute import batch_execute
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.parallel import ParallelError
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError as ReliabilityConfigError,
    ProviderRefusalError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm.fake import make_fake_handle
from tests.unit.benchmarks.tb_fake_remote import JobsRemoteLink, trial_files

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


class RecordingPlugin(BenchmarkPlugin):
    """Stub plugin that records every run() call like a real runner would.

    Each call goes through the handle's client (one model request per
    execution), so the FakeClient's call count is an independent proof of how
    many executions really happened. ``behavior(record) -> RunOutcome`` may
    also raise, to simulate a code bug or script per-unit outcomes.
    """

    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        record = {
            "task_id": task.task_id,
            "attempt_token": task.meta.get("attempt_token"),
            "meta": dict(task.meta),
            "handle": handle,
            "skills_block": skills_block,
            "workdir": Path(workdir),
        }
        self.calls.append(record)
        handle.client.complete(handle.name, "system", task.instruction)
        if self.behavior is None:
            return _success(task.task_id)
        return self.behavior(record)


    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery
    (same contract as tests/unit/blocks/test_execute.py).

    Idempotent within a test: a second batch_execute call with the same
    plugin must not re-run the module body (which would try to register the
    plugin a second time).
    """
    fullname = f"expedistill.benchmarks.{plugin.name}"
    if fullname in sys.modules:
        try:
            if get_plugin(plugin.name) is plugin:
                return plugin
        except ConfigError:
            pass

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


def _tasks(*ids_):
    return [Task(task_id=i, instruction=f"do {i}", meta={"gold": i})
            for i in ids_]


def _expected_id(task_id, attempt):
    return make_trajectory_id(make_task_id(_FAKE, task_id), "weak", attempt)


def _success(task_id):
    """The default outcome: a canned success derived from the task id."""
    return RunOutcome(
        steps=[{"action": f"exec {task_id}", "observation": "ok"}],
        status="success",
    )


def _run_batch(plugin, monkeypatch, tmp_path, tasks, *, name="run",
               handle=None, **overrides):
    _install_plugin(monkeypatch, plugin)
    handle = handle if handle is not None else make_fake_handle("weak")
    kwargs = dict(
        store=Store(tmp_path / name),
        benchmark=_FAKE,
        tasks=tasks,
        role="weak",
        model_name="weak-7b",
        handle=handle,
        n_attempts=1,
        workdir_root=tmp_path / name / "workdirs",
    )
    kwargs.update(overrides)
    return batch_execute(**kwargs)


def _fingerprint(traj):
    """Everything that must match across stores; meta.workdir is run-local."""
    return (
        traj.trajectory_id,
        traj.status,
        [(s.action, s.observation) for s in traj.steps],
        traj.meta.get("attempt_token"),
    )


def _real_batch_module():
    """The real batch_execute module object.

    The composites package ``__init__`` rebinds the attribute
    ``expedistill.composites.batch_execute`` to the FUNCTION, so a plain
    ``import ... as`` yields the function; the module (whose globals hold
    ``random``, ``time`` and ``_HEARTBEAT_SECONDS``) must come from
    importlib.
    """
    return importlib.import_module("expedistill.composites.batch_execute")


def _record_batch_sleeps(monkeypatch):
    """Replace the real module's ``time`` with a recorder namespace: every
    stagger sleep is recorded instead of slept, while the heartbeat's
    monotonic clock and the audit timestamps keep the real functions."""
    module = _real_batch_module()
    real = module.time
    sleeps = []
    monkeypatch.setattr(module, "time", types.SimpleNamespace(
        sleep=sleeps.append, monotonic=real.monotonic, time=real.time))
    return sleeps


def _record_reliability_sleeps(monkeypatch):
    """Same for with_retries' sleeper (infra backoff waits)."""
    module = importlib.import_module("expedistill.infra.reliability")
    real = module.time
    waits = []
    monkeypatch.setattr(module, "time", types.SimpleNamespace(
        sleep=waits.append, time=real.time))
    return waits


#: K1's pre-resilience behaviour, for tests that exercise an infra-retry
#: path: one retry and zero backoff, so the test is immune to default
#: changes and never sleeps for real (the default backoff draws 15-45s).
_PLAIN_KNOBS = {"infra_retries": 1, "infra_backoff": (0.0, 0.0)}

#: Every timing knob on: per-unit dispatch stagger plus K1's resilience
#: defaults (2 infra retries, 15-45s random backoff). The tests patch the
#: sleep functions, so nothing actually waits.
_FULL_KNOBS = {"dispatch_stagger": 0.5}

#: The default backoff bounds (asserted by the all-on variants).
_BACKOFF_BOUNDS = (15.0, 45.0)


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def test_resume_skips_completed_units(monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    tasks = _tasks("t1", "t2")
    first = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                      n_attempts=2)
    assert [t.trajectory_id for t in first] == [
        _expected_id("t1", 0), _expected_id("t1", 1),
        _expected_id("t2", 0), _expected_id("t2", 1),
    ]
    handle = plugin.calls[0]["handle"]
    n_client_calls = len(handle.client.calls)

    plugin.calls.clear()
    second = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                       n_attempts=2, handle=handle)
    # No new executions at all: the plugin saw nothing and the model client
    # was not called again.
    assert plugin.calls == []
    assert len(handle.client.calls) == n_client_calls
    assert [_fingerprint(t) for t in second] == [_fingerprint(t) for t in first]


@pytest.mark.parametrize("knobs", [
    pytest.param(_PLAIN_KNOBS, id="plain"),
    pytest.param(_FULL_KNOBS, id="stagger_and_backoff"),
])
def test_interrupted_run_completes_only_the_missing_unit(
        monkeypatch, tmp_path, knobs):
    # The all-on variant is the AGENTS.md 8 rule 2 obligation: with every
    # timing knob live (stagger per unit, 2 retries, 15-45s random backoff)
    # an interrupted run must still resume to exactly the same result.
    sleeps = _record_batch_sleeps(monkeypatch)
    waits = _record_reliability_sleeps(monkeypatch)

    def fail_on_t3(record):
        if record["task_id"] == "t3":
            raise RuntimeError("simulated crash mid-run")
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=fail_on_t3)
    tasks = _tasks("t1", "t2", "t3")
    with pytest.raises(ParallelError):
        _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1", **knobs)
    # The two units that finished before the crash are safely stored.
    store = Store(tmp_path / "r1")
    assert store.ids("trajectories") == [_expected_id("t1", 0),
                                         _expected_id("t2", 0)]

    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                         **knobs)
    # Only the missing unit was executed again.
    assert [c["task_id"] for c in plugin.calls] == ["t3"]
    assert len(resumed) == 3

    # Same final state as an uninterrupted run over the same unit set.
    plugin.calls.clear()
    whole = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r2",
                       **knobs)
    assert [_fingerprint(t) for t in resumed] == \
        [_fingerprint(t) for t in whole]
    if "dispatch_stagger" in knobs:
        # The knobs really were live and changed nothing: one stagger sleep
        # per started unit (3 before the crash, 1 on the resume, 3 on the
        # clean run), and the crash path (a code bug) never hit the backoff.
        assert len(sleeps) == 7
        assert all(0 <= delay <= 0.5 for delay in sleeps)
        assert waits == []
    else:
        assert waits == []


def test_empty_tasks_returns_empty_result(monkeypatch, tmp_path):
    result = _run_batch(RecordingPlugin(), monkeypatch, tmp_path, [],
                        name="r1")
    assert result == []


# ---------------------------------------------------------------------------
# Infra retries (B6 policy)
# ---------------------------------------------------------------------------

def test_infra_error_retried_then_success(monkeypatch, tmp_path):
    def infra_once(record):
        if record["attempt_token"] == "a00":
            return RunOutcome(status="infra_error", meta={"error": "sandbox down"})
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=infra_once)
    # Explicit knobs: immune to default changes, and the zero backoff keeps
    # the retry instant (the default backoff sleeps for real between the
    # attempts).
    result = _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"),
                        name="r1", infra_retries=1, infra_backoff=(0.0, 0.0))
    # The retry succeeded, so the unit is present and nothing was recorded.
    assert [t.trajectory_id for t in result] == [_expected_id("t1", 0)]
    assert result[0].status == "success"
    assert [c["attempt_token"] for c in plugin.calls] == ["a00", "a00r1"]
    # Both executions of the unit used the same per-unit workdir.
    assert {str(c["workdir"]) for c in plugin.calls} == {
        str(tmp_path / "r1" / "workdirs"
            / urllib.parse.quote(_expected_id("t1", 0), safe=""))}
    assert UnitFailureRecorder(tmp_path / "r1").list_failures() == []


def test_infra_gives_up_after_the_configured_retries(monkeypatch, tmp_path):
    def t2_always_infra(record):
        if record["task_id"] == "t2":
            return RunOutcome(status="infra_error",
                              meta={"error": "dead endpoint"})
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t2_always_infra)
    tasks = _tasks("t1", "t2")
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        infra_retries=2, infra_backoff=(0.0, 0.0))
    # The healthy unit is stored; the dead one burned its three tokens
    # (a00 plus two retries) and is absent from the result.
    assert [t.trajectory_id for t in result] == [_expected_id("t1", 0)]
    assert [c["attempt_token"] for c in plugin.calls
            if c["task_id"] == "t2"] == ["a00", "a00r1", "a00r2"]
    # Exactly one ledger line for the unit, however many attempts it burned.
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert [f["unit_id"] for f in failures] == [_expected_id("t2", 0)]

    # A resume retries the unit from the next unused token (a00r3).
    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                         infra_retries=2, infra_backoff=(0.0, 0.0))
    assert [c["attempt_token"] for c in plugin.calls] == ["a00r3"]
    assert [t.trajectory_id for t in resumed] == [
        _expected_id("t1", 0), _expected_id("t2", 0)]


def test_backoff_sleeps_one_uniform_draw_before_the_retry(
        monkeypatch, tmp_path):
    def infra_once(record):
        if record["attempt_token"] == "a00":
            return RunOutcome(status="infra_error", meta={"error": "blip"})
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=infra_once)
    module = _real_batch_module()
    draws = []

    def fake_uniform(low, high):
        draws.append((low, high))
        return (low + high) / 2

    monkeypatch.setattr(module, "random",
                        types.SimpleNamespace(uniform=fake_uniform))
    waits = _record_reliability_sleeps(monkeypatch)
    result = _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1",
                        infra_backoff=_BACKOFF_BOUNDS)
    assert [t.trajectory_id for t in result] == [_expected_id("t1", 0)]
    # One draw of random.uniform(*infra_backoff), its value fed to exactly
    # one sleep before the retry -- and the retry ran under the fresh token.
    assert draws == [_BACKOFF_BOUNDS]
    assert waits == [30.0]
    assert 15.0 <= waits[0] <= 45.0
    assert [c["attempt_token"] for c in plugin.calls] == ["a00", "a00r1"]


@pytest.mark.parametrize("knobs, t2_tokens", [
    pytest.param(_PLAIN_KNOBS, ["a00", "a00r1"], id="plain"),
    pytest.param(_FULL_KNOBS, ["a00", "a00r1", "a00r2"],
                 id="stagger_and_backoff"),
])
def test_infra_exhausted_recorded_absent_and_resumed(
        monkeypatch, tmp_path, knobs, t2_tokens):
    sleeps = _record_batch_sleeps(monkeypatch)
    waits = _record_reliability_sleeps(monkeypatch)

    def t2_always_infra(record):
        if record["task_id"] == "t2":
            return RunOutcome(status="infra_error",
                               meta={"error": "sandbox down"})
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t2_always_infra)
    tasks = _tasks("t1", "t2")
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        **knobs)
    # Only the healthy unit came back; the exhausted one is absent.
    assert [t.trajectory_id for t in result] == [_expected_id("t1", 0)]
    # All of t2's attempts ran (initial + every configured retry).
    assert [c["attempt_token"] for c in plugin.calls
            if c["task_id"] == "t2"] == t2_tokens
    # The failure ledger names the unit by its trajectory id.
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert [f["unit_id"] for f in failures] == [_expected_id("t2", 0)]
    assert failures[0]["error_class"] == "InfrastructureError"
    assert "sandbox down" in failures[0]["message"]

    # A resume retries the failed unit: nothing was stored for it, so it is
    # pending again and now completes.
    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                         **knobs)
    assert [c["task_id"] for c in plugin.calls] == ["t2"]
    assert [t.trajectory_id for t in resumed] == [
        _expected_id("t1", 0), _expected_id("t2", 0)]
    assert all(t.status == "success" for t in resumed)
    if "dispatch_stagger" in knobs:
        # The knobs really were live and the resume is unchanged: one
        # backoff wait per retry (a real draw inside the default bounds) and
        # one stagger sleep per started unit (t1 and t2, then t2 again).
        assert len(waits) == len(t2_tokens) - 1
        assert all(_BACKOFF_BOUNDS[0] <= w <= _BACKOFF_BOUNDS[1]
                  for w in waits)
        assert len(sleeps) == 3
    else:
        # Zero backoff keeps the old behaviour: no sleeper call at all.
        assert waits == []


# ---------------------------------------------------------------------------
# Attempt tokens and Task copies
# ---------------------------------------------------------------------------

def test_attempt_token_in_task_meta_and_caller_tasks_unmutated(
        monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    tasks = _tasks("t1")
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
               n_attempts=2)
    assert [c["attempt_token"] for c in plugin.calls] == ["a00", "a01"]
    # The plugin received copies: original meta preserved alongside the token
    # and the unit set's run namespace.
    namespace = plugin.calls[0]["meta"]["run_namespace"]
    assert [c["meta"] for c in plugin.calls] == [
        {"gold": "t1", "attempt_token": "a00", "run_namespace": namespace},
        {"gold": "t1", "attempt_token": "a01", "run_namespace": namespace},
    ]
    # The caller's Task objects were not mutated.
    assert tasks[0].meta == {"gold": "t1"}


# ---------------------------------------------------------------------------
# Skills passthrough and handle wiring
# ---------------------------------------------------------------------------

def test_skills_injected_and_block_passed_through(monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    tasks = _tasks("t1")

    bare = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="bare",
                     skills_injected=None, skills_block="")
    assert all(t.skills_injected is None for t in bare)
    assert all(c["skills_block"] == "" for c in plugin.calls)

    plugin.calls.clear()
    digest = "bank:9f2c1a"
    banked = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="banked",
                       skills_injected=digest, skills_block="- skill one\n")
    assert all(t.skills_injected == digest for t in banked)
    assert all(c["skills_block"] == "- skill one\n" for c in plugin.calls)


def test_working_handle_carries_usage_and_run_dir(monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    handle = make_fake_handle("weak")
    usage = object()
    run_dir = tmp_path / "r1"
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1",
               handle=handle, usage=usage, run_dir=run_dir)
    seen = plugin.calls[0]["handle"]
    # The runner got a replaced handle carrying the accounting objects...
    assert seen is not handle
    assert seen.usage is usage
    assert seen.run_dir == run_dir
    # ...with the caller's model wiring preserved and the original untouched.
    assert (seen.name, seen.role, seen.client) == (handle.name, handle.role,
                                                  handle.client)
    assert handle.usage is None and handle.run_dir is None


# ---------------------------------------------------------------------------
# Ordering and parallelism
# ---------------------------------------------------------------------------

def test_units_sorted_and_output_deterministic(monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    tasks = _tasks("b", "c", "a")  # deliberately unsorted input
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                       n_attempts=2)
    # Execution order: attempt-major -- every task's attempt 0, then every
    # task's attempt 1 -- so one broken task's attempts are never adjacent
    # (workers=1 serialises).
    assert [(c["task_id"], c["attempt_token"]) for c in plugin.calls] == [
        ("a", "a00"), ("b", "a00"), ("c", "a00"),
        ("a", "a01"), ("b", "a01"), ("c", "a01"),
    ]
    # Output: all unit-set trajectories sorted by trajectory_id.
    assert [t.trajectory_id for t in result] == sorted(
        t.trajectory_id for t in result)
    assert [t.trajectory_id for t in result] == [
        _expected_id("a", 0), _expected_id("a", 1),
        _expected_id("b", 0), _expected_id("b", 1),
        _expected_id("c", 0), _expected_id("c", 1),
    ]
    assert all(isinstance(t, Trajectory) for t in result)


def test_parallel_workers_match_serial(monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    tasks = _tasks("t1", "t2", "t3")
    serial = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="serial",
                       n_attempts=2, workers=1)
    plugin.calls.clear()
    threaded = _run_batch(plugin, monkeypatch, tmp_path, tasks,
                          name="threaded", n_attempts=2, workers=2)
    assert [_fingerprint(t) for t in threaded] == \
        [_fingerprint(t) for t in serial]
    assert len(plugin.calls) == 6


# ---------------------------------------------------------------------------
# Run namespace (remote run-id isolation) and the retry cursor
# ---------------------------------------------------------------------------

def _namespaces(plugin):
    return {c["meta"]["run_namespace"] for c in plugin.calls}


def test_run_namespace_is_deterministic_and_shared_by_retries(
        monkeypatch, tmp_path):
    def infra_on_first(record):
        if record["attempt_token"] == "a00":
            return RunOutcome(status="infra_error", meta={"error": "blip"})
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=infra_on_first)
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1", "t2"), name="r1",
               n_attempts=2, infra_retries=1, infra_backoff=(0.0, 0.0))
    first = _namespaces(plugin)
    # One namespace for the whole unit set, retries included.
    assert len(first) == 1
    (namespace,) = first
    assert len(namespace) == 10
    assert all(ch in "0123456789abcdef" for ch in namespace)

    # Same inputs (same store dir, role, model, skills) on a later call ->
    # the same namespace, so a resume recognises its remote runs.
    plugin.calls.clear()
    plugin.behavior = None
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t3"), name="r1")
    assert _namespaces(plugin) == first

    # The store path is resolved: a symlink to the same run dir is the same
    # experiment.
    os.symlink(tmp_path / "r1", tmp_path / "alias")
    plugin.calls.clear()
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t4"), name="alias",
               workdir_root=tmp_path / "r1" / "workdirs")
    assert _namespaces(plugin) == first


@pytest.mark.parametrize("change", [
    {"name": "other_run"},
    {"role": "strong"},
    {"model_name": "weak-14b"},
    {"skills_injected": "bank:9f2c1a", "skills_block": "- skill\n"},
    {"skills_injected": ["skill/a", "skill/b"], "skills_block": "- s\n"},
])
def test_run_namespace_differs_per_arm(monkeypatch, tmp_path, change):
    plugin = RecordingPlugin()
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1")
    base = _namespaces(plugin)
    plugin.calls.clear()
    overrides = dict(change)
    name = overrides.pop("name", "r1")
    if "role" in overrides:
        overrides["handle"] = make_fake_handle(overrides["role"])
    if name == "r1" and "role" not in overrides:
        # Same store and role: the trajectory id is taken, so the other arm
        # would be skipped as done -- exactly why stage 6 uses separate arm
        # stores. Run it on a different task of the same store instead.
        tasks = _tasks("t2")
    else:
        tasks = _tasks("t1")
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name=name, **overrides)
    assert len(plugin.calls) == 1
    assert _namespaces(plugin) != base
    # Same attempt token, different namespace: exactly the stage-1 strong vs
    # weak (or stage-6 arm vs stage-1) shape that used to collide remotely.
    assert {c["attempt_token"] for c in plugin.calls} == {"a00"}


@pytest.mark.parametrize("knobs, first_tokens, resume_token", [
    pytest.param(_PLAIN_KNOBS, ["a00", "a00r1"], "a00r2", id="plain"),
    pytest.param(_FULL_KNOBS, ["a00", "a00r1", "a00r2"], "a00r3",
                 id="stagger_and_backoff"),
])
def test_resume_continues_the_retry_sequence(
        monkeypatch, tmp_path, knobs, first_tokens, resume_token):
    sleeps = _record_batch_sleeps(monkeypatch)
    waits = _record_reliability_sleeps(monkeypatch)

    def always_infra(record):
        return RunOutcome(status="infra_error", meta={"error": "stale run"})

    plugin = RecordingPlugin(behavior=always_infra)
    tasks = _tasks("t1")
    assert _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                      **knobs) == []
    assert [c["attempt_token"] for c in plugin.calls] == first_tokens

    # The tokens are spent (their remote run dirs may hold stale trials the
    # TB plugin refuses to resubmit into), so a resume must use fresh ones
    # instead of failing on the same ones forever.
    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                         **knobs)
    assert [c["attempt_token"] for c in plugin.calls] == [resume_token]
    assert [t.status for t in resumed] == ["success"]
    if "dispatch_stagger" in knobs:
        # One backoff wait per retry (a draw inside the default bounds) and
        # one stagger sleep per started unit (the failed pass, the resume).
        assert len(waits) == len(first_tokens) - 1
        assert all(_BACKOFF_BOUNDS[0] <= w <= _BACKOFF_BOUNDS[1]
                  for w in waits)
        assert len(sleeps) == 2
    else:
        assert waits == []


@pytest.mark.parametrize("knobs", [
    pytest.param(_PLAIN_KNOBS, id="plain"),
    pytest.param(_FULL_KNOBS, id="stagger_and_backoff"),
])
def test_resume_rejoins_the_retry_in_flight(monkeypatch, tmp_path, knobs):
    sleeps = _record_batch_sleeps(monkeypatch)
    waits = _record_reliability_sleeps(monkeypatch)

    def infra_then_crash(record):
        if record["attempt_token"] == "a00":
            return RunOutcome(status="infra_error", meta={"error": "blip"})
        raise RuntimeError("simulated crash while a00r1 was running")

    plugin = RecordingPlugin(behavior=infra_then_crash)
    tasks = _tasks("t1")
    with pytest.raises(ParallelError):
        _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1", **knobs)
    # a00r1 was launched when the process died: the resume asks for the same
    # token again, so the TB plugin can rejoin (or reuse) that remote trial.
    plugin.calls.clear()
    plugin.behavior = None
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1", **knobs)
    assert [c["attempt_token"] for c in plugin.calls] == ["a00r1"]
    if "dispatch_stagger" in knobs:
        # Exactly one backoff wait (a00 failed before the crash) inside the
        # default bounds, and one stagger sleep per started unit.
        assert len(waits) == 1
        assert all(_BACKOFF_BOUNDS[0] <= w <= _BACKOFF_BOUNDS[1]
                  for w in waits)
        assert len(sleeps) == 2
    else:
        assert waits == []


class _NamedTB:
    """Build a real TBPlugin served under the fake benchmark name."""

    @staticmethod
    def make(link):
        from expedistill.benchmarks.tb.plugin import TBPlugin

        class FakebenchTB(TBPlugin):
            name = _FAKE

        return FakebenchTB(link=link, poll_seconds=0.0, deadline_seconds=10.0)


def test_strong_and_weak_arms_never_share_a_remote_result(
        monkeypatch, tmp_path):
    # Stage 1 shape end to end through the real TB plugin: both arms run the
    # same task with the same attempt index into the same store. The strong
    # trial succeeds, the weak trial fails; the weak trajectory must carry
    # the weak verdict, from its own remote run.
    def outcome(run_id, task, model):
        # Harbor names trial dirs <task>__<random>; the strong trial's dir
        # sorts first, so a shared run id would make the weak arm's poll
        # (``ls | head -n 1``) pick up the strong result.
        if model == "strong-70b":
            return trial_files(run_id, task, result="result_success.json",
                               trial="S0000001")
        return trial_files(run_id, task, result="result_failure.json",
                           trial="W0000001")

    link = JobsRemoteLink(on_submit=outcome)
    plugin = _NamedTB.make(link)
    tasks = [Task(task_id="train-fasttext", instruction="train-fasttext",
                  meta={"task_name": "train-fasttext"})]
    strong = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        role="strong", model_name="strong-70b",
                        handle=make_fake_handle("strong", name="strong-70b"))
    weak = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                      role="weak", model_name="weak-7b",
                      handle=make_fake_handle("weak", name="weak-7b"))
    assert [t.status for t in strong] == ["success"]
    assert [t.status for t in weak] == ["failure"]
    assert strong[0].meta["run_id"] != weak[0].meta["run_id"]
    assert [launch["model"] for launch in link.launches] == [
        "strong-70b", "weak-7b"]


def test_run_namespace_covers_the_rendered_skills_block(monkeypatch, tmp_path):
    # Same bank reference, different rendering (a regrouped bank): the remote
    # trial saw a different SKILL.md, so it must not share a run id.
    plugin = RecordingPlugin()
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1",
               skills_injected="bank:9f2c1a", skills_block="- a\n- b\n")
    first = _namespaces(plugin)
    plugin.calls.clear()
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t2"), name="r1",
               skills_injected="bank:9f2c1a", skills_block="## g1\n- a\n- b\n")
    second = _namespaces(plugin)
    assert first != second
    assert all(len(ns) == 10 for ns in first | second)


# ---------------------------------------------------------------------------
# Resume verifies the stored unit was produced under the same inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("change,field", [
    ({"skills_injected": "bank:NEW", "skills_block": "- new\n"},
     "skills_injected"),
    ({"model_name": "weak-14b"}, "model_name"),
])
def test_resume_rejects_a_unit_stored_under_other_inputs(
        monkeypatch, tmp_path, change, field):
    plugin = RecordingPlugin()
    base = {"skills_injected": "bank:OLD", "skills_block": "- old\n"}
    first = _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"),
                       name="r1", **base)
    stored = Store(tmp_path / "r1").path_for("trajectories",
                                             first[0].trajectory_id)
    before = stored.read_bytes()
    plugin.calls.clear()
    with pytest.raises(ReliabilityConfigError) as excinfo:
        _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1",
                   **{**base, **change})
    message = str(excinfo.value)
    assert first[0].trajectory_id in message
    assert field in message
    assert "new run directory" in message
    old_value = getattr(first[0], field)
    new_value = {**base, "model_name": "weak-7b", **change}[field]
    assert repr(old_value) in message and repr(new_value) in message
    # Nothing ran, nothing was overwritten.
    assert plugin.calls == []
    assert stored.read_bytes() == before


def test_resume_accepts_a_unit_stored_under_the_same_inputs(
        monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    kwargs = {"skills_injected": ["skill/a"], "skills_block": "- a\n"}
    first = _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"),
                       name="r1", **kwargs)
    plugin.calls.clear()
    again = _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"),
                       name="r1", **kwargs)
    assert plugin.calls == []
    assert [_fingerprint(t) for t in again] == [_fingerprint(t) for t in first]


# ---------------------------------------------------------------------------
# Config / provider errors stop the batch and surface unwrapped
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error", [ReliabilityConfigError,
                                   ProviderRefusalError])
def test_config_like_error_stops_scheduling_and_is_reraised(
        monkeypatch, tmp_path, error):
    def t2_refused(record):
        if record["task_id"] == "t2":
            raise error("endpoint rejects model weak-7b")
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t2_refused)
    with pytest.raises(error) as excinfo:
        _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1", "t2", "t3",
                                                         "t4"), name="r1")
    assert "endpoint rejects model" in str(excinfo.value)
    assert "1 unit(s) failed" in str(excinfo.value)
    # t3 / t4 were never started; t1 (done before) is stored.
    assert [c["task_id"] for c in plugin.calls] == ["t1", "t2"]
    assert Store(tmp_path / "r1").ids("trajectories") == [_expected_id("t1", 0)]
    # Every gated-off unit is audited exactly once, under the error gate.
    audit = read_jsonl(tmp_path / "r1" / "skip_audit.jsonl")
    assert [row["unit"] for row in audit] == [
        _expected_id("t3", 0), _expected_id("t4", 0)]
    assert {row["gate"] for row in audit} == {"error"}


def test_code_bug_stops_scheduling_and_stays_a_parallel_error(
        monkeypatch, tmp_path):
    def t1_bug(record):
        if record["task_id"] == "t1":
            raise CodeBugError("broken plugin")
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t1_bug)
    with pytest.raises(ParallelError):
        _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1", "t2", "t3"),
                   name="r1")
    assert [c["task_id"] for c in plugin.calls] == ["t1"]
    audit = read_jsonl(tmp_path / "r1" / "skip_audit.jsonl")
    assert [row["unit"] for row in audit] == [
        _expected_id("t2", 0), _expected_id("t3", 0)]
    assert {row["gate"] for row in audit} == {"error"}


def test_config_error_with_workers_stops_new_units(monkeypatch, tmp_path):
    raised = threading.Event()

    def behavior(record):
        if record["task_id"] == "t1":
            raised.set()
            raise ReliabilityConfigError("bad endpoint config")
        if record["task_id"] == "t2":
            # Already running when t1 fails: it finishes and is stored.
            raised.wait(5)
            time.sleep(0.2)
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=behavior)
    with pytest.raises(ReliabilityConfigError, match="bad endpoint config"):
        _run_batch(plugin, monkeypatch, tmp_path,
                   _tasks("t1", "t2", "t3", "t4", "t5"), name="r1", workers=2)
    assert sorted(c["task_id"] for c in plugin.calls) == ["t1", "t2"]
    assert Store(tmp_path / "r1").ids("trajectories") == [_expected_id("t2", 0)]
    audit = read_jsonl(tmp_path / "r1" / "skip_audit.jsonl")
    assert sorted(row["unit"] for row in audit) == sorted(
        _expected_id(t, 0) for t in ("t3", "t4", "t5"))
    assert {row["gate"] for row in audit} == {"error"}


# ---------------------------------------------------------------------------
# A permanently broken task: give-up, never a gate on the others
# ---------------------------------------------------------------------------

def _infra_for(*task_ids):
    def behavior(record):
        if record["task_id"] in task_ids:
            return RunOutcome(status="infra_error",
                              meta={"error": "endpoint timed out"})
        return _success(record["task_id"])
    return behavior


def test_one_broken_task_does_not_stop_the_others(monkeypatch, tmp_path):
    # Reviewer scenario: 4 tasks, N=5, t1 is permanently broken (an image
    # that never builds). That is evidence about t1, not about the batch:
    # every other task must still run all its attempts. With the circuit
    # breaker gone this is the regression guard that a give-up never gates
    # the rest of the unit set.
    plugin = RecordingPlugin(behavior=_infra_for("t1"))
    tasks = _tasks("t1", "t2", "t3", "t4")
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        n_attempts=5, infra_retries=1,
                        infra_backoff=(0.0, 0.0))
    assert len(result) == 15
    assert {t.task_id for t in result} == {
        make_task_id(_FAKE, t) for t in ("t2", "t3", "t4")}
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert sorted(f["unit_id"] for f in failures) == sorted(
        _expected_id("t1", a) for a in range(5))
    assert all(f["error_class"] == "InfrastructureError" for f in failures)
    assert all("endpoint timed out" in f["message"] for f in failures)
    # Infra give-ups gate nothing: no unit was audited out of the batch.
    assert not (tmp_path / "r1" / "skip_audit.jsonl").exists()


def test_untrusted_remote_run_is_retried_under_a_fresh_token(
        monkeypatch, tmp_path):
    # A result.json already sits under this unit's a00 run id, but nothing
    # local says this unit launched it: K1 must get a stale infra_error and
    # run a fresh trial under a00r1, never adopt the foreign result.
    link = JobsRemoteLink(on_submit=lambda run_id, task, model: trial_files(
        run_id, task, result="result_failure.json"))
    plugin = _NamedTB.make(link)
    tasks = [Task(task_id="train-fasttext", instruction="train-fasttext",
                  meta={"task_name": "train-fasttext"})]
    handle = make_fake_handle("weak", name="weak-7b")
    # The namespace depends on the store dir, so plant the foreign result
    # under whatever id the first probe asks about.
    original_check = link._check
    planted = []

    def plant_then_check(cmd):
        if "ABSENT" in cmd and not planted:
            run_id = cmd.split("/jobs/")[1].split("/")[0]
            planted.append(run_id)
            link.files.update(trial_files(run_id, "train-fasttext"))
        return original_check(cmd)

    link._check = plant_then_check
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        handle=handle,
                        infra_retries=1, infra_backoff=(0.0, 0.0))
    assert [t.status for t in result] == ["failure"]
    assert planted[0].endswith("_a00")
    assert result[0].meta["run_id"] == planted[0] + "r1"
    assert len(link.launches) == 1


# ---------------------------------------------------------------------------
# skip_unit_ids: accepted shortfalls are terminal
# ---------------------------------------------------------------------------

def test_skip_unit_ids_are_neither_run_nor_retried(monkeypatch, tmp_path):
    plugin = RecordingPlugin()
    tasks = _tasks("t1", "t2")
    skipped = {_expected_id("t1", 1)}
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        n_attempts=2, skip_unit_ids=skipped)
    assert sorted((c["task_id"], c["attempt_token"]) for c in plugin.calls) \
        == [("t1", "a00"), ("t2", "a00"), ("t2", "a01")]
    assert [t.trajectory_id for t in result] == [
        _expected_id("t1", 0), _expected_id("t2", 0), _expected_id("t2", 1)]
    # Nothing about the skipped unit lands in the retry ledger either.
    assert UnitFailureRecorder(tmp_path / "r1").list_failures() == []
    # A bare string is a programming error (it would skip by character).
    with pytest.raises(CodeBugError, match="skip_unit_ids"):
        _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                   n_attempts=2, skip_unit_ids=_expected_id("t1", 1))


# ---------------------------------------------------------------------------
# Dispatch stagger: spread the units, change nothing
# ---------------------------------------------------------------------------

def test_stagger_does_not_change_the_result(monkeypatch, tmp_path):
    sleeps = _record_batch_sleeps(monkeypatch)
    plugin = RecordingPlugin()
    tasks = _tasks("t1", "t2", "t3")
    plain = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="plain",
                       n_attempts=2)
    n_calls = len(plugin.calls)
    assert sleeps == []  # no stagger configured: nothing slept
    plugin.calls.clear()
    staggered = _run_batch(plugin, monkeypatch, tmp_path, tasks,
                           name="staggered", n_attempts=2,
                           dispatch_stagger=0.5)
    # Same trajectories, and exactly one stagger sleep per executed unit,
    # each a draw inside the configured bound.
    assert [_fingerprint(t) for t in staggered] == \
        [_fingerprint(t) for t in plain]
    assert len(plugin.calls) == n_calls
    assert len(sleeps) == n_calls
    assert all(0 <= delay <= 0.5 for delay in sleeps)


def test_gated_off_units_pay_no_stagger_sleep(monkeypatch, tmp_path):
    sleeps = _record_batch_sleeps(monkeypatch)

    def t1_raises(record):
        if record["task_id"] == "t1":
            raise RuntimeError("simulated crash")
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t1_raises)
    tasks = _tasks("t1", "t2", "t3", "t4")
    with pytest.raises(ParallelError):
        _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                   dispatch_stagger=0.5)
    # The unit that raised slept once (it passed the gate before its
    # stagger); the three gated-off units were audited without sleeping.
    assert len(sleeps) == 1
    audit = read_jsonl(tmp_path / "r1" / "skip_audit.jsonl")
    assert [row["unit"] for row in audit] == [
        _expected_id(t, 0) for t in ("t2", "t3", "t4")]
    assert {row["gate"] for row in audit} == {"error"}


# ---------------------------------------------------------------------------
# Heartbeat and dispatch observability
# ---------------------------------------------------------------------------

def test_heartbeat_snapshots_the_batch_from_disk(
        monkeypatch, tmp_path, capfd):
    module = _real_batch_module()
    monkeypatch.setattr(module, "_HEARTBEAT_SECONDS", 0.03)
    n_units = 40
    disk_done = {}

    def t01_hangs(record):
        if record["task_id"] == "t01":
            # Serial dispatch: exactly the units before this one are stored
            # for the whole hang, and those are the numbers every heartbeat
            # line printed during it must report.
            disk_done["count"] = len(
                Store(tmp_path / "r1").ids("trajectories"))
            time.sleep(0.4)
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t01_hangs)
    baseline_threads = threading.active_count()
    result = _run_batch(plugin, monkeypatch, tmp_path,
                        _tasks(*(f"t{i:02d}" for i in range(n_units))),
                        name="r1")
    assert len(result) == n_units
    # The heartbeat thread is gone once the call returns.
    for _ in range(400):
        if threading.active_count() <= baseline_threads:
            break
        time.sleep(0.005)
    assert threading.active_count() <= baseline_threads
    err = capfd.readouterr().err
    beats = [line for line in err.splitlines()
             if line.startswith("K1 heartbeat[weak]:")]
    assert len(beats) >= 2
    # Periodic, not per-unit: a handful of lines for forty units.
    assert len(beats) < n_units
    parsed = []
    for line in beats:
        fields = dict(part.split("=", 1)
                      for part in line.split(": ", 1)[1].split()
                      if "=" in part)
        parsed.append({key: int(fields[key])
                       for key in ("done", "failed", "skipped", "in_flight")})
    for row in parsed:
        # A disk snapshot: the four dispositions cover the pending set...
        assert sum(row.values()) == n_units
        # ...and in this clean run nothing failed and nothing was gated.
        assert row["failed"] == 0 and row["skipped"] == 0
    # During the hang the snapshot matched the store: every unit before the
    # hanging one done, the rest (the hanging unit included) in flight.
    assert any(row["done"] == disk_done["count"]
               and row["in_flight"] == n_units - disk_done["count"]
               for row in parsed)
    # And once the call is over the heartbeat stops printing.
    time.sleep(0.12)
    assert "K1 heartbeat[" not in capfd.readouterr().err


def test_dispatch_line_reports_the_knobs(monkeypatch, tmp_path, capfd):
    sleeps = _record_batch_sleeps(monkeypatch)
    plugin = RecordingPlugin()
    tasks = _tasks("t1", "t2")
    knobs = dict(workers=3, dispatch_stagger=2.5,
                 infra_retries=1, infra_backoff=(15.0, 45.0))
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1", **knobs)
    err = capfd.readouterr().err
    assert ("K1 dispatch[weak]: 2 pending units "
            "(0 skipped, 2 total), workers=3, stagger=2.5s, "
            "backoff=15.0-45.0s") in err
    assert "breaker" not in err
    # A nothing-to-do resume still says so: 0 pending prints too.
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1", **knobs)
    assert ("K1 dispatch[weak]: 0 pending units "
            "(0 skipped, 2 total)") in capfd.readouterr().err
    # The skipped count is the real intersection with this call's units.
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1", **knobs,
               skip_unit_ids={_expected_id("t1", 0)})
    assert ("K1 dispatch[weak]: 0 pending units "
            "(1 skipped, 2 total)") in capfd.readouterr().err
    assert len(sleeps) == 2  # only the first call had units to run


# ---------------------------------------------------------------------------
# Timing settings are validated up front
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("overrides", [
    pytest.param({"infra_retries": -1}, id="negative_retries"),
    pytest.param({"infra_backoff": (45.0, 15.0)}, id="backoff_inverted"),
    pytest.param({"infra_backoff": (-1.0, 5.0)}, id="backoff_negative"),
    pytest.param({"infra_backoff": (float("nan"), 5.0)}, id="backoff_nan"),
    pytest.param({"dispatch_stagger": -0.1}, id="stagger_negative"),
    pytest.param({"dispatch_stagger": float("nan")}, id="stagger_nan"),
])
def test_bad_timing_settings_are_rejected_up_front(
        monkeypatch, tmp_path, overrides):
    plugin = RecordingPlugin()
    with pytest.raises(ValueError):
        _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1",
                   **overrides)
    # Rejected before any unit ran.
    assert plugin.calls == []
