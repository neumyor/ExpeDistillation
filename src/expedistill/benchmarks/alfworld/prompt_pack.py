"""ALFWorld prompt pack (layer 3): every ALFWorld-specific prompt input.

The commands, rules and feedback strings of the vocabulary are the ones the
unified protocol's prompts (``protocol.py``) and the environment bridge
(``env.py``) already use; nothing is invented.
"""

from expedistill.benchmarks.alfworld.playbook import (
    PLAYBOOK_PROMPT_NAME,
    facts_from_sheets,
    playbook_requirements,
)
from expedistill.benchmarks.alfworld.protocol import (
    CORRECTION_PROMPT_NAME,
    FOLDER_PROMPT_NAME,
    FOLDER_PROMPT_NO_HISTORY_NAME,
    PROMPT_NAME,
    PROMPT_NO_HISTORY_NAME,
    SYSTEM_PROMPT_NAME,
)
from expedistill.benchmarks.base import PromptPack
from expedistill.prompts.learning_context import LearningContext

__all__ = ["PROMPT_PACK"]

_VOCABULARY = LearningContext(name="alfworld", slots={
    "agent_kind": "household",
    "benchmark_name": "ALFWorld",
    "skill_description": "an ALFWorld skill",
    "task_tag": "ALFWorld",
    "action_noun": "command",
    "discipline_examples": (
        "check the room and your inventory before acting, read the "
        "feedback before repeating a command, confirm the held object and "
        "the target receptacle before the final move."
    ),
    "executable_rules": (
        "- every task-specific value is a <placeholder> in angle brackets: "
        "object\n"
        "  names, receptacle names, their numbers, feedback strings;\n"
        "- a command may appear only in a general form that works after "
        "substituting\n"
        '  the placeholders, for example "go to <receptacle>", "take '
        '<object> from\n'
        '  <receptacle>" or "move <object> to <receptacle>";\n'
        "- use only the commands the game accepts: go to, open, close, "
        "examine,\n"
        "  take ... from, move ... to, inventory, use, clean/heat/cool ... "
        "with;\n"
        "  never invent a command;"
    ),
    "observable_signs": (
        "the task wording, objects and receptacles in view, the held "
        "object, feedback text"
    ),
    "segment_label_examples": (
        '"search receptacles for the target object", '
        '"heat a held object and place it in a receptacle"'
    ),
    "setup_steps": (
        "Looking around, checking the inventory, and one-off detours"
    ),
})

_PROTOCOL = """\
The reader executes one ALFWorld task under the benchmark's unified
protocol. At every decision step its entire context is exactly:

1. the current observation (the environment text it received just now);
2. the last two (observation, action) pairs, verbatim -- anything older has
   left the context;
3. the "Opened skill cards" block: the full text of every skill card the
   reader opened in this episode, kept verbatim until the episode ends.

Opening a card does not take one of those two interaction slots. It is not
a game action: the card text simply appears in the opened-cards block.

The reader does NOT see: the initial room description after the second
action, any interaction older than the last two, any skill card it has not
opened, a separate copy of the task instruction, or a plan it wrote in an
earlier step.

The prompt's admissible-action list names every place the reader can move
to right now (furniture, containers, and any other place the environment
lists), and it stays visible at every step. The initial room description
does not.

What every written procedure must respect:

- Every one of a card's steps must be executable from those three sources
  alone at the moment it fires.
- Never refer to an earlier step by position ("the object you saw two steps
  ago", "as established above", "the location from your first look"): that
  interaction is gone.
- Never assume the reader still remembers the initial room listing or the
  full instruction. A node that needs a fact must say how to re-observe it
  in that same node: look, open the receptacle, check the inventory.
- A fact stated in this card stays available while the card is open, so a
  card may carry a short list of object names, locations or inventory it
  wants the reader to keep using. It must still say how to obtain a fact
  that is neither in the card nor in the current observation.
- Prefer one self-contained action per node over a long ordered plan whose
  later nodes depend on state the reader can no longer see.
- The reader can run at most one environment action per step; a procedure
  that needs several observations must say what to check at each one."""

PROMPT_PACK = PromptPack(
    vocabulary=_VOCABULARY,
    protocol=_PROTOCOL,
    executor_prompts=(
        SYSTEM_PROMPT_NAME,
        PROMPT_NO_HISTORY_NAME,
        PROMPT_NAME,
        FOLDER_PROMPT_NO_HISTORY_NAME,
        FOLDER_PROMPT_NAME,
        CORRECTION_PROMPT_NAME,
    ),
    playbook_seed="",
    playbook_facts=facts_from_sheets,
    playbook_checks=tuple(playbook_requirements()),
    overrides={"PLAYBOOK_UPDATE": PLAYBOOK_PROMPT_NAME},
)
