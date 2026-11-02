"""Benchmark vocabulary for the learning-side prompts (layer 1).

The prompts that write, rewrite, segment and review skills describe a
benchmark: what the agent is, what a task is called, which capability
families exist, what the final action looks like. Those words differ per
benchmark while the method (the passes, the output contracts, the length
budgets) does not, so each prompt carries them as ``str.format`` slots and
the benchmark plugin supplies the values.

``LearningContext.slots`` maps slot name to text. ``call_model`` fills every
slot a template uses from, in increasing priority: :data:`GENERIC`, the
handle's ``prompt_defaults`` (wired from the benchmark plugin when the
pipeline builds its handles), the caller's own variables. A plugin's
context therefore only has to list the slots it wants to differ from
GENERIC; the three benchmarks of the paper list all of them. Code that
sends text to a model outside ``call_model`` (the neutral profile section,
the cluster authoring driver) merges the same way through
:func:`slots_for`.

The context is part of the experiment protocol like the prompt text:
stage 0 writes it into the manifest (:meth:`LearningContext.to_dict`), so a
changed word is a new experiment.
"""

from dataclasses import dataclass, field
from typing import Dict, Mapping

__all__ = [
    "GENERIC",
    "SLOT_DOCS",
    "TRIGGER_FAMILY_KEYS",
    "LearningContext",
    "slots_for",
    "trigger_timing_slots",
]


@dataclass(frozen=True)
class LearningContext:
    """One benchmark's values for the learning prompts' vocabulary slots."""

    name: str
    slots: Mapping[str, str] = field(default_factory=dict)

    def variables(self) -> Dict[str, str]:
        """The slot values to merge into a prompt's variables."""
        return dict(self.slots)

    def to_dict(self) -> Dict[str, object]:
        """The manifest form: the name and every slot's text."""
        return {"name": self.name, "slots": dict(self.slots)}


#: The five trigger-timing families. The keys are internal (stored in the
#: trigger-family and cluster ledgers); only their phrases and meanings are
#: benchmark vocabulary.
TRIGGER_FAMILY_KEYS = (
    "at_task_start",
    "after_first_environment_check",
    "when_command_fails",
    "before_submitting",
    "at_any_step",
)

#: Every slot and where its text lands. A test pins that GENERIC and the
#: three benchmark contexts define exactly these slots and that every slot
#: is used by a template (or, for the phrase_ / meaning_ slots, by the
#: cluster authoring driver).
SLOT_DOCS: Mapping[str, str] = {
    "agent_kind":
        "Adjective naming the executor, in 'a weaker <kind> agent on "
        "<benchmark>', 'a <kind>-agent trajectory', 'a <kind> task', 'its "
        "own <kind> behavior' and the neutral no-profile section.",
    "benchmark_name":
        "Benchmark name after 'on' or 'from' (SEGMENT, and every prompt "
        "that introduces the weaker agent).",
    "skill_description":
        "Article-led noun phrase of REWRITE's 'This is <a ... skill>'.",
    "task_tag":
        "Short tag in 'a DIFFERENT <tag> task', 'a new <tag> task shows' "
        "and 'separate <tag> discipline nodes'.",
    "action_noun":
        "What the agent's steps are called: 'no <noun> output', 'did not "
        "execute <noun>s', '<noun> lines'.",
    "capability_section":
        "The 'fits none of the capability families' instruction followed "
        "by the family list (SKILL_DISTILL, SKILL_DIAGNOSE).",
    "capability_example":
        "Quoted example of a capability phrase (SKILL_DISTILL, pass 1).",
    "discipline_examples":
        "The checks to weave into verify and fallback parts: before "
        "acting, before retrying, before finishing.",
    "executable_rules":
        "Bullets of the 'make it executable' list that name the "
        "benchmark's artifacts and actions (placeholder examples, the "
        "form an action may take, scripts and code).",
    "observable_signs":
        "Leading items of the list of signs a reader can observe; the "
        "tail 'tools, required output' stays in the templates.",
    "segment_label_examples":
        "Quoted examples of a segment label (SEGMENT).",
    "setup_steps":
        "Sentence-initial list of steps SEGMENT folds into the nearest "
        "capability segment.",
    "group_examples":
        "Quoted examples of a capability group name (GROUP).",
    "timing_menu":
        "Block listing the five opening phrases with the moment of each "
        "(SKILL_DISTILL, SKILL_DIAGNOSE).",
    "family_menu":
        "Block listing the five family keys with their meaning "
        "(CLUSTER_TRIGGER_FAMILY).",
    **{f"phrase_{key}":
       f"Opening phrase of the '{key}' family (the hint sentences of "
       "SKILL_DISTILL and SKILL_DIAGNOSE, and the cluster authoring "
       "driver, which also requires a skill's when_to_use to start with it)."
       for key in TRIGGER_FAMILY_KEYS},
    **{f"meaning_{key}":
       f"Meaning of the '{key}' family (CLUSTER_SUBTASKS, through the "
       "cluster authoring driver)."
       for key in TRIGGER_FAMILY_KEYS},
}


def trigger_timing_slots(
    *,
    phrases: Mapping[str, str],
    moments: Mapping[str, str],
    family_meanings: Mapping[str, str],
    subtask_meanings: Mapping[str, str],
) -> Dict[str, str]:
    """The timing slots of one benchmark from four per-family tables.

    Every table is keyed by :data:`TRIGGER_FAMILY_KEYS`: the opening
    ``phrases``, the ``moments`` shown next to each phrase in the writer
    prompts, the ``family_meanings`` shown next to each key in the family
    classifier, and the ``subtask_meanings`` shown to the clustering call.
    The texts carry no closing punctuation; the menus add it.
    """
    for label, table in (("phrases", phrases), ("moments", moments),
                         ("family_meanings", family_meanings),
                         ("subtask_meanings", subtask_meanings)):
        if set(table) != set(TRIGGER_FAMILY_KEYS):
            raise ValueError(
                f"{label} must be keyed by exactly "
                f"{list(TRIGGER_FAMILY_KEYS)}, got {sorted(table)}")
    last = TRIGGER_FAMILY_KEYS[-1]
    slots: Dict[str, str] = {}
    for key in TRIGGER_FAMILY_KEYS:
        slots[f"phrase_{key}"] = phrases[key]
        slots[f"meaning_{key}"] = subtask_meanings[key]
    slots["timing_menu"] = "\n".join(
        f'  "{phrases[key]}"\n      {moments[key]}'
        f'{"." if key == last else ";"}'
        for key in TRIGGER_FAMILY_KEYS)
    slots["family_menu"] = "\n".join(
        f'- "{key}": {family_meanings[key]}.' for key in TRIGGER_FAMILY_KEYS)
    return slots


_GENERIC_MOMENTS = {
    "at_task_start":
        "the reader has just received the task and is reading its "
        "description",
    "after_first_environment_check":
        "the reader has made its first observation of the environment and "
        "knows what is available",
    "when_command_fails":
        "an action failed, got stuck, or the same action keeps repeating "
        "without progress",
    "before_submitting":
        "the work looks done and the reader is about to check the result "
        "and finish",
    "at_any_step":
        "a basic rule that holds at every step. Use this only when none of "
        "the four moments above fits",
}

#: The benchmark-neutral wording; every slot any learning prompt uses has
#: an entry here, so a prompt always renders even for a plugin that
#: declares no context of its own.
GENERIC = LearningContext(name="generic", slots={
    "agent_kind": "text-based",
    "benchmark_name": "the benchmark",
    "skill_description": "a benchmark skill",
    "task_tag": "benchmark",
    "action_noun": "action",
    "capability_section": (
        "If the segment fits none of the capability families below, use\n"
        '"general procedure" and write only the meta-rules you can '
        "support.\n"
        "\n"
        "Capability families:\n"
        "- understanding the task and its goal\n"
        "- gathering information from the environment\n"
        "- planning and carrying out the actions the task needs\n"
        "- recovering from errors and unexpected feedback\n"
        "- checking the result before finishing"
    ),
    "capability_example": '"recover from a rejected action"',
    "discipline_examples": (
        "check before acting, read the feedback before retrying, check the "
        "result before finishing."
    ),
    "executable_rules": (
        "- every task-specific value is a <placeholder> in angle brackets: "
        "names,\n"
        "  numbers, identifiers, constants, feedback strings, entities;\n"
        "- an action may appear only in a general form that works after "
        "substituting\n"
        "  the placeholders;"
    ),
    "observable_signs": "task wording, input structure, feedback text",
    "segment_label_examples": (
        '"gather the needed information", "recover from a rejected action"'
    ),
    "setup_steps": (
        "Orientation steps, one-off actions, and task-specific data "
        "handling"
    ),
    "group_examples": '"information gathering" or "error recovery"',
    **trigger_timing_slots(
        phrases={
            "at_task_start": "At task start,",
            "after_first_environment_check":
                "After the first look at the environment,",
            "when_command_fails": "When an action fails or repeats,",
            "before_submitting": "Before finishing,",
            "at_any_step": "At any step,",
        },
        moments=_GENERIC_MOMENTS,
        family_meanings=_GENERIC_MOMENTS,
        subtask_meanings=_GENERIC_MOMENTS,
    ),
})


def slots_for(prompt_defaults: Mapping[str, str]) -> Dict[str, str]:
    """GENERIC overlaid with a handle's ``prompt_defaults``.

    The same merge ``call_model`` applies (minus the caller's variables),
    for code that needs a slot's text outside a prompt template.
    """
    return {**GENERIC.variables(), **prompt_defaults}
