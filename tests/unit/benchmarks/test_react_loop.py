"""Tests for benchmarks/react_loop.py: success path, step-budget exhaustion,
infra failures mid-run (raised, K1's to retry), the unscored
model-output-error episode, history truncation, and the bare-vs-skills
prompt rule. Uses FakeClient + toy BaseEnv; no real benchmark, no
network."""

import pytest

from expedistill.benchmarks.base import BaseEnv, Task, ViewRules
from expedistill.benchmarks.react_loop import render_history, run_react
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.llm import make_fake_handle
from expedistill.prompts import PROMPTS, PromptSpec

# The toy task prompt mirrors the contract local plugins' templates follow:
# {instruction}, {skills_block} (may render empty) and {history}.
TOY_TASK_TEMPLATE = (
    "TASK: {instruction}\n"
    "SKILLS:\n{skills_block}\n"
    "HISTORY:\n{history}\n"
    "Reply with your next action."
)


@pytest.fixture(autouse=True)
def toy_prompt(monkeypatch):
    """Register the toy task prompt for the duration of each test only.

    ``monkeypatch.setitem`` removes the entry at teardown, keeping the global
    B2 registry clean: tests/unit/prompts asserts the registry holds exactly
    the method prompts when no plugin package has been imported.
    """
    spec = PromptSpec(
        name="TOY_REACT_TASK",
        template=TOY_TASK_TEMPLATE,
        description="Test-only task prompt for the react_loop unit tests.",
    )
    monkeypatch.setitem(PROMPTS, "TOY_REACT_TASK", spec)
    return "TOY_REACT_TASK"


class _ToyEnv(BaseEnv):
    """Env that succeeds on a specific action, with optional scripted hooks."""

    def __init__(self, success_action="finish", fail_on_step=None,
                 raise_on_step=None, truncate_after=None):
        self.success_action = success_action
        self.fail_on_step = fail_on_step      # terminate with failure here
        self.raise_on_step = raise_on_step    # raise InfrastructureError here
        self.truncate_after = truncate_after  # report is_truncated here
        self.reset()

    def reset(self):
        self.terminated = False
        self.truncated = False
        self.step_count = 0

    def step(self, action):
        self.step_count += 1
        if self.raise_on_step == self.step_count:
            raise InfrastructureError(f"sandbox died at step {self.step_count}")
        if self.fail_on_step == self.step_count:
            self.terminated = True
            return "Answer is INCORRECT."
        if self.truncate_after == self.step_count:
            self.truncated = True
            return "Max steps reached."
        if action == self.success_action:
            self.terminated = True
            return "Task is SOLVED."
        return f"you did: {action}"

    def success_fn(self):
        return self.terminated and self.fail_on_step is None

    def is_truncated(self):
        return self.truncated


def _run(env, script, **kwargs):
    handle = make_fake_handle(role="test", script=list(script))
    outcome = run_react(
        Task(task_id="t-1", instruction="solve the puzzle"),
        env,
        handle,
        task_prompt_name="TOY_REACT_TASK",
        max_steps=kwargs.pop("max_steps", 5),
        **kwargs,
    )
    return outcome, handle


# ---------------------------------------------------------------------------
# Success and step flow
# ---------------------------------------------------------------------------

def test_success_path_records_steps_and_verdict():
    outcome, handle = _run(_ToyEnv(), ["finish"])
    assert outcome.correctness is True
    assert outcome.meta == {"termination": "terminated", "steps_used": 1}
    assert outcome.steps == [
        {"action": "finish", "observation": "Task is SOLVED."}]
    assert len(handle.client.calls) == 1
    user = handle.client.calls[0]["user"]
    assert user.startswith("TASK: solve the puzzle\nSKILLS:\n\nHISTORY:\n\n")


def test_history_accumulates_action_observation_pairs():
    outcome, handle = _run(_ToyEnv(), ["look", "finish"])
    assert outcome.correctness is True
    assert outcome.steps[0]["action"] == "look"
    first_user = handle.client.calls[0]["user"]
    second_user = handle.client.calls[1]["user"]
    # The first call has no history; the second sees step 1 rendered.
    assert "action: look" not in first_user
    assert "action: look\nobservation: you did: look" in second_user


def test_terminated_with_wrong_answer_scores_false():
    env = _ToyEnv(fail_on_step=2)
    outcome, _ = _run(env, ["search", "finish"])
    assert outcome.correctness is False
    assert outcome.meta["termination"] == "terminated"
    assert outcome.steps[-1]["observation"] == "Answer is INCORRECT."


def test_truncated_env_scores_false_not_true():
    env = _ToyEnv(truncate_after=2)
    outcome, _ = _run(env, ["a", "b"])
    assert outcome.correctness is False
    assert outcome.meta["termination"] == "truncated"
    assert len(outcome.steps) == 2


def test_max_steps_exhaustion_scores_false():
    env = _ToyEnv()
    outcome, _ = _run(env, ["a", "b", "c"], max_steps=3)
    assert outcome.correctness is False
    assert outcome.meta == {"termination": "step_budget", "steps_used": 3}
    assert [step["action"] for step in outcome.steps] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Infra failures
# ---------------------------------------------------------------------------

def test_env_infra_error_mid_run_propagates():
    # R2b: infrastructure failures travel as exceptions (K1 retries the
    # unit); nothing is stored for the failed attempt.
    env = _ToyEnv(raise_on_step=2)
    with pytest.raises(InfrastructureError, match="sandbox died at step 2"):
        _run(env, ["ok", "boom"])


def test_model_call_infra_error_propagates():
    with pytest.raises(InfrastructureError, match="endpoint down"):
        _run(_ToyEnv(), [InfrastructureError("endpoint down")])


def test_env_reset_infra_error_propagates():
    class _BrokenReset(_ToyEnv):
        def __init__(self):
            pass  # skip the constructor reset; reset() itself is broken

        def reset(self):
            raise InfrastructureError("sandbox never started")

    with pytest.raises(InfrastructureError, match="sandbox never started"):
        _run(_BrokenReset(), ["finish"])


def test_code_bugs_still_raise():
    class _BuggyEnv(_ToyEnv):
        def step(self, action):
            raise ValueError("plugin bug")

    with pytest.raises(ValueError):
        _run(_BuggyEnv(), ["finish"])


# ---------------------------------------------------------------------------
# Model output errors: an unscored episode, not infra, not a bug
# ---------------------------------------------------------------------------

def test_model_output_error_mid_episode_is_unscored_with_partial_steps():
    # OpenAICompatClient raises ModelOutputError for an empty reply, and
    # call_model does not retry it; it used to escape run_react and stop the
    # whole pipeline as a code bug. R2b: execution no longer reclassifies it
    # as the model's failure -- the episode ends with no scorer verdict
    # (correctness None) and acceptance judges the run.
    outcome, handle = _run(
        _ToyEnv(),
        ["look", "search", ModelOutputError("model m returned no content")])
    assert outcome.correctness is None
    assert [step["action"] for step in outcome.steps] == ["look", "search"]
    assert outcome.meta["termination"] == "model_output_error"
    assert outcome.meta["steps_used"] == 2
    assert "ModelOutputError" in outcome.meta["error"]
    assert "returned no content" in outcome.meta["error"]
    assert len(handle.client.calls) == 3


def test_model_output_error_on_first_step_is_unscored_with_no_steps():
    outcome, _ = _run(_ToyEnv(), [ModelOutputError("no content")])
    assert outcome.correctness is None
    assert outcome.steps == []
    assert outcome.meta["termination"] == "model_output_error"


def test_provider_refusal_still_propagates():
    # A refused request (bad key, unknown model) stops the phase; it is
    # neither the model's failure on this task nor retryable.
    with pytest.raises(ProviderRefusalError):
        _run(_ToyEnv(), ["look", ProviderRefusalError("status=401")])


def test_k1_stores_the_unscored_unit_and_continues(monkeypatch, tmp_path):
    import importlib.abc
    import importlib.machinery
    import sys
    import types

    from expedistill.benchmarks.base import BenchmarkPlugin
    from expedistill.benchmarks.registry import _reset_registry, register_plugin
    from expedistill.composites.batch_execute import batch_execute
    from expedistill.core.models import Trajectory
    from expedistill.infra.store import Store

    name = "reactbench"
    fullname = f"expedistill.benchmarks.{name}"

    class ReactPlugin(BenchmarkPlugin):
        name = "reactbench"
        view_rules = ViewRules()
        task_prompt_name = "TOY_REACT_TASK"

        def load_tasks(self, tasks_spec):
            return []

        def run(self, task, handle, skills_block, workdir):
            return run_react(task, _ToyEnv(), handle,
                             task_prompt_name="TOY_REACT_TASK",
                             skills_block=skills_block, max_steps=3)

    plugin = ReactPlugin()

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(plugin)

    class Finder:
        def find_spec(self, wanted, path=None, target=None):
            if wanted == fullname:
                return importlib.machinery.ModuleSpec(wanted, Loader())
            return None

    _reset_registry()
    monkeypatch.delitem(sys.modules, fullname, raising=False)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])

    def reply(system, user):
        if "TASK: empty" in user:
            if "action: look" in user:
                raise ModelOutputError("model m returned no content")
            return "look"
        return "finish"

    store = Store(tmp_path / "run")
    try:
        trajectories = batch_execute(
            store=store, benchmark=name,
            tasks=[Task(task_id="empty", instruction="empty"),
                   Task(task_id="fine", instruction="fine")],
            role="weak", model_name="m",
            handle=make_fake_handle("weak", script=reply),
            n_attempts=1, workdir_root=tmp_path / "work")
    finally:
        _reset_registry()
    by_task = {t.task_id.rsplit(":", 1)[-1]: t for t in trajectories}
    assert by_task["empty"].correctness is None
    assert by_task["empty"].meta["termination"] == "model_output_error"
    assert [s.action for s in by_task["empty"].steps] == ["look"]
    assert by_task["fine"].correctness is True
    for traj in trajectories:
        stored = store.load("trajectories", traj.trajectory_id, Trajectory)
        assert stored.correctness == traj.correctness


# ---------------------------------------------------------------------------
# Prompt variables and the bare-vs-skills rule
# ---------------------------------------------------------------------------

def test_skills_block_bare_and_with_use_same_template():
    env_bare = _ToyEnv()
    env_skills = _ToyEnv()
    skills_block = "SKILL: always finish early."
    outcome_bare, handle_bare = _run(env_bare, ["finish"], skills_block="")
    outcome_skills, handle_skills = _run(env_skills, ["finish"],
                                         skills_block=skills_block)
    assert outcome_bare.correctness is True
    assert outcome_skills.correctness is True
    bare_user = handle_bare.client.calls[0]["user"]
    skills_user = handle_skills.client.calls[0]["user"]
    # The bare run is exactly the template with an empty skills slot; the
    # skills run differs only by the injected block.
    expected_bare = TOY_TASK_TEMPLATE.format(
        instruction="solve the puzzle", skills_block="", history="")
    assert bare_user == expected_bare
    assert skills_block in skills_user
    assert skills_user == expected_bare.replace(
        "SKILLS:\n\n", f"SKILLS:\n{skills_block}\n")


def test_system_override_and_run_dir_passthrough(tmp_path):
    handle = make_fake_handle(role="test", script=["finish"])
    run_react(
        Task(task_id="t", instruction="i"),
        _ToyEnv(),
        handle,
        task_prompt_name="TOY_REACT_TASK",
        max_steps=2,
        system_override="You are a toy agent.",
        run_dir=tmp_path,
    )
    call = handle.client.calls[0]
    assert call["system"] == "You are a toy agent."
    # call_model dumped the request under the run directory.
    assert (tmp_path / "llm_requests").exists()


# ---------------------------------------------------------------------------
# History truncation
# ---------------------------------------------------------------------------

def test_view_rules_truncate_history_but_not_stored_steps():
    class _VerboseEnv(_ToyEnv):
        def step(self, action):
            observation = super().step(action)
            if action == "look":
                return "x" * 500
            return observation

    outcome, handle = _run(
        _VerboseEnv(), ["look", "finish"],
        view_rules=ViewRules(max_obs_chars=100, head_keep=10, tail_keep=10))
    # The stored step keeps the full observation (raw evidence rule).
    assert len(outcome.steps[0]["observation"]) == 500
    # The second model call saw the truncated history with a clear marker.
    second_user = handle.client.calls[1]["user"]
    assert "characters truncated" in second_user
    assert "x" * 500 not in second_user


def test_render_history_no_truncation_without_rules():
    steps = [{"action": "a", "observation": "x" * 300}]
    full = render_history(steps)
    assert full == "action: a\nobservation: " + "x" * 300
    assert render_history([]) == ""


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_non_base_env_rejected():
    with pytest.raises(TypeError, match="BaseEnv"):
        _run(object(), ["finish"])


def test_bad_max_steps_is_config_error():
    with pytest.raises(ConfigError, match="max_steps"):
        _run(_ToyEnv(), ["finish"], max_steps=0)
    with pytest.raises(ConfigError, match="max_steps"):
        _run(_ToyEnv(), ["finish"], max_steps="3")


def test_unknown_prompt_name_propagates():
    with pytest.raises(Exception):
        run_react(
            Task(task_id="t", instruction="i"),
            _ToyEnv(),
            make_fake_handle(script=["x"]),
            task_prompt_name="NO_SUCH_PROMPT",
            max_steps=1,
        )
