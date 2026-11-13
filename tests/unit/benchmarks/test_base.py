"""Tests for benchmarks/base.py: Task/RunOutcome dataclasses, ViewRules,
BaseEnv contract and the BenchmarkPlugin abstract interface. No network,
no real benchmark."""


import pytest

from expedistill.benchmarks.base import (
    BaseEnv,
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.llm import ModelHandle


# ---------------------------------------------------------------------------
# Task / RunOutcome
# ---------------------------------------------------------------------------

def test_task_defaults():
    task = Task(task_id="t-1", instruction="do the thing")
    assert task.meta == {}


def test_runoutcome_defaults_and_correctness_values():
    outcome = RunOutcome()
    assert outcome.steps == []
    assert outcome.correctness is None
    assert outcome.meta == {}
    for correctness in (True, False, None):
        assert RunOutcome(correctness=correctness).correctness \
            is correctness


def test_runoutcome_rejects_a_non_boolean_correctness():
    with pytest.raises(CodeBugError):
        RunOutcome(correctness="crashed")


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
        return RunOutcome(correctness=True)

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
    assert outcome.correctness is True
    assert plugin.view_rules.max_obs_chars == ViewRules().max_obs_chars
    assert plugin.task_prompt_name == "TOY_TASK"


def test_plugin_defaults_declare_no_auxiliary_metric():
    # A plugin that does nothing declares no auxiliary metric: the name is
    # None (what stage 0 freezes as manifest "aux_metric": null) and the
    # scorer answers None, so stages never call it and no extra work
    # happens (benchmarks/base.py contract).
    plugin = _ToyPlugin()
    assert plugin.aux_metric_name is None
    from expedistill.core.models import Trajectory
    traj = Trajectory(trajectory_id="traj/t:1/weak/00", task_id="t:1",
                      benchmark="toy", model_role="weak", model_name="m",
                      attempt_index=0)
    assert plugin.aux_success(traj) is None
