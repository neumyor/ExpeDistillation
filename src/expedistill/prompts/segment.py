"""G1 segmentation prompt: split a trajectory view into goal-coherent subtasks."""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SEGMENT"]

SEGMENT = register(
    PromptSpec(
        name="SEGMENT",
        template="""You are given the view of one agent trajectory from Terminal Bench V2.1.
Split it into capability-coherent sub-procedures.

TRAJECTORY:
{trajectory_view}

A segment is one reusable sub-procedure, not one task-specific step. A good
segment has:
- an observable trigger: the situation where the sub-procedure starts;
- a general goal: what it achieves, stated without this task's names or
  concrete values;
- a boundary: the point where the next sub-procedure begins.

Split the trajectory into at most {K} such subtasks.

Rules:
- The segments must cover the whole trajectory in order, without gaps and
  without overlaps: the first segment starts at step 0, the last one ends at
  the final step, and consecutive segments touch (the end index of one is
  the start index of the next; end indices are exclusive).
- Use at most {K} segments, and every segment must contain at least one step.
- Merge adjacent steps that belong to the same capability. Drop pure
  environment setup, one-off commands, and data wrangling that only makes
  sense for this task.
- Do not restate the content of the steps. Describe each segment at the
  level of its observable trigger and general goal, so the step content and
  the segment description stay separate.
- "label" is a short capability phrase, not a task-specific phrase (for
  example "build and verify a source project", "recover a key from known
  pairs").
- "description" is one or two sentences that name the observable trigger and
  the general goal.
- "success" says whether that segment's own goal was achieved by the end of
  the segment, independent of whether the overall trajectory succeeded.

Return ONLY a JSON array, one object per segment in order, with exactly
these fields:
[
  {{"start": <first step index, integer>,
    "end": <step index after the last step of this segment, exclusive, integer>,
    "label": "<short capability phrase>",
    "description": "<one or two sentences: observable trigger and general goal>",
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
