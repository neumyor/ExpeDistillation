"""ALFWorld execution protocol: the outer eval repo's unified runner.

The paper's ALFWorld numbers are compared with runs made by
``benchmark/alfworld-eval``'s unified protocol (vanilla / SkillOpt /
Trace2Skill / SkillRL). An earlier local prompt repeated the initial room
description in every step and accepted free-form commands, which measured a
materially easier protocol: the same bare student scored ~0.91 locally and
~0.63 in the unified runner. This module makes the local plugin speak the
unified protocol:

* system message: ``You are an expert agent operating in the ALFRED Embodied
  Environment.``;
* user message: the unified templates (no-history at step 1, task line plus
  the last two observations/actions afterwards), with the environment's own
  admissible-action list;
* the model replies ``<think>...</think><action>...</action>``; the action
  must be one of the admissible commands, must follow the tags, and must
  contain no CJK. An invalid reply gets exactly one correction request with
  the same diagnostics the unified runner uses; a still-invalid reply
  executes ``look``;
* temperature 0, ``max_tokens`` 4096, seed 0, at most 50 environment steps
  (the engine's own budget).

Progressive skill disclosure stays: the catalog rides the ``{skill_block}``
slot, and ``<action>ReadSkill[<location>]</action>`` is answered by this
loop (never by the environment) under the folder prompt, at most
``MAX_READS`` cards per attempt. A read costs no environment step and is
recorded as an interaction in the history, so the next prompt's
``current_observation`` is the card text and the step after that sees it as
one of the last two interactions. Bare arms never see the folder prompt and
are byte-identical to the unified runner's prompts.

The templates are copies of ``alfworld_eval.unified.prompts``; the parsing
and diagnostics mirror ``alfworld_eval.unified.runner``. Both files carry
their own byte-exact constants because the two projects are separate
repositories; a test pins the shared text so drift is caught.
"""

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from expedistill.benchmarks.base import BaseEnv, RunOutcome, Task
from expedistill.benchmarks.skill_folder import (
    MAX_READS,
    SkillFolder,
    read_limit_message,
    read_target,
)
from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.llm.call import ModelHandle, call_model
from expedistill.prompts import PromptSpec, register

__all__ = [
    "CORRECTION_PROMPT_NAME",
    "FOLDER_PROMPT_NAME",
    "FOLDER_PROMPT_NO_HISTORY_NAME",
    "MAX_OUTPUT_TOKENS",
    "PROMPT_NAME",
    "PROMPT_NO_HISTORY_NAME",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_NAME",
    "TASK_ANCHOR",
    "TEMPERATURE",
    "extract_task_description",
    "format_admissible_actions",
    "protocol_handle",
    "run_unified",
]

#: The system message of the unified protocol.
SYSTEM_PROMPT_NAME = "ALFWORLD_TASK_PROMPT_SYSTEM"
SYSTEM_PROMPT = (
    "You are an expert agent operating in the ALFRED Embodied Environment.")

PROMPT_NO_HISTORY_NAME = "ALFWORLD_TASK_PROMPT_NO_HISTORY"
PROMPT_NAME = "ALFWORLD_TASK_PROMPT"
FOLDER_PROMPT_NO_HISTORY_NAME = "ALFWORLD_TASK_PROMPT_NO_HISTORY_FOLDER"
FOLDER_PROMPT_NAME = "ALFWORLD_TASK_PROMPT_FOLDER"
CORRECTION_PROMPT_NAME = "ALFWORLD_TASK_PROMPT_CORRECTION"

#: The generation settings of the unified runner (OpenAIChatAgent defaults).
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 4096
SEED = 0

#: Where the initial observation states the task; the unified runner splits
#: it off so later steps can repeat the task without the room description.
TASK_ANCHOR = "Your task is to: "

_ACTION_PROTOCOL_REMINDER = """Before answering, reason in no more than two short
sentences. Do not discuss alternative plans or repeat the task description.
Then output exactly one action from the admissible action list using this format:
<think>brief reason</think><action>one admissible action</action>
Do not output anything after </action>."""

# ruff: noqa: E501 -- byte-exact copies of the unified templates.
_PROMPT_NO_HISTORY = """You are an expert agent operating in the ALFRED Embodied Environment.
Your current observation is: {current_observation}
{skill_block}Your admissible actions of the current situation are: [{admissible_actions}].

""" + _ACTION_PROTOCOL_REMINDER + "\n"

_PROMPT = """You are an expert agent operating in the ALFRED Embodied Environment. Your task is to: {task_description}
{skill_block}Prior to this step, you have already taken {step_count} step(s). Below are the most recent {history_length} observations and the corresponding actions you took: {action_history}
You are now at step {current_step} and your current observation is: {current_observation}
Your admissible actions of the current situation are: [{admissible_actions}].

""" + _ACTION_PROTOCOL_REMINDER + "\n"

#: The folder-only paragraph: progressive disclosure as a pseudo action.
#: The catalog itself rides {skill_block}; this text says what to do with it.
_READ_RULE_ANCHOR = "Do not output anything after </action>.\n"
_READ_RULE = (
    "\n"
    "Skill cards are optional. When the context lists <available_skills>, "
    "you may open one card by replying\n"
    "<think>brief reason</think><action>ReadSkill[<location>]</action> with a "
    "location from that list. The next observation is the card's full text.\n"
    "Opening a card is not a game command and does not use a game step; open "
    f"at most {MAX_READS} cards per episode.\n"
    "Open a card only when its description matches the current situation; if "
    "none matches, open none.\n"
)

_FOLDER_PROMPT_NO_HISTORY = _PROMPT_NO_HISTORY.replace(
    _READ_RULE_ANCHOR, _READ_RULE_ANCHOR + _READ_RULE, 1)
_FOLDER_PROMPT = _PROMPT.replace(
    _READ_RULE_ANCHOR, _READ_RULE_ANCHOR + _READ_RULE, 1)

_CORRECTION_TEMPLATE = """{original_prompt}

VALIDATION FAILED. Correct your previous response once.
Diagnostics: {issues}.
{brevity}Your extracted action was: {requested_action}.
Choose exactly one action from this admissible list: {admissible_list}.
Do not explain the correction. Output exactly one line and nothing else:
<think>brief reason</think><action>one admissible action</action>
Do not output anything before <think> or after </action>."""

register(PromptSpec(
    name=SYSTEM_PROMPT_NAME,
    template=SYSTEM_PROMPT,
    description="ALFWorld unified protocol: the system message.",
))
register(PromptSpec(
    name=PROMPT_NO_HISTORY_NAME,
    template=_PROMPT_NO_HISTORY,
    description="ALFWorld unified protocol, step 1 (no history), bare.",
))
register(PromptSpec(
    name=PROMPT_NAME,
    template=_PROMPT,
    description="ALFWorld unified protocol, steps 2+ (last two "
                "interactions), bare.",
))
register(PromptSpec(
    name=FOLDER_PROMPT_NO_HISTORY_NAME,
    template=_FOLDER_PROMPT_NO_HISTORY,
    description="ALFWorld unified protocol, step 1, skill folder: adds the "
                "ReadSkill card rule.",
))
register(PromptSpec(
    name=FOLDER_PROMPT_NAME,
    template=_FOLDER_PROMPT,
    description="ALFWorld unified protocol, steps 2+, skill folder: adds the "
                "ReadSkill card rule.",
))
register(PromptSpec(
    name=CORRECTION_PROMPT_NAME,
    template=_CORRECTION_TEMPLATE,
    description="ALFWorld unified protocol: the one-shot correction request "
                "after an invalid action reply.",
))

_ACTION_RE = re.compile(r"<action>(.*?)</action>", re.IGNORECASE | re.DOTALL)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)
_CJK_RE = re.compile(r"[一-鿿]")


def extract_task_description(initial_observation: str) -> str:
    """The task line inside ALFWorld's initial observation."""
    start = initial_observation.find(TASK_ANCHOR)
    if start == -1:
        return initial_observation.strip()
    return initial_observation[start + len(TASK_ANCHOR):].strip()


def format_admissible_actions(commands: Sequence[str]) -> str:
    """The unified rendering: one quoted command per line, no ``help``."""
    return "\n ".join(f"'{command}'" for command in commands
                      if command != "help")


def _skill_block(text: str) -> str:
    """The unified ``{skill_block}`` spacing: blank lines around the block."""
    return f"\n{text}\n\n" if text else ""


def project_action(model_response: str) -> Tuple[str, bool]:
    """The unified action projection: lowercase, tags required, no CJK."""
    lowered = model_response.lower()
    start = lowered.find("<action>")
    end = lowered.find("</action>")
    if start == -1 or end == -1:
        action = lowered[-30:] if lowered else "look"
        valid = False
    else:
        action = lowered[start + len("<action>"):end].strip()
        valid = True
    if "<think>" not in lowered or "</think>" not in lowered:
        valid = False
    if _CJK_RE.search(model_response):
        valid = False
    return action, valid


def _diagnose(model_response: str,
              admissible_commands: Sequence[str],
              *, truncated: bool = False) -> Dict[str, Any]:
    """Why a reply cannot be executed; the unified runner's diagnostics."""
    action, format_valid = project_action(model_response)
    commands = list(admissible_commands)
    issues: List[str] = []
    lowered = model_response.lower()
    if truncated:
        issues.append(
            "the response was cut off at the completion token limit before "
            "any <action> was emitted")
    if "<think>" not in lowered:
        issues.append("missing opening <think> tag")
    if "</think>" not in lowered:
        issues.append("missing closing </think> tag")
    if "<action>" not in lowered:
        issues.append("missing opening <action> tag")
    if "</action>" not in lowered:
        issues.append("missing closing </action> tag")
    if _CJK_RE.search(model_response):
        issues.append("response contains non-English characters")
    if action not in commands:
        issues.append(
            f"extracted action {action!r} is not in the admissible action list")
    if not issues and not format_valid:
        issues.append("response does not satisfy the action protocol")
    return {
        "valid": bool(format_valid and action in commands),
        "format_valid": format_valid,
        "requested_action": action,
        "requested_admissible": action in commands,
        "issues": issues,
        "admissible_actions": commands,
    }


def _correction_variables(original_prompt: str, model_response: str,
                          diagnostic: Mapping[str, Any],
                          *, truncated: bool) -> Dict[str, str]:
    """The unified correction request, as template fields."""
    issues = diagnostic["issues"] or ["response failed validation"]
    admissible = diagnostic["admissible_actions"]
    brevity = (
        "Your previous response ran out of output tokens, so it was cut off "
        "mid-reasoning. Reason in at most one short sentence this time.\n"
        if truncated else ""
    )
    return {
        "original_prompt": original_prompt,
        "issues": "; ".join(str(issue) for issue in issues),
        "brevity": brevity,
        "requested_action": repr(diagnostic["requested_action"]),
        "admissible_list": repr(list(admissible)),
    }


def protocol_handle(handle: ModelHandle) -> ModelHandle:
    """The handle's protocol slice: temperature 0, 4096 output, seed 0.

    Built per episode (dataclasses.replace) so the run's accounting and
    request dumps stay on the same handle. The config's model options are
    kept; its generation knobs are not (the config owns budgets for the
    method's own calls, the ALFWorld protocol owns the ones it measures).
    """
    from dataclasses import replace
    extra = dict(handle.extra_body)
    extra.setdefault("seed", SEED)
    return replace(handle, temperature=TEMPERATURE,
                   max_tokens=MAX_OUTPUT_TOKENS, extra_body=extra)


def run_unified(
    task: Task,
    env: BaseEnv,
    handle: ModelHandle,
    *,
    skill_folder: Optional[SkillFolder] = None,
    skills_block: str = "",
    max_steps: int,
    usage: Any = None,
    run_dir: Any = None,
) -> RunOutcome:
    """One episode under the unified protocol; return a uniform RunOutcome.

    ``skills_block`` is the D9 delivery text for non-folder units (it rides
    the same ``{skill_block}`` slot the unified runner gives skill methods).
    With ``skill_folder`` the block is the folder's card list and
    ``ReadSkill[...]`` replies are served by this loop.
    """
    if not isinstance(env, BaseEnv):
        raise TypeError(
            f"run_unified expects a BaseEnv, got {type(env).__name__}")
    if not isinstance(max_steps, int) or max_steps < 1:
        raise ConfigError(f"max_steps must be an int >= 1, got {max_steps!r}")

    initial_observation = str(env.reset())
    budget = getattr(env, "max_steps", None)
    if isinstance(budget, int) and budget > 0 and max_steps != budget:
        raise ConfigError(
            f"max_steps={max_steps} disagrees with the environment's "
            f"episode budget {budget}; the ALFWorld protocol has a single "
            "step-budget source")
    task_description = extract_task_description(initial_observation)

    plain_no_history = (FOLDER_PROMPT_NO_HISTORY_NAME if skill_folder is not None
                        else PROMPT_NO_HISTORY_NAME)
    plain_history = (FOLDER_PROMPT_NAME if skill_folder is not None
                     else PROMPT_NAME)
    block = _skill_block(
        skills_block if skill_folder is None
        else "Available skills:\n" + skill_folder.available_skills_block() + "\n")
    system = _system_prompt()

    steps: List[Dict[str, str]] = []
    history: List[Tuple[str, str]] = []
    observation = initial_observation
    env_steps = 0
    reads = 0
    invalid_actions = 0
    corrections = 0
    termination = "step_budget"

    while env_steps < max_steps and not env.is_terminated() \
            and not env.is_truncated():
        commands = tuple(getattr(env, "admissible_commands", ()) or ())
        recent = history[-2:]
        variables = {
            "current_observation": observation,
            "admissible_actions": format_admissible_actions(commands),
            "skill_block": block,
        }
        prompt_name = plain_no_history
        if recent:
            start = len(history) - len(recent)
            variables.update({
                "task_description": task_description,
                "step_count": str(len(history)),
                "history_length": str(len(recent)),
                "action_history": "\n".join(
                    f"[Observation {start + i + 1}: '{obs}', Action "
                    f"{start + i + 1}: '{action}']"
                    for i, (obs, action) in enumerate(recent)),
                "current_step": str(len(history) + 1),
            })
            prompt_name = plain_history

        truncated = False
        try:
            response = str(call_model(
                handle, prompt_name, variables, usage=usage, run_dir=run_dir,
                system_override=system, max_format_retries=0))
        except ModelOutputError as exc:
            # The unified agent turns an empty or length-cut reply into an
            # invalid action and asks for one correction; call_model raises
            # instead of returning it, so reproduce that step here.
            response = ""
            truncated = getattr(exc, "finish_reason", None) == "length"

        diagnostic = _diagnose(response, commands, truncated=truncated)
        raw_action = _raw_action(response)
        target = (read_target(raw_action, ignore_case=True)
                  if skill_folder is not None
                  and diagnostic["format_valid"] else None)

        if target is not None:
            # A read is answered here, never by the environment, and costs
            # no game step.
            if reads < MAX_READS:
                reads += 1
                card_text, _found = skill_folder.read(target)
                steps.append({"action": f"ReadSkill[{target}]",
                              "observation": card_text})
                history.append((observation, f"ReadSkill[{target}]"))
                observation = card_text
                continue
            env_steps += 1
            steps.append({"action": f"ReadSkill[{target}]",
                          "observation": read_limit_message()})
            continue

        if diagnostic["valid"]:
            action = str(diagnostic["requested_action"])
        else:
            invalid_actions += 1
            corrections += 1
            correction_variables = _correction_variables(
                _render(prompt_name, variables), response, diagnostic,
                truncated=truncated)
            try:
                corrected = str(call_model(
                    handle, CORRECTION_PROMPT_NAME, correction_variables,
                    usage=usage, run_dir=run_dir, system_override=system,
                    max_format_retries=0))
            except ModelOutputError:
                corrected = ""
            second = _diagnose(corrected, commands)
            action = (str(second["requested_action"]) if second["valid"]
                      else "look")

        observation_before = observation
        result = _step(env, action)
        env_steps += 1
        steps.append({"action": action, "observation": result})
        history.append((observation_before, action))
        observation = result
        if env.is_terminated():
            termination = "terminated"
            break
        if env.is_truncated():
            termination = "truncated"
            break

    correctness = False
    if termination != "step_budget" and env.success_fn():
        correctness = True
    meta: Dict[str, Any] = {
        "termination": termination,
        "steps_used": env_steps,
        "initial_observation": initial_observation,
        "invalid_actions": invalid_actions,
        "corrections": corrections,
    }
    if skill_folder is not None:
        meta["skill_list_shown"] = True
        meta["skill_reads"] = reads
    return RunOutcome(steps=steps, correctness=correctness, meta=meta)


def _render(prompt_name: str, variables: Mapping[str, str]) -> str:
    """The prompt text the model actually saw (for the correction request)."""
    from expedistill import prompts
    return prompts.get(prompt_name).format(**variables)


def _system_prompt() -> str:
    from expedistill import prompts
    return prompts.get(SYSTEM_PROMPT_NAME)


def _raw_action(response: str) -> str:
    """The first ``<action>`` content in its original case."""
    match = _ACTION_RE.search(response)
    return match.group(1).strip() if match is not None else ""


def _step(env: BaseEnv, action: str) -> str:
    """Send one validated command; ALFWorld sends it verbatim."""
    step_verbatim = getattr(env, "step_verbatim", None)
    if step_verbatim is not None:
        return str(step_verbatim(action))
    return str(env.step(action))
