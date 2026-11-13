"""The learn phase's subtask-card prompts.

* ``SUBTASK_TYPING`` -- put each new segment into the run's subtask type
  table: an existing type, or a new type declared in the same reply with a
  name and an observable entry state (G2, ``blocks/subtask_types``);
* ``CARD_WRITE`` -- write ONE card for one subtask type that has gap
  evidence, from the strong model's successful segments of that type and
  the weak model's failed ones (G3, ``methods/cards``);
* ``CARDS_CORRECTION`` -- the ``correction_note`` either prompt carries
  after a reply the checks rejected.

JSON is the canonical output of both; the type ids are assigned by the
program, never taken from the reply.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["CARDS_CORRECTION", "CARD_WRITE", "SUBTASK_TYPING"]


SUBTASK_TYPING = register(
    PromptSpec(
        name="SUBTASK_TYPING",
        template="""You sort segments of {agent_kind}-agent trajectories on {benchmark_name} into
subtask types. A subtask type is one recurring part of a task that a weaker
agent can learn as a unit, such as finding a needed object or recovering
from a rejected action. It is defined by its observable entry state: what
the agent can see at the moment the subtask starts ({observable_signs}).
It is not a moment in time ("at task start", "before finishing") and not a
task name.

EXISTING SUBTASK TYPES (type id | name | entry state; "(none)" when the
table is empty):
{type_table}

SEGMENTS TO SORT (SOURCE names the model that ran the segment, strong or
weak; SUBTASK_SUCCESS says whether the segment reached its own goal):
{cards}

Rules:
- Put every SUBTASK_ID from the segments exactly once.
- Never invent a SUBTASK_ID and never repeat one.
- Prefer an existing type whenever the segment does the same part of the
  task from the same kind of entry state, even when task names, objects or
  files differ. A failed segment belongs to the type of the part it was
  trying to do.
- Declare a new type only when no existing type fits. Give it a short key
  of your own ("new1", "new2", ...), a name (a verb and an object, no task
  names) and its entry state (one sentence naming what the agent can
  observe when this subtask starts). Several segments may share a new
  type.
- The "type" of an assignment is either an existing type id from the table
  or the key of a new type declared in this reply.

{correction_note}Output ONLY JSON with exactly this shape:
{{"new_types": [
  {{"key": "<new1>", "name": "<verb and object>",
    "entry_state": "<what the agent observes when this subtask starts>"}}
 ],
 "assignments": [
  {{"subtask_id": "<exact SUBTASK_ID from the segments>",
    "type": "<an existing type id or a new type key>"}}
 ]}}
""",
        description=(
            "Learn step 2 (G2): assign every new segment to an existing "
            "subtask type or to a new type declared in the same reply."
        ),
    )
)


CARD_WRITE = register(
    PromptSpec(
        name="CARD_WRITE",
        template="""You are writing ONE skill card for a weaker {agent_kind} agent on {benchmark_name}.
The card teaches one subtask type. It will be listed in the agent's skill
catalog on DIFFERENT {task_tag} tasks, not the tasks below, and the agent
opens it when its situation matches the card's "when to use" text.

SUBTASK TYPE: {type_name}
ENTRY STATE (what the agent observes when this subtask starts):
{entry_state}

HOW THE STRONG MODEL DID IT (successful segments of this type; SOURCE and
SUBTASK_SUCCESS describe each segment):
{strong_segments}

WHERE THE WEAK MODEL FAILED AT IT (failed segments of this type from the
latest trials; "(none)" when there are none):
{weak_segments}

ENVIRONMENT PROTOCOL (what the agent sees and may do at each step; every
step you write must be executable under it):
{protocol}

THE PLAYBOOK THE AGENT ALREADY SEES AT EVERY STEP ("(none)" when it has
none):
{playbook}

{profile_section}

Read the weak model profile FIRST. Every step must be an action this exact
model can carry out as written. Where the profile names a weakness, add an
explicit guard.

Write the card so that it adds only what the playbook does not already
give: the procedure for this subtask, the checks that tell it worked, and
the mistakes the failed segments show. Never repeat a playbook rule.

- when_to_use: the subtask's observable entry state, in terms the agent can
  check from what it sees ({observable_signs}). Do not describe a moment
  in time and do not use task names.
- steps: 3 to 7 steps, as a list without numbers. A step is one decision:
  "When <observable condition>, do <general action>; verify <pass signal>;
  if it fails, <fallback>." Use only actions the segments support.
{executable_rules}
- not_applicable_when: observable signs that this card is wrong for the
  situation.
- summary: one line, a verb and an object, saying what the card teaches. No
  trigger and no task names.

Keep the card short: under 1,500 characters in total. Use only complete
sentences.

{correction_note}Output ONLY JSON with exactly these fields:
{{"when_to_use": "<the observable entry state of the subtask>",
  "steps": ["<one decision: condition, action, verify, fallback>", "..."],
  "not_applicable_when": "<observable conditions under which this card is wrong>",
  "summary": "<one line: what this card teaches>"}}
""",
        description=(
            "Learn step 5 (G3): write one card for one subtask type with "
            "gap evidence, from strong successful and weak failed segments "
            "of that type."
        ),
    )
)


CARDS_CORRECTION = register(
    PromptSpec(
        name="CARDS_CORRECTION",
        template=("Your previous reply was rejected: {problem}. Answer "
                  "again in exactly the shape given below.\n\n"),
        description=(
            "Learn steps 2 and 5: the correction note of a typing or card "
            "reply the checks rejected; {problem} names what was wrong."
        ),
    )
)
