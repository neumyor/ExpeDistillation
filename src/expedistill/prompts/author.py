"""G2 skill-writing prompts (distill / diagnose) and the G6 rewrite prompt.

All three write for the weak model, in the student-aware style of AgentBrew
(README stage 3): short sentences, explicit order, concrete trigger
conditions, no abstract commands the weak model cannot operationalize.

Protocol v2 (docs/decisions/stage23_protocol_v2_20261013.md section 3.2):
SKILL_DISTILL writes from SUCCEEDED subtasks, SKILL_DIAGNOSE from failed ones,
and both return JSON whose field names map 1:1 onto the four stored sections
-- blocks/author.py validates the fields and its renderer adds the bracket
section markers, so the G2 templates carry no marker literals. Since the
2026-10-19 ruling (docs/decisions/unified_skill_structure_20261019.md) both
return the SAME four fields: a skill learned from a failure has the same
structure as one learned from a success. REWRITE names the four markers:
its rewritten skill_text must reproduce them.

Schema v3 (skill-folder M1, 2026-10-17) adds ONE non-section field to all
three output contracts: ``summary``, the one-line "what this skill is"
sentence a future skill-directory listing shows next to the skill id. It
is not a fifth section and never enters the stored content; when_to_use
says WHEN to apply the skill, summary says WHAT it is. REWRITE must
produce a fresh summary: the content changed, so the old line no longer
describes the skill.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SKILL_DISTILL", "SKILL_DIAGNOSE", "REWRITE"]

SKILL_DISTILL = register(
    PromptSpec(
        name="SKILL_DISTILL",
        template="""You are writing a skill for a weaker model, distilled from ONE subtask of a
strong model's trajectory. The subtask SUCCEEDED: the skill is the executable
procedure the weak model should follow when it faces the same kind of subtask.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
STEPS OF THE SUBTASK:
{steps_view}

{profile_section}

Read the weak model profile FIRST. It is the capability boundary of your
reader: every step you write must be an action this exact model can carry out
as written -- vocabulary it knows, tool syntax it handles, one action per
step. Where the profile names a weakness, add an explicit guard step against
it; where it says the model cannot do something, never assume it can.

Write the skill in four sections:
- when_to_use: the concrete, observable trigger -- "when you need to
  <goal>". Surface features the weak model can recognize mid-task, never
  abstract categories.
- steps: an ordered checklist of concrete actions. One action per item,
  imperative voice, WITHOUT its own numbering (the numbering is added
  downstream). Write the exact command FORM to run, with generic
  placeholders (for example <config-file>, <port>) where the subtask used
  a concrete value -- the discipline below forbids copying that value.
- verify: the observable sign that the subtask worked. State what to do
  when the sign is absent (redo the previous step, or report the failure);
  never "keep retrying".
- not_applicable_when: when this skill does NOT apply, so it is not forced
  onto unrelated subtasks.

Also write a one-line summary of the skill: what it IS and does, in one
or two sentences. This is not when_to_use -- when_to_use says WHEN to
apply the skill, the summary says WHAT it is. It will be shown as the
skill's single line in a directory of skills, so make it concrete and
specific ("inspect the reported service state before acting on it", not
"a helpful skill for service work"), and keep it on one line.

Writing discipline:
- short, simple sentences; one instruction per sentence;
- imperative and concrete. Banned phrases the weak model cannot
  operationalize: "deeply understand", "deeply analyze", "carefully
  consider", "be aware of", "make sure", "keep in mind". Write the action to
  take instead of any of them;
- no references to this specific task, its file names, or its concrete
  values: the skill must transfer to other tasks of the same kind.

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<one or two sentences>",
  "steps": ["<one action>", "<one action>", ...],
  "verify": "<one or two sentences>",
  "not_applicable_when": "<one sentence>",
  "summary": "<one line: what this skill is and does>"}}
""",
        description=(
            "G2 skill writing, distill mode: turn a succeeded strong-model "
            "subtask into a four-section procedure skill, written for the "
            "weak model."
        ),
    )
)

SKILL_DIAGNOSE = register(
    PromptSpec(
        name="SKILL_DIAGNOSE",
        template="""You are writing a skill for a weaker model, learned from ONE subtask of the
weak model's own trajectory where it went wrong. The skill teaches the weak
model to recognize the situation where this mistake happens and what to do
there instead.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING (context summary):
{context_summary}
STEPS OF THE SUBTASK:
{steps_view}

{profile_section}

Work in three passes:
1. Diagnose: find where the weak model started going wrong -- which step or
   decision, and what kind of mistake it made there (a wrong command, a
   skipped check, a misread observation, a repeated failing action, ...).
   Name the missing capability at the weak model's level, not a vague
   "logical error".
2. Adapt: read the weak model profile. It is the capability boundary of your
   reader: every corrective step must be an action this exact model can carry
   out as written -- vocabulary it knows, tool syntax it handles, one action
   per step. Where the profile names a weakness that caused or worsened this
   failure, the fix must guard against it.
3. Write the skill in four sections:
   - when_to_use: the situation where this mistake happens -- concrete and
     observable, phrased so the model can recognize it mid-task (including
     its own failing behavior, such as an error it keeps getting). Say in
     one short sentence what goes wrong there if the model does the usual
     thing.
   - steps: an ordered checklist of concrete actions, the correct procedure
     for this situation. One action per item, imperative voice, WITHOUT its
     own numbering; the exact command FORM with generic placeholders where
     the subtask shows one.
   - verify: the observable sign that the correct procedure worked. State
     what to do when the sign is absent (redo the previous step, or report
     the failure); never "keep retrying".
   - not_applicable_when: when this skill does NOT apply.

Also write a one-line summary of the skill: what mistake it prevents and
what it teaches instead, in one or two sentences. This is not
when_to_use -- when_to_use says WHEN to apply the skill, the summary
says WHAT it is. It will be shown as the skill's single line
in a directory of skills, so make it concrete and specific, and keep it
on one line.

Writing discipline:
- short, simple sentences; one instruction per sentence;
- imperative and concrete. Banned phrases the weak model cannot
  operationalize: "deeply understand", "deeply analyze", "carefully
  consider", "be aware of", "make sure", "keep in mind". Write the action to
  take instead of any of them;
- no references to this specific task, its file names, or its concrete
  values: the skill must transfer to other tasks of the same kind.

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<one or two sentences>",
  "steps": ["<one action>", "<one action>", ...],
  "verify": "<one or two sentences>",
  "not_applicable_when": "<one sentence>",
  "summary": "<one line: what this skill is and does>"}}
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

{profile_section}

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
2. Rewrite the skill to fix the classified problem. The rewritten skill must
   keep the four bracket sections of the original skill ([When to use]/
   [Steps]/[Verify]/[Do not use when]): rewrite each section's content, never collapse them into prose. Reproduce
   the bracketed section headers exactly as written, each on its own line.
   Change the wording, granularity, or trigger conditions of the skill -- not
   its underlying idea. Then write a fresh one-line summary of the REWRITTEN
   skill (what it is and does, one sentence): the content changed, so the
   skill's directory line must describe what it now says, not what it used
   to say. Keep the summary on one line.

Hard constraint: do NOT copy specific values, file names, entities, or any other
concrete details from the exam question into the skill. The rewritten skill must
stay general; it must not smuggle in the answer to this particular exam.

Output ONLY JSON with exactly these fields:
{{"failure_category": "<misread|missing_step|wrong_trigger|other>",
  "skill_text": "<the rewritten skill, plain text>",
  "rewrite_reason": "<one or two sentences: what you changed and why>",
  "summary": "<one line: what the rewritten skill is and does>"}}
""",
        description=(
            "G6 skill rewrite: classify the weak model's failure category, "
            "then rewrite the skill section by section, keeping its four "
            "bracket sections, without leaking the exam scenario."
        ),
    )
)
