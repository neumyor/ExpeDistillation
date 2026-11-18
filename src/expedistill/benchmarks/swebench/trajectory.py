"""Stepwise trajectory from mini-SWE-agent's ``traj.json`` (layer 3, pure).

The message list is system, the first user message, then the agent loop:
each assistant message carries its shell commands (``extra.actions``, or the
tool calls' ``command`` arguments), and the tool messages after it carry
their outputs (``extra.raw_output``). A user message inside the loop is the
harness's reply to a malformed response; the final ``exit`` message carries
the submission. The harness's own reader (swebench-eval
``methods/trace.py``) splits the same list into the same steps.
"""

import json
from typing import Any, Dict, List, Mapping, Sequence

__all__ = ["initial_prompt", "steps_from_messages"]

#: Messages whose text is what the last assistant message got back.
_OBSERVATION_ROLES = ("tool", "user", "exit")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI-style content parts
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict)
                         else str(part) for part in content)
    return "" if content is None else str(content)


def _commands(message: Mapping[str, Any]) -> List[str]:
    actions = (message.get("extra") or {}).get("actions")
    if actions:
        return [str(action.get("command", "")) for action in actions]
    commands = []
    for call in message.get("tool_calls") or []:
        try:
            arguments = json.loads(call["function"]["arguments"])
            commands.append(str(arguments.get("command", "")))
        except (KeyError, ValueError, TypeError, AttributeError):
            commands.append("")
    return commands


def steps_from_messages(messages: Sequence[Mapping[str, Any]]
                        ) -> List[Dict[str, str]]:
    """One step per assistant message: ``action`` its commands, one per
    line ("" when it issued none); ``observation`` the texts of the
    messages that followed it, the raw command output where recorded."""
    steps: List[Dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            steps.append({"action": "\n".join(_commands(message)),
                          "observation": ""})
        elif role in _OBSERVATION_ROLES and steps:
            raw = (message.get("extra") or {}).get("raw_output")
            text = _text(raw if raw is not None else message.get("content"))
            step = steps[-1]
            step["observation"] = (f"{step['observation']}\n{text}"
                                   if step["observation"] else text)
    return steps


def initial_prompt(messages: Sequence[Mapping[str, Any]]) -> str:
    """The first user message: the task as the agent received it."""
    for message in messages:
        if message.get("role") == "user":
            return _text(message.get("content"))
    return ""
