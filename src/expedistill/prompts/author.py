"""G2 skill-writing prompts (distill / diagnose) and the G6 rewrite prompt.

All three write for the weak model, in the student-aware style of AgentBrew
(README stage 3): short sentences, explicit order, concrete trigger
conditions, no abstract commands the weak model cannot operationalize.

Protocol v2 (docs/decisions/stage23_protocol_v2_20261013.md section 3.2):
SKILL_DISTILL writes from SUCCEEDED subtasks, SKILL_DIAGNOSE from failed ones,
and both return JSON whose field names map 1:1 onto the three stored sections
-- blocks/author.py validates the fields and its renderer adds the bracket
section markers, so the G2 templates carry no marker literals. Since the
2026-10-19 ruling (docs/decisions/unified_skill_structure_20261019.md) both
return the SAME three fields: a skill learned from a failure has the same
structure as one learned from a success. REWRITE names the three markers:
its rewritten skill_text must reproduce them.

Schema v3 (skill-folder M1, 2026-10-17) adds ONE non-section field to all
three output contracts: ``summary``, the one-line "what this skill is"
sentence a future skill-directory listing shows next to the skill id. It
is not a section and never enters the stored content; when_to_use
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
SEGMENT POSITION: {segment_position}

{profile_section}

Read the weak model profile FIRST. It is the capability boundary of your
reader: every node you write must be an action this exact model can carry
out as written. Where the profile names a weakness, add an explicit guard;
where it says the model cannot do something, never assume it can.

Work in five passes.

Pass 1 - abstract the capability.
Treat the steps as ONE instance of a reusable capability. Decide, for
yourself:
- capability: a short verb-and-object phrase that names the general skill
  (for example "recover a key from known plaintext-ciphertext pairs"). Do
  not use this task's name, file names, or specific tool names in this
  phrase.
- observable_trigger: the concrete signs a new TB21 task shows when this
  capability applies: file types, input structure, error text, available
  tools, required output. Tool names are allowed here.
- invariants: the steps that remain correct when the task's file names,
  constants, and tools change.
- variants: the choices that differ across tasks in this capability family.
  Name a variant type you recognize from the family even if only one variant
  appears in the steps view; but do not invent action steps for variants
  you did not observe.
- failure_guard: the weak reader's most likely failure here, taken from the
  profile.

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
Use only the invariants. For each variant, add a short condition inside the
node ("if the task uses X, ...; otherwise ..."). Every action step must be
grounded in an observed action or observation from the steps view; omit a
step rather than invent it. Do not add separate TB21 discipline nodes;
instead weave the discipline into the verify and fallback parts of nodes:
check before acting, read the error before retrying, run the verifier before
calling mark_task_complete.

Pass 3 - make it executable for the weak model.
- one action per node, imperative voice, no self-numbering;
- every task-specific value is a <placeholder> in angle brackets: file
  names, paths, commands, flags, API names, constants, error strings,
  entities;
- a command may appear only in a general form that works after substituting
  the placeholders;
- scripts used as tools: write only the invocation pattern, the inputs, and
  the expected output—never include the script body, even if the trajectory
  showed it in full;
- code as the deliverable (when the task is to write a program or module):
  describe the structure the code must satisfy and how to verify it with
  the task's tests—do not include the implementation;
- no code block longer than three lines;
- keep the whole card between 1,500 and 2,500 characters; if 5 to 9 full
  nodes do not fit in 2,500 characters, prefer fewer well-written nodes
  over many truncated ones.

Pass 4 - write the trigger and the summary.
The reader does not see this card at first. It sees a list with one line
per card: the summary, then the when_to_use text. From that one line it
decides whether to open the card. So the line must say WHAT the card
teaches and WHEN to open it.

First choose the ONE moment in a new task when the reader should open
this card, and start when_to_use with its phrase, copied exactly:
  "At task start,"
      the reader has just received the task and is reading the task
      description and the given files;
  "After the first environment check,"
      the reader has run its first ls, which, import or network probe and
      now knows what is installed and what is reachable;
  "When a command fails, hangs, or repeats,"
      a command gave an error, got stuck, or the same action keeps
      repeating without progress;
  "Before submitting,"
      the work looks done and the reader is about to run the final check
      and submit;
  "At any step,"
      a basic rule that holds at every step. Use this only when none of
      the four moments above fits.
SEGMENT POSITION is a hint, not a rule: an early segment often belongs
"At task start," or "After the first environment check,"; the last
segment often belongs "Before submitting,".

Then write:
- summary: one sentence, a verb and an object, saying what the card
  teaches. Do not put the trigger in the summary. No task or
  subtask names.
- when_to_use: the timing phrase, then the signs the reader can see at
  that moment (file types, input structure, error text, available tools,
  required output), and the preconditions. One or two sentences.
- not_applicable_when: the signs that this card is wrong for the task.
  Keep these out of when_to_use.

Length: summary and when_to_use together should stay under about 400
characters and must never go over 1,000. Write only complete sentences.
If the text is too long, drop details; never cut a sentence off. Do not
make it vague to make it short: the reader must still be able to tell
this card apart from the other cards in the same list.

Pass 5 - transfer audit.
Before output, check every item; if any fails, rewrite the card:
1. Replace the task's language, tools, file names and constants in your
   mind. Do the nodes still make sense?
2. Is the trigger observable from the task, not from its name?
3. Is every task-specific token a <placeholder>?
4. Are there 5 to 9 decision nodes and 1,500 to 2,500 characters?
5. Does when_to_use start with exactly one of the five timing phrases?
6. Is the summary free of the trigger, and are summary and when_to_use
   complete sentences, together under 1,000 characters (about 400 is
   best)?

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<timing phrase, then the observable trigger and the
preconditions, in one or two sentences>",
  "steps": ["<one decision node: condition, action, verify, fallback>", ...],
  "not_applicable_when": "<observable conditions under which this card is
wrong>",
  "summary": "<one sentence: the reusable capability this card teaches
  (verb + object); no trigger, no task or subtask names>"}}
""",
        description=(
            "G2 skill writing, distill mode: turn a succeeded strong-model "
            "subtask into a three-section procedure skill, written for the "
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
the reader to recognize a general failure pattern and follow a corrective
procedure; do not write a fix for this task.

SUBTASK LABEL: {label}
SUBTASK DESCRIPTION: {description}
WHAT THE PRECEDING PART OF THE TRAJECTORY WAS DOING:
{context_summary}
STEPS OF THE SUBTASK:
{steps_view}
SEGMENT POSITION: {segment_position}

{profile_section}

Read the weak model profile FIRST. It is the capability boundary of your
reader: every node you write must be an action this exact model can carry
out as written. Where the profile names a weakness that caused or worsened
this failure, the guard must address it.

Work in five passes.

Pass 1 - diagnose the general failure pattern.
Find the step or decision where the weak model went wrong. Name the pattern
at the reader's level: skipped verification, misread observation, wrong
tool, unread error before retry, wrong order, early success claim, or an
unrecognized precondition. Do not write "logical error" or this task's name.
Use the context summary only to understand what the reader was trying to do.

Pass 2 - abstract the capability.
Decide, for yourself:
- capability: a short verb-and-object phrase that names the general skill.
  Do not use this task's name, file names, or specific tool names in this
  phrase.
- observable_trigger: the signs a new TB21 task shows when this pattern can
  occur (file types, input structure, error text, tool state, required
  output). Tool names are allowed here.
- invariants: the corrective steps that hold across tasks.
- variants: the choices that differ across tasks. Name a variant type you
  recognize from the family even if only one variant appears in the steps
  view; but do not invent action steps for variants you did not observe.
- failure_guard: the guard that prevents this pattern from recurring.
- corrective_steps: the correct actions that the trajectory shows working,
  OR—where the trajectory only shows the mistake—the minimal corrective
  action the profile tells you the reader can execute (for example: read the
  error message before retrying). Do not invent multi-step procedures that
  are not grounded in the steps view or the failure pattern.

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
Use only the invariants. For each variant, add a short condition inside the
node ("if the task uses X, ...; otherwise ..."). The procedure must teach
the reader to recognize the failing behavior from observable signs, not from
this task's names.
For nodes where the trajectory only shows what went wrong and not what is
correct: write one node that detects the failure sign and one node that
names the corrective action from the profile or the failure pattern—do not
invent a multi-step procedure beyond what the evidence supports.
Do not add separate TB21 discipline nodes; instead weave the discipline into
the verify and fallback parts of nodes: check before acting, read the error
before retrying, run the verifier before calling mark_task_complete.

Make it executable for the weak model:
- one action per node, imperative voice, no self-numbering;
- every task-specific value is a <placeholder> in angle brackets: file
  names, paths, commands, flags, API names, constants, error strings,
  entities;
- a command may appear only in a general form that works after substituting
  the placeholders;
- scripts used as tools: write only the invocation pattern, the inputs, and
  the expected output—never include the script body, even if the trajectory
  showed it in full;
- code as the deliverable (when the task is to write a program or module):
  describe the structure the code must satisfy and how to verify it with
  the task's tests—do not include the implementation;
- no code block longer than three lines;
- keep the whole card between 1,500 and 2,500 characters; if 5 to 9 full
  nodes do not fit in 2,500 characters, prefer fewer well-written nodes
  over many truncated ones.

Pass 4 - write the trigger and the summary.
The reader does not see this card at first. It sees a list with one line
per card: the summary, then the when_to_use text. From that one line it
decides whether to open the card. So the line must say WHAT the card
teaches and WHEN to open it.

First choose the ONE moment in a new task when the reader should open
this card, and start when_to_use with its phrase, copied exactly:
  "At task start,"
      the reader has just received the task and is reading the task
      description and the given files;
  "After the first environment check,"
      the reader has run its first ls, which, import or network probe and
      now knows what is installed and what is reachable;
  "When a command fails, hangs, or repeats,"
      a command gave an error, got stuck, or the same action keeps
      repeating without progress;
  "Before submitting,"
      the work looks done and the reader is about to run the final check
      and submit;
  "At any step,"
      a basic rule that holds at every step. Use this only when none of
      the four moments above fits.
SEGMENT POSITION is a hint, not a rule: a failed segment often belongs
"When a command fails, hangs, or repeats,"; an early segment often
belongs "At task start," or "After the first environment check,"; the
last segment often belongs "Before submitting,".

Then write:
- summary: one sentence, a verb and an object, saying what the card
  teaches and the failure pattern it prevents. Do not put the trigger in
  the summary. No task or subtask names.
- when_to_use: the timing phrase, then the signs the reader can see at
  that moment (file types, input structure, error text, available tools,
  required output), and the failing behavior to recognize. One or two
  sentences.
- not_applicable_when: the signs that this card is wrong for the task.
  Keep these out of when_to_use.

Length: summary and when_to_use together should stay under about 400
characters and must never go over 1,000. Write only complete sentences.
If the text is too long, drop details; never cut a sentence off. Do not
make it vague to make it short: the reader must still be able to tell
this card apart from the other cards in the same list.

Pass 5 - transfer audit.
Before output, check every item; if any fails, rewrite the card:
1. Replace the task's language, tools, file names and constants in your
   mind. Do the nodes still make sense?
2. Is the trigger observable from the task, not from its name?
3. Is every task-specific token a <placeholder>?
4. Are there 5 to 9 decision nodes and 1,500 to 2,500 characters?
5. Does when_to_use start with exactly one of the five timing phrases?
6. Is the summary free of the trigger, and are summary and when_to_use
   complete sentences, together under 1,000 characters (about 400 is
   best)?

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<timing phrase, then the observable trigger and the
failing behavior to recognize, in one or two sentences>",
  "steps": ["<one decision node: condition, action, verify, fallback>", ...],
  "not_applicable_when": "<observable conditions under which this card is
wrong>",
  "summary": "<one sentence: what the card teaches instead (verb + object)
and the failure pattern it prevents; no trigger, no task or subtask names>"}}
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

REAL SITUATION THE EXAM WAS CUT FROM (the trajectory the skill was
learned from; use it to see what the skill must really cover):
{source_context}

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

3. If you rewrite, keep the three bracket sections of the original skill
   ([When to use]/[Steps]/[Do not use when]): rewrite each
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
            "then rewrite the skill section by section, keeping its three "
            "bracket sections, without leaking the exam scenario."
        ),
    )
)
