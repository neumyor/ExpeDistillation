"""G1 segmentation prompt: split a trajectory view into goal-coherent subtasks."""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SEGMENT"]

SEGMENT = register(
    PromptSpec(
        name="SEGMENT",
        template="""You are given the view of one agent trajectory below. Split it into subtasks.

TRAJECTORY:
{trajectory_view}

Each subtask is one semantically coherent unit of behavior: the complete process
of achieving one goal (for example, "authenticate with a service"). Split the
trajectory into at most {K} such subtasks.

Rules:
- The subtasks must cover the whole trajectory in order, without gaps and without
  overlaps: the first subtask starts at step 0, the last one ends at the final
  step, and consecutive subtasks touch (the end index of one is the start index
  of the next; end indices are exclusive).
- Use at most {K} subtasks, and every subtask must contain at least one step.
- Do not restate the content of the steps. Describe each subtask at the level of
  what it is trying to achieve, so the step content and the segment description
  stay separate.
- "success" says whether that subtask's own goal was achieved by the end of the
  segment, independent of whether the overall trajectory succeeded.

Return ONLY a JSON array, one object per subtask in order, with exactly these
fields:
[
  {{"start": <first step index, integer>,
    "end": <step index after the last step of this segment, exclusive, integer>,
    "label": "<short name, a few words>",
    "description": "<one or two sentences: what this segment is doing>",
    "success": <true or false>}}
]
Return no text before or after the JSON array.
""",
        description=(
            "G1 segmentation: split a trajectory view into at most K "
            "goal-coherent subtasks covering it without gaps or overlaps."
        ),
    )
)
