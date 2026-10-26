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
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.core.models import Step, Trajectory

__all__ = ["final_status_word", "render_answer_sheet",
           "render_steps_view", "render_trajectory_view"]

#: Accept both Step objects and {action, observation} mappings: runners and
#: stored records hand over either shape.
_StepLike = Union[Step, dict]


def final_status_word(correctness: Optional[bool]) -> str:
    """The header/evidence word for one run's scorer verdict.

    A pure display label, not a data vocabulary: ``correctness`` True /
    False / None render as ``success`` / ``failure`` / ``unknown``. The
    True/False words are byte-identical to the pre-R2b header on purpose --
    they feed the G1 segmentation prompt and the G10 evidence lines, both
    pinned call for call by the semantic instrument's E4 -- so this mapping
    is frozen the same way a prompt is (changing it is a new experiment).
    """
    return {True: "success", False: "failure"}.get(correctness, "unknown")


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
                      view_rules: Optional[ViewRules] = None,
                      *, start_index: int = 0) -> str:
    """Render steps as the numbered text view models consume.

    Deterministic: the same steps and rules always render to the same string.
    ``view_rules=None`` disables truncation entirely. ``start_index`` is
    the original trajectory index of the first rendered step; a caller
    rendering a slice passes the slice's offset so the model sees the
    same step numbers the window metadata carries.
    """
    blocks: List[str] = []
    for index, step in enumerate(steps, start=start_index):
        action, observation = _step_fields(step, index)
        if view_rules is not None:
            action = view_rules.truncate(action)
            observation = view_rules.truncate(observation)
        blocks.append(f"step {index}\naction: {action}\nobservation: {observation}")
    return "\n\n".join(blocks)


def render_trajectory_view(traj: Trajectory,
                           view_rules: Optional[ViewRules] = None) -> str:
    """Render a whole Trajectory: status header, then the steps view."""
    header = (f"task: {traj.task_id} "
              f"(final status: {final_status_word(traj.correctness)})")
    steps_view = render_steps_view(traj.steps, view_rules)
    if not steps_view:
        return header
    return header + "\n\n" + steps_view


def render_answer_sheet(sheet: AnswerSheet,
                        view_rules: Optional[ViewRules] = None) -> str:
    """Render one trial-level answer sheet as Markdown.

    The JSON record stays canonical; this renderer is the human-readable
    view used by the derived ``answer_sheets/*.md`` export.  A missing or
    terminal trial has no execution payload: the coverage section is the
    whole sheet.
    """
    execution = sheet.execution
    coverage = sheet.coverage
    grading = sheet.grading
    lines = [
        f"# Answer sheet `{sheet.sheet_id}`",
        "",
        f"- phase: `{sheet.phase_label}`",
        f"- unit: `{coverage.unit_id}`",
        f"- attempt: `{coverage.attempt_index}`",
        f"- coverage: `{coverage.status}`"
        + (f" / `{coverage.cause}`" if coverage.cause else ""),
    ]
    if execution is not None:
        lines.extend([
            f"- task: `{execution.task_ref}`",
            f"- arm: `{execution.arm}`",
            f"- role/model: `{execution.model_role}` / `{execution.model_name}`",
            f"- skills: `{execution.skills_injected}`",
            f"- raw correctness: `{execution.raw_correctness}`",
        ])
    if grading is not None:
        lines.extend([
            f"- verdict: `{grading.verdict}`",
            f"- basis: {grading.basis}",
            f"- rule: `{grading.rule_name}`",
        ])
    lines.append("")

    if execution is None:
        lines.append("_No execution record: this trial was missing or "
                     "terminal before an answer existed._")
        return "\n".join(lines)

    if execution.kind == "trajectory":
        lines.append("## Trajectory")
        lines.append("")
        lines.append(render_steps_view(execution.steps or [], view_rules))
    elif execution.kind == "attempt":
        lines.append("## Exam answer")
        lines.append("")
        lines.append(execution.answer or "")
    elif execution.kind == "external":
        lines.append("## External campaign reward")
        lines.append("")
        rewards = execution.rewards or []
        lines.append("| attempt | reward |")
        lines.append("|---:|---:|")
        for index, reward in enumerate(rewards):
            lines.append(f"| {index} | {reward} |")
        source = execution.artifacts.get("source")
        sha = execution.artifacts.get("sha256")
        if source:
            lines.append("")
            lines.append(f"- source: `{source}`")
        if sha:
            lines.append(f"- sha256: `{sha}`")
    return "\n".join(lines)
