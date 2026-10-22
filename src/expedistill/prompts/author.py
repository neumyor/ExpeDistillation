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
        template="""You are writing ONE reusable capability card for a weaker terminal agent on
Terminal Bench V2.1. The card will be injected into a DIFFERENT TB21 task
from the same capability family, not the task below. The subtask SUCCEEDED:
the card is the procedure the weak model should follow when it faces the
same kind of subtask. A card that only works on this task is a failure.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
STEPS OF THE SUBTASK:
{steps_view}

{profile_section}

Read the weak model profile FIRST. It is the capability boundary of your
reader: every node you write must be an action this exact model can carry
out as written. Where the profile names a weakness, add an explicit guard;
where it says the model cannot do something, never assume it can.

Work in four passes.

Pass 1 - abstract the capability.
Treat the steps as ONE instance of a reusable capability. Decide, for
yourself:
- capability: a short verb-and-object phrase (for example "recover a key
  from known plaintext-ciphertext pairs", "build a C project from source
  and verify the binary"). Do not use this task's name, file names or tools.
- observable_trigger: the concrete signs a new TB21 task shows when this
  capability applies: file types, input structure, error text, available
  tools, required output.
- invariants: the steps that remain correct when the task's language, file
  names, tools and constants change.
- variants: the choices that differ across tasks in this capability family.
- failure_guard: the weak reader's most likely failure here, taken from the
  profile (for example verifying too late, not reading the error before
  retrying, or declaring success without running the verifier).

If the segment fits none of the TB21 capability families below, use
"general terminal procedure" and write only the meta-rules you can support.

TB21 capability families:
- source build, install and verification
- runtime crash, hang and resource diagnosis
- environment and dependency setup
- numerical and scientific computation with result validation
- cryptographic analysis and security forensics
- sequence, primer and bioinformatics work
- distributed PyTorch and parallel execution
- data, text and large-file processing
- terminal interaction and recovery
- final verification and submission

Pass 2 - write the decision procedure.
Write 5 to 9 decision nodes. A node is one decision, not a script:
When <observable condition>, do <general action>; verify <pass signal>; if
it fails, <fallback>.
Use only the invariants. For each variant, put the choice inside the node as
a short condition ("if the task uses X, ...; otherwise ...") instead of
presenting one variant as universal.
Never invent a step the steps view does not support. Every node must be
grounded in an observed action or observation; if the evidence is missing,
omit the node rather than guess.

Pass 3 - make it executable for the weak model.
- one action per node, imperative voice, no self-numbering;
- every task-specific value is a <placeholder> in angle brackets: file
  names, paths, commands, flags, API names, constants, error strings,
  entities;
- a command may appear only in a general form that works after substituting
  the placeholders;
- a script may appear only as its input, output, and invocation; never
  include its implementation;
- short sentences; no code block longer than three lines;
- keep the whole card between 1,500 and 2,500 characters.

Encode the TB21 discipline:
- locate and read the task's verifier or test before solving; run it after
  the change;
- wait for the shell prompt after every action; send one command per action;
  read the exit status and output before the next action;
- if a command fails, read the error before retrying; do not blind-retry;
- do not call mark_task_complete until the verifier passes; confirm the
  submission prompt at most once;
- if a required file or tool is missing, report it and stop instead of
  inventing a replacement.

Pass 4 - transfer audit.
Before output, check every item; if any fails, rewrite the card:
1. Replace the task's language, tools, file names and constants in your
   mind. Do the nodes still make sense?
2. Is the trigger observable from the task, not from its name?
3. Is every task-specific token a <placeholder>?
4. Are there 5 to 9 decision nodes and 1,500 to 2,500 characters?
5. Does the verify field name the exact pass signal (the task's verifier,
   exit status, required file or expected output) and forbid submission
   before it passes?

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<capability name, observable trigger, preconditions, and
one counterexample in at most two sentences>",
  "steps": ["<one decision node: condition, action, verify, fallback>", ...],
  "verify": "<the exact pass signal and what to do when it is absent>",
  "not_applicable_when": "<observable conditions under which this card is
wrong>",
  "summary": "<one short line: the reusable capability this card teaches
  (verb + object) and its observable trigger; no task or subtask names>"}}
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
        template="""You are writing ONE reusable capability card for a weaker terminal agent on
Terminal Bench V2.1, from a subtask where it went wrong. The card will be
injected into a DIFFERENT TB21 task from the same capability family. Teach
the reader to recognize a general failure pattern and follow a different
procedure; do not write a fix for this task.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING:
{context_summary}
STEPS OF THE SUBTASK:
{steps_view}

{profile_section}

Read the weak model profile FIRST. It is the capability boundary of your
reader: every node you write must be an action this exact model can carry
out as written. Where the profile names a weakness that caused or worsened
this failure, the guard must address it.

Work in four passes.

Pass 1 - diagnose the general failure pattern.
Find the step or decision where the weak model went wrong. Name the pattern
at the reader's level: skipped verification, misread observation, wrong
tool, unread error before retry, wrong order, early success claim, or an
unrecognized precondition. Do not write "logical error" or this task's name.
Use the context summary only to understand what the reader was trying to do.

Pass 2 - abstract the capability.
Decide, for yourself:
- capability: a short verb-and-object phrase;
- observable_trigger: the signs a new TB21 task shows when this pattern can
  occur (file types, input structure, error text, tool state, required
  output);
- invariants: the corrective steps that hold across tasks;
- variants: the choices that differ across tasks;
- failure_guard: the guard that prevents this pattern from recurring.

If the segment fits none of the TB21 capability families below, use
"general terminal procedure" and write only the meta-rules you can support.

TB21 capability families:
- source build, install and verification
- runtime crash, hang and resource diagnosis
- environment and dependency setup
- numerical and scientific computation with result validation
- cryptographic analysis and security forensics
- sequence, primer and bioinformatics work
- distributed PyTorch and parallel execution
- data, text and large-file processing
- terminal interaction and recovery
- final verification and submission

Pass 3 - write the corrective decision procedure.
Write 5 to 9 decision nodes. A node is one decision, not a script:
When <observable condition>, do <general action>; verify <pass signal>; if
it fails, <fallback>.
Use only the invariants. For each variant, put the choice inside the node as
a short condition ("if the task uses X, ...; otherwise ...") instead of
presenting one variant as universal.
The procedure must teach the reader to recognize the failing behavior from
observable signs, not from this task's names. Never invent a step the steps
view does not support; every node must be grounded in an observed action or
observation.

Make it executable for the weak model:
- one action per node, imperative voice, no self-numbering;
- every task-specific value is a <placeholder> in angle brackets: file
  names, paths, commands, flags, API names, constants, error strings,
  entities;
- a command may appear only in a general form that works after substituting
  the placeholders;
- a script may appear only as its input, output, and invocation; never
  include its implementation;
- short sentences; no code block longer than three lines;
- keep the whole card between 1,500 and 2,500 characters.

Encode the TB21 discipline:
- locate and read the task's verifier or test before solving; run it after
  the change;
- wait for the shell prompt after every action; send one command per action;
  read the exit status and output before the next action;
- if a command fails, read the error before retrying; do not blind-retry;
- do not call mark_task_complete until the verifier passes; confirm the
  submission prompt at most once;
- if a required file or tool is missing, report it and stop instead of
  inventing a replacement.

Pass 4 - transfer audit.
Before output, check every item; if any fails, rewrite the card:
1. Replace the task's language, tools, file names and constants in your
   mind. Do the nodes still make sense?
2. Is the trigger observable from the task, not from its name?
3. Is every task-specific token a <placeholder>?
4. Are there 5 to 9 decision nodes and 1,500 to 2,500 characters?
5. Does the verify field name the exact pass signal (the task's verifier,
   exit status, required file or expected output) and forbid submission
   before it passes?

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<capability name, observable trigger, and the failing
behavior to recognize, in at most two sentences>",
  "steps": ["<one decision node: condition, action, verify, fallback>", ...],
  "verify": "<the exact pass signal and what to do when it is absent>",
  "not_applicable_when": "<observable conditions under which this card is
wrong>",
  "summary": "<one short line: the failure pattern this card prevents and
the reusable capability it teaches instead; no task or subtask names>"}}
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
        template="""A skill written for a weak model failed its acceptance exam. Rewrite it
only if the failure reveals a general defect. This is a Terminal Bench V2.1
skill; the card will be used on other tasks, so task-specific details must
not enter it.

SKILL (current version):
{skill_text}

EXAM QUESTION:
{question_text}

WEAK MODEL'S ANSWER:
{weak_answer}

WHAT WAS WRONG WITH THE ANSWER:
{error_description}

{profile_section}

No reference answer is available to you, and you must not invent or guess
one.

Work in three steps:

1. Classify why the weak model did not succeed. Choose exactly one category:
   - "misread": the weak model misunderstood the instruction;
   - "missing_step": the skill skipped an intermediate step that needed to
     be spelled out;
   - "wrong_trigger": the condition for when to apply the skill was unclear,
     so the weak model applied it wrongly or at the wrong moment;
   - "other": none of the above.

2. Decide whether the failure is a general defect in the skill or a
   task-specific issue.
   - General defect: a missing intermediate step, a trigger the reader
     cannot observe, a step the reader cannot execute, or a misleading
     order. Rewrite the skill.
   - Task-specific issue: a fact about this exam's files, values, tools or
     expected answer. Do not encode it. Return the skill unchanged and say
     in rewrite_reason that the failure is task-specific.

3. If you rewrite, keep the four bracket sections of the original skill
   ([When to use]/[Steps]/[Verify]/[Do not use when]): rewrite each
   section's content, never collapse them into prose. Reproduce
   the bracketed section headers exactly as written, each on its own line.
   Change the wording, granularity, or trigger conditions of the skill --
   not its underlying idea.
   Rewrite to 5 to 9 decision nodes and 1,500 to 2,500 characters. Compress
   instead of appending. Every node must remain a general decision rule;
   task-specific values become <placeholders>. A script may appear only as
   its input, output, and invocation; never include its implementation, and
   never use a code block longer than three lines.
   Then write a fresh capability-level summary of the REWRITTEN skill: the
   reusable capability it teaches (verb + object) and its observable
   trigger, with no task or subtask names. Keep it on one line.

Hard constraint: do NOT copy specific values, file names, entities, or any
other concrete details from the exam question into the skill. The rewritten
skill must stay general; it must not smuggle in the answer to this
particular exam.

Transfer audit before output:
1. Would the rewritten nodes still make sense if the exam's language, tools,
   file names and constants changed?
2. Is every trigger observable from the task, not from its name?
3. Is every task-specific token a <placeholder>?
4. Are there 5 to 9 decision nodes and 1,500 to 2,500 characters?
5. Does the verify part name the exact pass signal and forbid submission
   before it passes?
If any check fails, rewrite again.

Output ONLY JSON with exactly these fields:
{{"failure_category": "<misread|missing_step|wrong_trigger|other>",
  "skill_text": "<the rewritten skill, plain text>",
  "rewrite_reason": "<one or two sentences: what you changed and why, or why
  the failure is task-specific and the skill is unchanged>",
  "summary": "<one line: the reusable capability the rewritten skill teaches
  (verb + object) and its observable trigger; no task or subtask names>"}}
""",
        description=(
            "G6 skill rewrite: classify the weak model's failure category, "
            "then rewrite the skill section by section, keeping its four "
            "bracket sections, without leaking the exam scenario."
        ),
    )
)
