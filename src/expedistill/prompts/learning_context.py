"""Benchmark vocabulary for the learning-side prompts (layer 1).

The prompts that write, rewrite, segment and review skills describe a
benchmark: what the agent is, what a task is called, which signs it can
observe, what its actions look like. Those words differ per
benchmark while the method (the passes, the output contracts, the length
budgets) does not, so each prompt carries them as ``str.format`` slots and
the benchmark plugin supplies the values.

``LearningContext.slots`` maps slot name to text. ``call_model`` fills every
slot a template uses from, in increasing priority: :data:`GENERIC`, the
handle's ``prompt_defaults`` (wired from the benchmark's prompt pack when the
pipeline builds its handles), the caller's own variables. A plugin's
context therefore only has to list the slots it wants to differ from
GENERIC; the three benchmarks of the paper list all of them. Code that
sends text to a model outside ``call_model`` (the neutral profile section)
merges the same way through :func:`slots_for`.

The context is part of the experiment protocol like the prompt text:
stage 0 writes it into the manifest (:meth:`LearningContext.to_dict`), so a
changed word is a new experiment.
"""

from dataclasses import dataclass, field
from typing import Dict, Mapping

__all__ = [
    "GENERIC",
    "PROTOCOL_SLOT",
    "LearningContext",
    "default_slots",
    "slots_for",
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


#: The benchmark-neutral wording; every slot any learning prompt uses has
#: an entry here, so a prompt always renders even for a plugin that
#: declares no context of its own.
GENERIC = LearningContext(name="generic", slots={
    "agent_kind": "text-based",
    "benchmark_name": "the benchmark",
    "skill_description": "a benchmark skill",
    "task_tag": "benchmark",
    "action_noun": "action",
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
})


#: The slot the prompt pack's environment protocol fills (wired into every
#: handle's ``prompt_defaults`` by the pipeline). It is not vocabulary: a
#: handle built without a pack renders it empty.
PROTOCOL_SLOT = "protocol"


def default_slots() -> Dict[str, str]:
    """The values every template slot falls back to: GENERIC's words and
    an empty protocol."""
    return {PROTOCOL_SLOT: "", **GENERIC.variables()}


def slots_for(prompt_defaults: Mapping[str, str]) -> Dict[str, str]:
    """The default slots overlaid with a handle's ``prompt_defaults``.

    The same merge ``call_model`` applies (minus the caller's variables),
    for code that needs a slot's text outside a prompt template.
    """
    return {**default_slots(), **prompt_defaults}
