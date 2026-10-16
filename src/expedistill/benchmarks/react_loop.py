"""Shared stepwise ReAct loop for LOCAL benchmarks (layer 3).

Adapted from the SkillExpand executor cluster (ExpeToSkill-merged
@ TB-eval 4f436af: runtime/agent/react.py, runtime/agent/base.py and the
step loop inside l1/agent.py:execute_trial). What was kept, and why:

- Stepwise prompt rebuilding: each step re-renders the whole user message from
  the task prompt template (``{instruction}``, ``{skills_block}``,
  ``{history}``) instead of growing a langchain message list -- same prompt
  semantics, no langchain.
- Character discipline: observations fed back into the history can be
  truncated through the plugin's ViewRules so long rollouts stay in budget;
  the stored steps keep full observations, so D5 can render later with any
  rules without re-running anything (README measurement rule 4: keep raw
  unit-level evidence).
- The scorer discipline: ``correctness`` comes from the env's own
  ``success_fn`` plus its termination flags; a step-budget exhaustion is
  ``False`` (the env's contract: an unfinished episode has not achieved the
  goal). A run that ends without an env verdict carries ``correctness=None``
  -- execution claims nothing about the model -- and acceptance decides.
- Failure dispositions mid-episode, by B6 category:

  * ``InfrastructureError`` (model call, ``env.step``, ``env.reset``)
    propagates; K1 retries the unit and D3 never counts it against the
    model.
  * ``ModelOutputError`` from the model call (the client found no usable
    reply, e.g. empty content; call_model does not retry it) -> the episode
    ends with ``correctness=None`` (R2b: no scorer verdict exists for an
    episode that died without an action, so execution records the fact and
    lets acceptance judge), ``meta["termination"] = "model_output_error"``
    plus ``meta["error"]``, partial steps kept. Producing no action is not
    a code bug (stopping the pipeline over one reply) and no longer
    execution's call to score as the model's failure (the pre-R2b
    ``status="failure"`` reclassification).
  * Anything else -- ProviderRefusalError, ConfigError, code bugs --
    propagates and stops the phase.

What was dropped, and why:

- langchain / ExpeL inheritance, few-shot plumbing, ChatMessage collapsing
  (the single re-rendered user message replaces it).
- The L1 repair loop, action-format retries, action-only recovery and the
  repeated-action nudge: those were the old L1's own method, not this
  design; ``call_model`` owns bounded format retries, and action parsing /
  stall detection is per-benchmark knowledge that belongs in plugin envs.
- The 15800-token context cutoff: replaced by the explicit ViewRules
  truncation, which is per-benchmark config, not a constant.
- The 'N/A' no-action placeholder: the loop never invents actions; the model
  reply (stripped) is the action, and an env that dislikes it answers with an
  invalid-action observation of its own.

The loop is benchmark-agnostic and testable with FakeClient + a toy BaseEnv.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.benchmarks.base import BaseEnv, RunOutcome, Task, ViewRules
from expedistill.infra.reliability import (
    ConfigError,
    ModelOutputError,
)
from expedistill.llm.call import ModelHandle, call_model

__all__ = ["run_react", "render_history"]


def render_history(steps: List[Dict[str, str]],
                   view_rules: Optional[ViewRules] = None) -> str:
    """Render accumulated steps as the template's ``{history}`` variable.

    Format per step: ``action: <action>\\nobservation: <observation>``,
    steps joined by newlines; an empty history renders as ``""`` (the first
    model call sees a template with no history section). With ``view_rules``,
    observations in the HISTORY are truncated for the prompt only -- the
    caller's ``steps`` are never modified here.
    """
    parts: List[str] = []
    for step in steps:
        observation = step["observation"]
        if view_rules is not None:
            observation = view_rules.truncate(observation)
        parts.append(f"action: {step['action']}\nobservation: {observation}")
    return "\n".join(parts)


def run_react(
    task: Task,
    env: BaseEnv,
    handle: ModelHandle,
    *,
    task_prompt_name: str,
    skills_block: str = "",
    max_steps: int,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    system_override: Optional[str] = None,
    view_rules: Optional[ViewRules] = None,
) -> RunOutcome:
    """Run one stepwise ReAct episode on ``env``; return a uniform RunOutcome.

    Per step: rebuild the user message from the task prompt template
    (``{instruction}``, ``{skills_block}`` verbatim -- same template bare and
    with skills, README G4 rule -- and ``{history}``), call the model through
    ``call_model``, feed the stripped reply to ``env.step``, append the
    action/observation pair.

    Termination: when the env flags ``is_terminated``/``is_truncated`` the
    verdict comes from ``success_fn()``; a step-budget exhaustion is
    ``False``. Infrastructure failures (``InfrastructureError`` from the
    model call or from ``env.step``, including the ``environment_call``
    deadline) propagate -- K1 retries the unit, D3 never counts the
    attempt against the model. A ``ModelOutputError`` from the model call
    (no usable reply) ends the episode with ``correctness=None`` and
    ``meta["termination"] = "model_output_error"`` plus the error text,
    keeping the steps so far (see the module docstring). Anything else that
    raises propagates.

    ``max_steps`` is the loop's own budget (K1 config); the env may also
    truncate earlier through its own ``max_steps``.
    """
    if not isinstance(env, BaseEnv):
        raise TypeError(
            f"run_react expects a BaseEnv, got {type(env).__name__}")
    if not isinstance(max_steps, int) or max_steps < 1:
        raise ConfigError(f"max_steps must be an int >= 1, got {max_steps!r}")

    steps: List[Dict[str, str]] = []

    env.reset()

    termination = "step_budget"
    while len(steps) < max_steps and not env.is_terminated() \
            and not env.is_truncated():
        history = render_history(steps, view_rules)
        try:
            reply = call_model(
                handle,
                task_prompt_name,
                {"instruction": task.instruction,
                 "skills_block": skills_block,
                 "history": history},
                usage=usage,
                run_dir=run_dir,
                system_override=system_override,
            )
        except ModelOutputError as exc:
            return RunOutcome(
                steps=steps,
                correctness=None,
                meta={"termination": "model_output_error",
                      "steps_used": len(steps),
                      "error": f"{type(exc).__name__}: {exc}"},
            )
        action = str(reply).strip()
        observation = env.step(action)
        steps.append({"action": action, "observation": str(observation)})
        if env.is_terminated():
            termination = "terminated"
            break
        if env.is_truncated():
            termination = "truncated"
            break

    # A budget-exhausted episode is a failure even if success_fn() happens to
    # read true: the env's contract is that success flags termination, and
    # trusting the clock instead would score half-finished episodes.
    correctness = False
    if termination != "step_budget" and env.success_fn():
        correctness = True
    return RunOutcome(
        steps=steps,
        correctness=correctness,
        meta={"termination": termination, "steps_used": len(steps)},
    )
