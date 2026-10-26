"""Prompts for the cluster-by-trigger P02 authoring method.

Three calls make one cluster skill:

* ``CLUSTER_TRIGGER_FAMILY`` -- put one segment into one of the five
  trigger-timing families;
* ``CLUSTER_SUBTASKS`` -- group one family's segments by the problem
  they handle;
* ``CLUSTER_SKILL`` -- write ONE three-section skill for one group.

JSON is the canonical output of all three; Markdown views are rendered
by the stage module and are never parsed back.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["CLUSTER_SKILL", "CLUSTER_SUBTASKS", "CLUSTER_TRIGGER_FAMILY"]


CLUSTER_TRIGGER_FAMILY = register(
    PromptSpec(
        name="CLUSTER_TRIGGER_FAMILY",
        template="""You classify ONE segment of a terminal-agent trajectory into the trigger
timing at which a reusable skill distilled from it should be opened.

SEGMENT LABEL: {label}
SEGMENT DESCRIPTION: {description}
SEGMENT SOURCE: {source}
SEGMENT SUCCESS: {success}
SEGMENT POSITION: {segment_position}
STEPS:
{steps_view}

Choose exactly ONE family key from this closed list:

- "at_task_start": the reader has just received a new task and is reading
  the task description, inputs and initial files.
- "after_first_environment_check": the reader has run its first ls, which,
  import or network probe and now knows what is installed and reachable.
- "when_command_fails": a command produced an error, hung, or the same
  action keeps repeating without progress.
- "before_submitting": the work looks done and the reader is about to run
  the final verifier and submit.
- "at_any_step": a basic rule that applies at every step and fits none of
  the four moments above.

Use the segment position only as a hint: an early segment often belongs to
the first two families, and the last segment often belongs to
"before_submitting". Judge from the observed steps, not the task name.

Output ONLY JSON with exactly these fields:
{{"family": "<one of the five keys above>",
  "reason": "<one short sentence naming the observable cue>"}}
""",
        description=(
            "Cluster authoring: classify one segment into the five fixed "
            "trigger-timing families."
        ),
    )
)


CLUSTER_SUBTASKS = register(
    PromptSpec(
        name="CLUSTER_SUBTASKS",
        template="""You group subtasks of ONE trigger-timing family by the problem they
handle. The reader of the resulting skills is a weaker terminal agent on
Terminal Bench V2.1. Group subtasks that call for the same reusable
capability, even when their task names, files and tools differ.

FAMILY: {family}
FAMILY MEANING: {family_definition}

SUBTASK CARDS:
{cards}

Rules:
- Use every SUBTASK_ID from the cards exactly once.
- Never invent a SUBTASK_ID and never repeat one.
- A group may have one member when no other card belongs with it.
- The theme must name a capability (a verb and an object), not a task name.
- The reason must state what the members have in common.

Output ONLY JSON with exactly this shape:
{{"clusters": [
  {{"theme": "<capability theme>",
    "subtask_ids": ["<exact SUBTASK_ID from the cards>", "..."],
    "reason": "<one sentence: what the members have in common>"}}
]}}
""",
        description=(
            "Cluster authoring: partition one trigger family's subtasks by "
            "the problem they handle."
        ),
    )
)


CLUSTER_SKILL = register(
    PromptSpec(
        name="CLUSTER_SKILL",
        template="""You are writing ONE reusable capability card for a weaker terminal agent
on Terminal Bench V2.1. The card will be injected into a DIFFERENT TB21
task from the same capability family, not the tasks below.

TRIGGER FAMILY: {family}
OPENING PHRASE: {family_trigger}
CLUSTER THEME: {theme}
WHY THESE SEGMENTS BELONG TOGETHER: {reason}

SOURCE SUBTASKS:
{cards}

{profile_section}

Read the weak model profile FIRST. Every node must be an action this exact
model can carry out as written. Where the profile names a weakness, add an
explicit guard.

Write ONE skill that generalizes across all source subtasks:

- when_to_use: begin with the OPENING PHRASE exactly, then name the
  observable signs (file types, input structure, error text, tools,
  required output) and the preconditions. Do not use task names.
- steps: 5 to 9 decision nodes. Each node is one decision, not a script:
  "When <observable condition>, do <general action>; verify <pass signal>;
  if it fails, <fallback>." Use only actions supported by the cards; omit
  rather than invent. Make every task-specific value a <placeholder>.
- not_applicable_when: observable signs that this card is wrong for the
  task.
- summary: one line, a verb and an object, saying what the card teaches.
  Do not put the trigger in the summary and do not use task or subtask
  names.

Keep the whole card between 1,500 and 2,500 characters. Keep summary and
when_to_use together under about 400 characters and never over 1,000.
Use only complete sentences.

Output ONLY JSON with exactly these fields:
{{"when_to_use": "<opening phrase, then observable trigger and preconditions>",
  "steps": ["<one decision node: condition, action, verify, fallback>", "..."],
  "not_applicable_when": "<observable conditions under which this card is wrong>",
  "summary": "<one sentence: the reusable capability this card teaches>"}}
""",
        description=(
            "Cluster authoring: write one three-section skill from one "
            "problem cluster."
        ),
    )
)
