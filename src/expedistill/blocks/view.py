"""D5 trajectory view: the ONLY block that renders steps to text for a model.

Everything a model ever sees of a trajectory goes through here (G1 sees the
whole trajectory, G2/G3 see the steps of one segment), so truncation policy
lives in exactly one place. The stored Trajectory steps are never truncated --
only this rendered view is (README 1.3, D5 row).

Rendered format, one block per step, numbered from 0:

    step 0
    action: <text>
    observation: <text>

    step 1
    ...

Truncation uses the benchmark plugin's ``ViewRules`` (``.truncate`` applies
head/tail keep with a marker carrying the omitted size -- truncation must be
clearly marked, never silent). The SAME rule is applied to actions: terminal
commands can be long too. ``view_rules=None`` means no truncation at all.

Old repo (ExpeToSkill-merged) reference: react history was rendered as chat
messages and l1/protocol.py kept 180-char observation excerpts with explicit
``observation_truncated`` flags. Deviation here: a flat numbered text view
(the segmentation prompt consumes plain text, not chat turns), and the
truncation parameters are per-benchmark configuration instead of a constant.

Layer 4; depends on core (Step) and benchmarks.base (ViewRules) only.
"""

from typing import Iterable, List, Optional, Union

from expedistill.benchmarks.base import ViewRules
from expedistill.core.models import Step, Trajectory

__all__ = ["render_steps_view", "render_trajectory_view"]

#: Accept both Step objects and {action, observation} mappings: runners and
#: stored records hand over either shape.
_StepLike = Union[Step, dict]


def _step_fields(step: _StepLike, position: int) -> tuple:
    if isinstance(step, dict):
        action, observation = step.get("action"), step.get("observation")
    else:
        action, observation = getattr(step, "action", None), getattr(step, "observation", None)
    if not isinstance(action, str) or not isinstance(observation, str):
        raise TypeError(
            f"step {position} needs string 'action' and 'observation', got "
            f"action={type(action).__name__}, observation={type(observation).__name__}")
    return action, observation


def render_steps_view(steps: Iterable[_StepLike],
                      view_rules: Optional[ViewRules] = None) -> str:
    """Render steps as the numbered text view models consume.

    Deterministic: the same steps and rules always render to the same string.
    ``view_rules=None`` disables truncation entirely.
    """
    blocks: List[str] = []
    for index, step in enumerate(steps):
        action, observation = _step_fields(step, index)
        if view_rules is not None:
            action = view_rules.truncate(action)
            observation = view_rules.truncate(observation)
        blocks.append(f"step {index}\naction: {action}\nobservation: {observation}")
    return "\n\n".join(blocks)


def render_trajectory_view(traj: Trajectory,
                           view_rules: Optional[ViewRules] = None) -> str:
    """Render a whole Trajectory: status header, then the steps view."""
    header = f"task: {traj.task_id} (final status: {traj.status})"
    steps_view = render_steps_view(traj.steps, view_rules)
    if not steps_view:
        return header
    return header + "\n\n" + steps_view
