"""G2 skill-writing prompts (distill / diagnose) and the G6 rewrite prompt.

All three write for the weak model, in the student-aware style of AgentBrew
(README stage 3): short sentences, explicit order, concrete trigger conditions,
no abstract commands the weak model cannot operationalize.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SKILL_DISTILL", "SKILL_DIAGNOSE", "REWRITE"]

SKILL_DISTILL = register(
    PromptSpec(
        name="SKILL_DISTILL",
        template="""You are writing a skill for a weaker model, distilled from one subtask of a
strong model's trajectory.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
SUBTASK OUTCOME (success): {success}
STEPS OF THE SUBTASK:
{steps_view}

THE MODEL THAT WILL READ THE SKILL (weak model profile):
{weak_profile}

Distill this subtask into a skill:
- If the subtask succeeded, distill WHAT TO DO: the working approach.
- If the subtask failed, distill the LESSON: what the approach was, why it failed,
  and what should be done instead.

The skill must be written so the weak model can actually execute it:
- short, simple sentences, one instruction per sentence;
- an explicit order of steps whenever the order matters;
- concrete trigger conditions ("when you see X, do Y"), not abstract commands the
  weak model cannot operationalize (write "check whether the file exists before
  opening it", not "be careful with the file system");
- no references to this specific task, its file names, or its concrete values:
  the skill must transfer to other tasks of the same kind.

Output ONLY the skill text as plain prose. No title, no JSON, no commentary.
""",
        description=(
            "G2 skill writing, distill mode: turn a strong-model subtask into a "
            "what-to-do skill or a failure lesson, written for the weak model."
        ),
    )
)

SKILL_DIAGNOSE = register(
    PromptSpec(
        name="SKILL_DIAGNOSE",
        template="""You are writing a skill for a weaker model, based on one subtask where the
weak model itself went wrong.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING (context summary):
{context_summary}
STEPS OF THE SUBTASK:
{steps_view}

THE MODEL THAT WILL READ THE SKILL (weak model profile):
{weak_profile}

Diagnose the failure first, then write the skill:
1. Identify where the weak model started going wrong: which step or decision, and
   what kind of mistake it made there.
2. State the correct approach for this kind of subtask.

Then write the skill for the weak model so it gets this kind of subtask right next
time:
- short, simple sentences, one instruction per sentence;
- an explicit order of steps whenever the order matters;
- concrete trigger conditions ("when you see X, do Y"), not abstract commands the
  weak model cannot operationalize (write "check whether the file exists before
  opening it", not "be careful with the file system");
- name the mistake to avoid only when naming it helps the weak model avoid it;
- no references to this specific task, its file names, or its concrete values:
  the skill must transfer to other tasks of the same kind.

Output ONLY the skill text as plain prose. No title, no JSON, no commentary.
""",
        description=(
            "G2 skill writing, diagnose mode: find where the weak model went "
            "wrong and write the corrective skill for the weak model."
        ),
    )
)

REWRITE = register(
    PromptSpec(
        name="REWRITE",
        template="""A skill written for a weak model failed its acceptance exam. Rewrite it.

SKILL (current version):
{skill_text}

EXAM QUESTION:
{question_text}

WEAK MODEL'S ANSWER:
{weak_answer}

WHAT WAS WRONG WITH THE ANSWER:
{error_description}

THE MODEL THAT WILL READ THE SKILL (weak model profile):
{weak_profile}

No reference answer is available to you, and you must not invent or guess one.
When a weak model fails an exam, the skill's idea is usually not wrong; the
wording is something the weak model cannot read or execute: missing intermediate
steps, trigger conditions too abstract, vocabulary beyond its comprehension.

Work in two steps:
1. Classify why the weak model did not succeed. Choose exactly one category:
   - "misread": the weak model misunderstood the instruction;
   - "missing_step": the skill skipped an intermediate step that needed to be
     spelled out;
   - "wrong_trigger": the condition for when to apply the skill was unclear, so
     the weak model applied it wrongly or at the wrong moment;
   - "other": none of the above.
2. Rewrite the skill to fix the classified problem. Change the wording,
   structure, granularity, or trigger conditions of the skill -- not its
   underlying idea.

Hard constraint: do NOT copy specific values, file names, entities, or any other
concrete details from the exam question into the skill. The rewritten skill must
stay general; it must not smuggle in the answer to this particular exam.

Output ONLY JSON with exactly these fields:
{{"failure_category": "<misread|missing_step|wrong_trigger|other>",
  "skill_text": "<the rewritten skill, plain text>",
  "rewrite_reason": "<one or two sentences: what you changed and why>"}}
""",
        description=(
            "G6 skill rewrite: classify the weak model's failure category, then "
            "rewrite the skill without leaking the exam scenario."
        ),
    )
)
