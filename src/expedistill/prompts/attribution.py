"""P04/P06 attribution and revision prompts.

``SKILL_ATTEMPT_ATTRIBUTION`` reads one bounded window of one attempt and
reports, per active skill, what triggered it, what the model did next and
where the skill could improve. It never returns an action.

``SKILL_ATTEMPT_REVISION`` rewrites one skill from all of its window
evidence. The writer itself reconciles conflicting windows.

The old group attribution and trigger-only retrigger prompts are gone;
the P02 add path and the exam path keep their own prompts in author.py.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SKILL_ATTEMPT_ATTRIBUTION", "SKILL_ATTEMPT_REVISION"]

SKILL_ATTEMPT_ATTRIBUTION = register(
    PromptSpec(
        name="SKILL_ATTEMPT_ATTRIBUTION",
        template="""You are attributing ONE window of ONE real attempt of a terminal task
to the skills that were active in that window.

TASK INSTRUCTION (what the weak model saw):
{task_instruction}

ATTEMPT: {attempt}
OUTCOME: {outcome}
WINDOW: {window_kind}, steps {step_start} to {step_end}

TRAJECTORY WINDOW:
{trajectory_window}

SKILLS UNDER REVIEW:
{skills_listing}

{profile_section}

For EACH skill listed above, report:
- "trigger_context": what was happening when this skill became relevant,
  using concrete observable signs or step numbers from the window;
- "behavior_after_trigger": what the model actually did after that point -
  followed the skill, ignored it, or did the opposite - grounded in the
  window;
- "improvement": one concrete change to the skill that would help on a
  later task; use an empty string when the window shows no improvement.

Rules:
- Report facts only. Do not propose keep, revise or delete actions.
- Cover every listed skill exactly once, with its exact skill_id.
- If the window does not show a skill being used, say so in
  trigger_context and leave improvement empty.
- Do not invent steps outside the window.

Output ONLY JSON with exactly this field:
{{"skills": [{{"skill_id": "...", "trigger_context": "...",
  "behavior_after_trigger": "...", "improvement": "..."}}]}}
""",
        description=(
            "P04/P06 per-window attribution: report trigger context, "
            "observed behavior and improvement facts for every active skill."
        ),
    )
)

SKILL_ATTEMPT_REVISION = register(
    PromptSpec(
        name="SKILL_ATTEMPT_REVISION",
        template="""A skill was active in real attempts of a terminal task. Rewrite it using
the per-window evidence below. The evidence may contain several attempts,
and several windows per attempt; reconcile them yourself.

SKILL (current version):
{skill_text}

ATTEMPT EVIDENCE:
{attempt_evidence}

{profile_section}

Rules:
- Keep the three bracket sections exactly:
  [When to use] / [Steps] / [Do not use when].
- Rewrite the skill for a DIFFERENT task of the same kind: no task names,
  file names, entity names or concrete values; use <placeholders>.
- Every step must be grounded in the evidence; do not invent actions the
  windows do not support.
- When windows conflict, prefer the most directly observed behavior and
  state the remaining condition explicitly instead of averaging the
  advice.
- Keep the weak-model style: short sentences, one action per step,
  observable conditions and exact verification.
- A script may appear only as its input, output and invocation; never
  include its implementation. Do not use code blocks longer than three
  lines.
- Keep the card between 1,500 and 2,500 characters.
- Produce a fresh one-line summary: the reusable capability this card
  teaches, with no task or subtask names.

Output ONLY JSON with exactly these fields:
{{"skill_text": "<the rewritten skill, plain text>",
  "revision_reason": "<one or two sentences: what changed and why>",
  "summary": "<one line: the reusable capability; no task names>"}}
""",
        description=(
            "P04/P06 revision writer: rewrite one skill from all of its "
            "per-window attempt evidence."
        ),
    )
)
