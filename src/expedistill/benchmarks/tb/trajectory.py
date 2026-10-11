"""Harbor agent-trajectory reconstruction (pure functions, no IO).

The remote link never gives us stepwise interaction: one ``run()`` call is a
whole Harbor rollout, and the per-step view is rebuilt afterwards from the
trial's ``agent/trajectory.json`` (README 1.3, TB runner row). This module
knows that file's shape and nothing else.

Recorded shape (Harbor trials, e.g. the tb21 campaign and the local
``benchmark/terminalbench-eval`` outputs; the same fields UpSkill's
``extract_acp`` reads):

    {"schema_version": ..., "session_id": ..., "agent": {...},
     "steps": [
       {"step_id": 1, "source": "user", "message": "<the instruction>"},
       {"step_id": 2, "source": "agent",
        "message": "...", "reasoning_content": "...",
        "tool_calls": [{"tool_call_id": "...", "function_name": "...",
                        "arguments": {...}}],
        "observation": {"results": [{"content": "..."}]},
        "metrics": {"prompt_tokens": ..., "completion_tokens": ...}},
       ...],
     "final_metrics": {"total_prompt_tokens": ..., "total_completion_tokens": ...,
                       "total_cached_tokens": ...}}

Mapping rules (fixed before looking at any data):

- One kept pair per interaction step: ``action`` is the model's tool-call
  rendering -- ``function_name(json arguments)``, one line per call of the
  step's batch -- and ``observation`` is the joined
  ``observation.results[].content`` of the SAME step (Harbor records the
  environment response in the same dict, after the calls it answers).
- A step is kept only when BOTH sides render; anything else is skipped, not
  crashed on (spec: a malformed step must never take the whole trajectory
  down). ``reconstruct`` also returns how many steps looked like
  interactions (had tool calls or an observation) but failed to map, so the
  plugin can record the count in meta.
- Non-interaction steps (the leading ``source: "user"`` instruction) are
  neither kept nor counted as skipped.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["render_action", "render_observation", "reconstruct",
           "reconstruct_steps", "total_usage"]


def render_action(step: Dict[str, Any]) -> Optional[str]:
    """Render a step's tool calls, or None when the step is not a
    well-formed interaction step."""
    calls = step.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    lines = []
    for call in calls:
        if not isinstance(call, dict):
            return None
        function_name = call.get("function_name")
        if not isinstance(function_name, str) or not function_name:
            return None
        try:
            rendered = json.dumps(call.get("arguments", {}), ensure_ascii=False,
                                  sort_keys=True)
        except (TypeError, ValueError):
            return None
        lines.append(f"{function_name}({rendered})")
    return "\n".join(lines)


def render_observation(step: Dict[str, Any]) -> Optional[str]:
    """Render a step's environment response, or None when absent/malformed."""
    observation = step.get("observation")
    if not isinstance(observation, dict):
        return None
    results = observation.get("results")
    if not isinstance(results, list) or not results:
        return None
    texts = []
    for result in results:
        if not isinstance(result, dict) or result.get("content") is None:
            return None
        texts.append(str(result["content"]))
    return "\n".join(texts)


def reconstruct(traj_json: Any) -> Tuple[List[Dict[str, str]], int]:
    """Map a trajectory dict to (steps, skipped_count).

    Never raises: garbage input maps to ``([], 0)``.
    """
    if not isinstance(traj_json, dict):
        return [], 0
    raw_steps = traj_json.get("steps")
    if not isinstance(raw_steps, list):
        return [], 0
    steps: List[Dict[str, str]] = []
    skipped = 0
    for step in raw_steps:
        if not isinstance(step, dict):
            skipped += 1
            continue
        action = render_action(step)
        observation = render_observation(step)
        if action is not None and observation is not None:
            steps.append({"action": action, "observation": observation})
        elif step.get("tool_calls") or step.get("observation"):
            # Looked like an interaction but could not be mapped.
            skipped += 1
    return steps, skipped


def reconstruct_steps(traj_json: Any) -> List[Dict[str, str]]:
    """Flat {action, observation} pairs; the E1-facing wrapper of reconstruct."""
    return reconstruct(traj_json)[0]


def total_usage(traj_json: Any) -> Tuple[Optional[int], Optional[int]]:
    """(prompt_tokens, completion_tokens) from ``final_metrics``.

    Harbor's per-run token totals. Unavailable fields come back as None --
    the caller records None, never 0 (AGENTS.md section 6 rule 5).
    """
    if not isinstance(traj_json, dict):
        return None, None
    metrics = traj_json.get("final_metrics")
    if not isinstance(metrics, dict):
        return None, None

    def as_int(value: Any) -> Optional[int]:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    return as_int(metrics.get("total_prompt_tokens")), \
        as_int(metrics.get("total_completion_tokens"))
