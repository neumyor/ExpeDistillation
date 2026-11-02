"""ALFWorld vocabulary for the learning prompts (layer 3).

The commands, rules and feedback strings named here are the ones the task
prompt in ``plugin.py`` (``TASK_PROMPT_TEMPLATE``) and the environment
bridge (``env.py``) already use; nothing is invented.
"""

from expedistill.prompts.learning_context import (
    LearningContext,
    trigger_timing_slots,
)

__all__ = ["ALFWORLD_CONTEXT"]

#: One text per moment; ALFWorld shows it the same way in all three
#: writer and classifier prompts.
_MOMENTS = {
    "at_task_start":
        "the reader has just received the task and the first description "
        "of the room and has not acted yet",
    "after_first_environment_check":
        "the reader has looked around, checked its inventory or opened its "
        "first receptacle and now knows what is in the room and what it "
        "can reach",
    "when_command_fails":
        'a command was rejected with "Nothing happens.", or the same '
        "action keeps repeating without progress",
    "before_submitting":
        "the target object is ready and the reader is about to make the "
        "final placement and see whether the environment reports the task "
        "as solved",
    "at_any_step":
        "a basic rule that holds at every step, such as carrying only one "
        "object at a time. Use this only when none of the four moments "
        "above fits",
}

ALFWORLD_CONTEXT = LearningContext(name="alfworld", slots={
    "agent_kind": "household",
    "benchmark_name": "ALFWorld",
    "skill_description": "an ALFWorld skill",
    "task_tag": "ALFWorld",
    "action_noun": "command",
    "capability_section": (
        "If the segment fits none of the ALFWorld capability families "
        "below, use\n"
        '"general household procedure" and write only the meta-rules you '
        "can support.\n"
        "\n"
        "ALFWorld capability families:\n"
        "- locating an object by checking receptacles\n"
        "- picking up an object and placing it in a receptacle\n"
        "- heating, cooling or cleaning a held object\n"
        "- delivering two objects, one at a time\n"
        "- examining an object under a lamp\n"
        "- recovering from rejected or repeated actions\n"
        "- checking the held object and the target before the final "
        "placement"
    ),
    "capability_example": '"find an object by checking receptacles one by one"',
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
    "group_examples": '"object search" or "error recovery"',
    **trigger_timing_slots(
        phrases={
            "at_task_start": "At task start,",
            "after_first_environment_check": "After the first look around,",
            "when_command_fails": "When an action is rejected or repeats,",
            "before_submitting": "Before the final placement,",
            "at_any_step": "At any step,",
        },
        moments=_MOMENTS,
        family_meanings=_MOMENTS,
        subtask_meanings=_MOMENTS,
    ),
})
