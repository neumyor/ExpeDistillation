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
from expedistill.infra.store import Store
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


def test_interrupted_run_completes_only_the_missing_unit(
        monkeypatch, tmp_path):
    def fail_on_t3(record):
        if record["task_id"] == "t3":
            raise RuntimeError("simulated crash mid-run")
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=fail_on_t3)
    tasks = _tasks("t1", "t2", "t3")
    with pytest.raises(ParallelError):
        _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    # The two units that finished before the crash are safely stored.
    store = Store(tmp_path / "r1")
    assert store.ids("trajectories") == [_expected_id("t1", 0),
                                         _expected_id("t2", 0)]

    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    # Only the missing unit was executed again.
    assert [c["task_id"] for c in plugin.calls] == ["t3"]
    assert len(resumed) == 3

    # Same final state as an uninterrupted run over the same unit set.
    plugin.calls.clear()
    whole = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r2")
    assert [_fingerprint(t) for t in resumed] == \
        [_fingerprint(t) for t in whole]


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
    result = _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"),
                        name="r1")
    # The retry succeeded, so the unit is present and nothing was recorded.
    assert [t.trajectory_id for t in result] == [_expected_id("t1", 0)]
    assert result[0].status == "success"
    assert [c["attempt_token"] for c in plugin.calls] == ["a00", "a00r1"]
    # Both executions of the unit used the same per-unit workdir.
    assert {str(c["workdir"]) for c in plugin.calls} == {
        str(tmp_path / "r1" / "workdirs"
            / urllib.parse.quote(_expected_id("t1", 0), safe=""))}
    assert UnitFailureRecorder(tmp_path / "r1").list_failures() == []


def test_infra_exhausted_recorded_absent_and_resumed(monkeypatch, tmp_path):
    def t2_always_infra(record):
        if record["task_id"] == "t2":
            return RunOutcome(status="infra_error",
                               meta={"error": "sandbox down"})
        return _success(record["task_id"])

    plugin = RecordingPlugin(behavior=t2_always_infra)
    tasks = _tasks("t1", "t2")
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    # Only the healthy unit came back; the exhausted one is absent.
    assert [t.trajectory_id for t in result] == [_expected_id("t1", 0)]
    # Both of t2's executions ran (initial + one infra retry).
    t2_tokens = [c["attempt_token"] for c in plugin.calls
                 if c["task_id"] == "t2"]
    assert t2_tokens == ["a00", "a00r1"]
    # The failure ledger names the unit by its trajectory id.
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert [f["unit_id"] for f in failures] == [_expected_id("t2", 0)]
    assert failures[0]["error_class"] == "InfrastructureError"
    assert "sandbox down" in failures[0]["message"]

    # A resume retries the failed unit: nothing was stored for it, so it is
    # pending again and now completes.
    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    assert [c["task_id"] for c in plugin.calls] == ["t2"]
    assert [t.trajectory_id for t in resumed] == [
        _expected_id("t1", 0), _expected_id("t2", 0)]
    assert all(t.status == "success" for t in resumed)


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
               n_attempts=2)
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


def test_resume_continues_the_retry_sequence(monkeypatch, tmp_path):
    def always_infra(record):
        return RunOutcome(status="infra_error", meta={"error": "stale run"})

    plugin = RecordingPlugin(behavior=always_infra)
    tasks = _tasks("t1")
    assert _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1") == []
    assert [c["attempt_token"] for c in plugin.calls] == ["a00", "a00r1"]

    # The tokens a00 / a00r1 are spent (their remote run dirs may hold stale
    # trials the TB plugin refuses to resubmit into), so a resume must use
    # fresh ones instead of failing on the same two forever.
    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    assert [c["attempt_token"] for c in plugin.calls] == ["a00r2"]
    assert [t.status for t in resumed] == ["success"]


def test_resume_rejoins_the_retry_in_flight(monkeypatch, tmp_path):
    def infra_then_crash(record):
        if record["attempt_token"] == "a00":
            return RunOutcome(status="infra_error", meta={"error": "blip"})
        raise RuntimeError("simulated crash while a00r1 was running")

    plugin = RecordingPlugin(behavior=infra_then_crash)
    tasks = _tasks("t1")
    with pytest.raises(ParallelError):
        _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    # a00r1 was launched when the process died: the resume asks for the same
    # token again, so the TB plugin can rejoin (or reuse) that remote trial.
    plugin.calls.clear()
    plugin.behavior = None
    _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1")
    assert [c["attempt_token"] for c in plugin.calls] == ["a00r1"]


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


# ---------------------------------------------------------------------------
# Circuit breaker for a dead endpoint
# ---------------------------------------------------------------------------

def _infra_for(*task_ids):
    def behavior(record):
        if record["task_id"] in task_ids:
            return RunOutcome(status="infra_error",
                              meta={"error": "endpoint timed out"})
        return _success(record["task_id"])
    return behavior


def test_breaker_opens_after_a_streak_of_distinct_tasks(monkeypatch, tmp_path):
    plugin = RecordingPlugin(behavior=_infra_for("t1", "t2", "t3", "t4", "t5"))
    result = _run_batch(plugin, monkeypatch, tmp_path,
                        _tasks("t1", "t2", "t3", "t4", "t5"), name="r1",
                        infra_breaker=2)
    assert result == []
    # Two units exhausted their retries (2 executions each); the rest were
    # never attempted.
    assert [c["task_id"] for c in plugin.calls] == ["t1", "t1", "t2", "t2"]
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert [f["unit_id"] for f in failures] == [
        _expected_id(t, 0) for t in ("t1", "t2", "t3", "t4", "t5")]
    assert all("circuit open" not in f["message"] for f in failures[:2])
    assert all("circuit open" in f["message"] for f in failures[2:])

    # The skipped units are pending, not done: a resume attempts them.
    plugin.calls.clear()
    plugin.behavior = None
    resumed = _run_batch(plugin, monkeypatch, tmp_path,
                         _tasks("t1", "t2", "t3", "t4", "t5"), name="r1",
                         infra_breaker=2)
    assert len(resumed) == 5


def test_breaker_streak_is_reset_by_any_success(monkeypatch, tmp_path):
    plugin = RecordingPlugin(behavior=_infra_for("t1", "t3", "t4", "t6"))
    result = _run_batch(plugin, monkeypatch, tmp_path,
                        _tasks("t1", "t2", "t3", "t4", "t5", "t6"),
                        name="r1", infra_breaker=2)
    # t1 fails, t2 succeeds (reset), t3 + t4 fail -> open; t5, t6 skipped.
    assert [t.trajectory_id for t in result] == [_expected_id("t2", 0)]
    assert [c["task_id"] for c in plugin.calls if c["task_id"] != "t2"] == [
        "t1", "t1", "t3", "t3", "t4", "t4"]
    assert "t5" not in {c["task_id"] for c in plugin.calls}


def test_breaker_zero_disables(monkeypatch, tmp_path):
    plugin = RecordingPlugin(behavior=_infra_for("t1", "t2", "t3"))
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1", "t2", "t3"),
               name="r1", infra_breaker=0)
    assert len(plugin.calls) == 6
    with pytest.raises(ValueError, match="infra_breaker"):
        _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r2",
                   infra_breaker=-1)


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
                        handle=handle)
    assert [t.status for t in result] == ["failure"]
    assert planted[0].endswith("_a00")
    assert result[0].meta["run_id"] == planted[0] + "r1"
    assert len(link.launches) == 1


def test_one_broken_task_never_trips_the_breaker(monkeypatch, tmp_path):
    # Reviewer scenario: 4 tasks, N=5, t1 is permanently broken (an image
    # that never builds). That is evidence about t1, not about the endpoint:
    # every other task must still run all its attempts.
    plugin = RecordingPlugin(behavior=_infra_for("t1"))
    tasks = _tasks("t1", "t2", "t3", "t4")
    result = _run_batch(plugin, monkeypatch, tmp_path, tasks, name="r1",
                        n_attempts=5, infra_breaker=5)
    assert len(result) == 15
    assert {t.task_id for t in result} == {
        make_task_id(_FAKE, t) for t in ("t2", "t3", "t4")}
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert sorted(f["unit_id"] for f in failures) == sorted(
        _expected_id("t1", a) for a in range(5))
    assert not any("circuit open" in f["message"] for f in failures)


def test_breaker_counts_distinct_tasks_only(monkeypatch, tmp_path):
    # Even with a tiny breaker, the same task failing over and over is one
    # task's worth of evidence -- and with a single pending task there can
    # be no cross-task evidence at all, so the breaker stays off.
    plugin = RecordingPlugin(behavior=_infra_for("t1"))
    _run_batch(plugin, monkeypatch, tmp_path, _tasks("t1"), name="r1",
               n_attempts=5, infra_breaker=2)
    assert len(plugin.calls) == 10  # 5 units x (1 + 1 infra retry)
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert not any("circuit open" in f["message"] for f in failures)


def test_dead_endpoint_trips_after_distinct_tasks(monkeypatch, tmp_path):
    ids_ = ("t1", "t2", "t3", "t4", "t5", "t6")
    plugin = RecordingPlugin(behavior=_infra_for(*ids_))
    result = _run_batch(plugin, monkeypatch, tmp_path, _tasks(*ids_),
                        name="r1", n_attempts=2, infra_breaker=3)
    assert result == []
    # Attempt 0 of t1, t2, t3 exhaust their retries -> 3 distinct tasks ->
    # open; the other 9 units are recorded, not attempted.
    assert [c["task_id"] for c in plugin.calls] == [
        "t1", "t1", "t2", "t2", "t3", "t3"]
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert len(failures) == 12
    assert sum("circuit open" in f["message"] for f in failures) == 9


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


def test_breaker_threshold_shrinks_to_small_task_sets(monkeypatch, tmp_path):
    # A 3-task pilot on a dead endpoint: infra_breaker=5 could never be
    # reached, so the effective threshold is the 3 distinct pending tasks.
    ids_ = ("t1", "t2", "t3")
    plugin = RecordingPlugin(behavior=_infra_for(*ids_))
    result = _run_batch(plugin, monkeypatch, tmp_path, _tasks(*ids_),
                        name="r1", n_attempts=2, infra_breaker=5)
    assert result == []
    assert [c["task_id"] for c in plugin.calls] == [
        "t1", "t1", "t2", "t2", "t3", "t3"]
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert sum("circuit open" in f["message"] for f in failures) == 3


def test_small_task_set_with_one_broken_task_keeps_running(
        monkeypatch, tmp_path):
    plugin = RecordingPlugin(behavior=_infra_for("t1"))
    result = _run_batch(plugin, monkeypatch, tmp_path,
                        _tasks("t1", "t2", "t3"), name="r1", n_attempts=3,
                        infra_breaker=5)
    assert len(result) == 6  # every attempt of t2 and t3
    failures = UnitFailureRecorder(tmp_path / "r1").list_failures()
    assert not any("circuit open" in f["message"] for f in failures)
