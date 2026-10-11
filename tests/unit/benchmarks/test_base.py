"""Tests for benchmarks/base.py: Task/RunOutcome dataclasses, ViewRules,
BaseEnv contract, the environment_call deadline wrapper, and the
BenchmarkPlugin abstract interface. No network, no real benchmark."""

import threading
import time

import pytest

from expedistill.benchmarks.base import (
    ENV_TIMEOUT_ENV,
    BaseEnv,
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
    environment_call,
)
from expedistill.infra.reliability import CodeBugError, ConfigError, InfrastructureError
from expedistill.llm import ModelHandle


# ---------------------------------------------------------------------------
# Task / RunOutcome
# ---------------------------------------------------------------------------

def test_task_defaults():
    task = Task(task_id="t-1", instruction="do the thing")
    assert task.meta == {}


def test_runoutcome_defaults_and_valid_statuses():
    outcome = RunOutcome()
    assert outcome.steps == []
    assert outcome.status == "failure"
    assert outcome.meta == {}
    for status in ("success", "failure", "infra_error"):
        assert RunOutcome(status=status).status == status


def test_runoutcome_rejects_unknown_status():
    with pytest.raises(CodeBugError):
        RunOutcome(status="crashed")


def test_runoutcome_infra_error_keeps_partial_steps():
    error = InfrastructureError("sandbox died")
    outcome = RunOutcome.infra_error(
        [{"action": "a", "observation": "o"}], error)
    assert outcome.status == "infra_error"
    assert outcome.steps == [{"action": "a", "observation": "o"}]
    assert outcome.meta["error"] == "InfrastructureError: sandbox died"


def test_runoutcome_infra_error_without_error():
    outcome = RunOutcome.infra_error()
    assert outcome.steps == []
    assert outcome.meta == {}


# ---------------------------------------------------------------------------
# ViewRules
# ---------------------------------------------------------------------------

def test_view_rules_short_observation_passes_through():
    rules = ViewRules()
    assert rules.truncate("short observation") == "short observation"


def test_view_rules_truncation_marks_and_counts():
    rules = ViewRules(max_obs_chars=20, head_keep=5, tail_keep=5)
    out = rules.truncate("x" * 50)
    assert out == "xxxxx\n[... 40 characters truncated ...]\nxxxxx"


def test_view_rules_marker_without_placeholder_is_used_verbatim():
    rules = ViewRules(max_obs_chars=10, head_keep=2, tail_keep=2,
                      truncation_marker="[cut]")
    assert rules.truncate("0123456789ABCDEF") == "01\n[cut]\nEF"


def test_view_rules_rejects_bad_config():
    with pytest.raises(ConfigError):
        ViewRules(max_obs_chars=0)
    with pytest.raises(ConfigError):
        ViewRules(max_obs_chars=10, head_keep=-1)
    # No room left for the marker inside the budget.
    with pytest.raises(ConfigError):
        ViewRules(max_obs_chars=10, head_keep=5, tail_keep=5)


# ---------------------------------------------------------------------------
# BaseEnv
# ---------------------------------------------------------------------------

class _CountingEnv(BaseEnv):
    """Minimal concrete env exercising the inherited default flags."""

    def reset(self):
        self.terminated = False
        self.curr_step = 0
        self.max_steps = 2

    def step(self, action):
        self.curr_step += 1
        if action == "stop":
            self.terminated = True
        return f"obs {self.curr_step}"

    def success_fn(self):
        return True


def test_baseenv_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BaseEnv()  # pylint: disable=abstract-class-instantiated


def test_baseenv_default_flags():
    env = _CountingEnv()
    env.reset()
    assert not env.is_terminated()
    assert not env.is_truncated()
    env.step("walk")
    assert not env.is_terminated()
    assert not env.is_truncated()  # curr_step == 1 is still inside budget 2
    env.step("walk")
    assert not env.is_truncated()  # curr_step == 2, still not past budget 2
    env.step("walk")
    assert env.is_truncated()  # curr_step > max_steps
    env.step("stop")
    assert env.is_terminated()


def test_baseenv_defaults_without_state_attributes():
    class _BareEnv(BaseEnv):
        def reset(self):
            pass

        def step(self, action):
            return "ok"

        def success_fn(self):
            return False

    env = _BareEnv()
    env.reset()
    assert not env.is_terminated()
    assert not env.is_truncated()


# ---------------------------------------------------------------------------
# environment_call deadline wrapper
# ---------------------------------------------------------------------------

def test_environment_call_timeout_raises_infra(monkeypatch):
    monkeypatch.setenv(ENV_TIMEOUT_ENV, "0.05")

    class _Env(BaseEnv):
        def reset(self):
            pass

        @environment_call
        def step(self, action):
            time.sleep(0.3)
            return "too late"

        def success_fn(self):
            return False

    with pytest.raises(InfrastructureError, match="step exceeded 0.05s"):
        _Env().step("go")


def test_environment_call_explicit_seconds_beats_env_var(monkeypatch):
    monkeypatch.setenv(ENV_TIMEOUT_ENV, "120")

    @environment_call(seconds=0.05)
    def slow():
        time.sleep(0.3)

    with pytest.raises(InfrastructureError):
        slow()


def test_environment_call_bad_env_value_is_config_error(monkeypatch):
    monkeypatch.setenv(ENV_TIMEOUT_ENV, "soon")

    @environment_call
    def fast():
        return "fine"

    with pytest.raises(ConfigError):
        fast()


def test_environment_call_nonpositive_value_is_config_error(monkeypatch):
    monkeypatch.setenv(ENV_TIMEOUT_ENV, "0")

    @environment_call
    def fast():
        return "fine"

    with pytest.raises(ConfigError):
        fast()


def test_environment_call_translates_pipe_errors():
    @environment_call(seconds=5)
    def dies():
        raise BrokenPipeError("pipe gone")

    with pytest.raises(InfrastructureError, match="lost its subprocess"):
        dies()


def test_environment_call_requires_main_thread():
    @environment_call(seconds=5)
    def anything():
        return 1

    failures = []

    def worker():
        try:
            anything()
        except RuntimeError as exc:
            failures.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert len(failures) == 1
    assert "main thread" in str(failures[0])


# ---------------------------------------------------------------------------
# BenchmarkPlugin interface
# ---------------------------------------------------------------------------

class _ToyPlugin(BenchmarkPlugin):
    """Minimal concrete plugin used by the registry tests too."""

    def __init__(self, name="toy"):
        self._name = name

    @property
    def name(self):
        return self._name

    def load_tasks(self, tasks_spec):
        return [Task(task_id="t-1", instruction="do it")]

    def run(self, task, handle, skills_block, workdir):
        return RunOutcome(status="success")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "TOY_TASK"


def test_benchmarkplugin_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BenchmarkPlugin()  # pylint: disable=abstract-class-instantiated


def test_toy_plugin_satisfies_interface():
    plugin = _ToyPlugin()
    tasks = plugin.load_tasks(None)
    assert tasks[0].task_id == "t-1"
    outcome = plugin.run(tasks[0], ModelHandle(name="m", role="r", client=None),
                         "", "/tmp/nowhere")
    assert outcome.status == "success"
    assert plugin.view_rules.max_obs_chars == ViewRules().max_obs_chars
    assert plugin.task_prompt_name == "TOY_TASK"
