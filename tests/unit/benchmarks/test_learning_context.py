"""The benchmark vocabulary of the learning prompts (prompts/learning_context.py).

Pins four things: the TB context renders the learning prompts to the
recorded wording (a fixture of the TB renderings); the other contexts leave
no TB vocabulary in any prompt; every context defines exactly the documented
slots and every slot is used; each plugin's prompt pack carries its
context.
"""

import json
import string
from pathlib import Path

import pytest

from expedistill.benchmarks.alfworld.prompt_pack import PROMPT_PACK as ALFWORLD_PACK
from expedistill.benchmarks.alfworld.plugin import ALFWorldPlugin
from expedistill.benchmarks.searchqa.prompt_pack import PROMPT_PACK as SEARCHQA_PACK
from expedistill.benchmarks.searchqa.plugin import SearchQAPlugin
from expedistill.benchmarks.tb.prompt_pack import PROMPT_PACK as TB_PACK
from expedistill.benchmarks.tb.plugin import TBPlugin
from expedistill.prompts import METHOD_PROMPT_NAMES, PROMPTS, get
from expedistill.prompts.learning_context import (
    GENERIC,
    LearningContext,
    slots_for,
)
from expedistill.prompts.profile import profile_section

ALFWORLD_CONTEXT = ALFWORLD_PACK.vocabulary
SEARCHQA_CONTEXT = SEARCHQA_PACK.vocabulary
TB_CONTEXT = TB_PACK.vocabulary

SNAPSHOT = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "prompts"
     / "learning_prompts_tb.json").read_text(encoding="utf-8"))

CONTEXTS = [GENERIC, TB_CONTEXT, ALFWORLD_CONTEXT, SEARCHQA_CONTEXT]

#: Words that must not reach a model outside the TB benchmark.
TB_VOCABULARY = ("Terminal Bench", "TB21", "terminal agent", "bash",
                 "keystroke", "mark_task_complete", "tmux")


def _placeholders(template):
    return {field for _, field, _, _ in string.Formatter().parse(template)
            if field is not None}


def _render(name, context, caller_value=None):
    """The template under ``context``; caller variables get a dummy or the
    placeholder itself (``caller_value`` None)."""
    template = get(name)
    slots = {**GENERIC.variables(), **context.variables()}
    return template.format(**{
        field: slots.get(field, caller_value or f"<{field}>")
        for field in _placeholders(template)})


def _normalized(text):
    return " ".join(text.split())


# -- TB renders the recorded wording -----------------------------------------

@pytest.mark.parametrize("name", sorted(SNAPSHOT["prompts"]))
def test_tb_context_renders_the_recorded_wording(name):
    # Line wrapping may differ (a slot value cannot know in which template
    # it ends up and where that template wrapped); the words may not.
    assert _normalized(_render(name, TB_CONTEXT)) == _normalized(
        SNAPSHOT["prompts"][name])


def test_the_neutral_profile_section_renders_the_tb_wording():
    kind = TB_CONTEXT.slots["agent_kind"]
    assert profile_section("skill_reader", "", kind) == (
        SNAPSHOT["no_profile_section"])


def test_the_snapshot_prompts_are_registered():
    assert set(SNAPSHOT["prompts"]) <= set(PROMPTS)


# -- the other contexts carry no TB vocabulary -------------------------------

@pytest.mark.parametrize("context", CONTEXTS[:1] + CONTEXTS[2:],
                         ids=lambda context: context.name)
def test_no_tb_vocabulary_outside_the_tb_context(context):
    for name in sorted(METHOD_PROMPT_NAMES):
        rendered = _render(name, context, caller_value="dummy").lower()
        for word in TB_VOCABULARY:
            assert word.lower() not in rendered, (context.name, name, word)
    for text in (profile_section("skill_reader", "", context.slots["agent_kind"]),
                 *context.variables().values()):
        for word in TB_VOCABULARY:
            assert word.lower() not in text.lower(), (context.name, word)


def test_tb_vocabulary_is_in_the_tb_context():
    # The guard above would pass vacuously if the words were never there.
    text = (_render("CARD_WRITE", TB_CONTEXT)
            + _render("REVISE_CONTENT", TB_CONTEXT))
    for word in ("Terminal Bench", "TB21", "terminal", "mark_task_complete"):
        assert word in text


# -- the slots ---------------------------------------------------------------

@pytest.mark.parametrize("context", CONTEXTS, ids=lambda context: context.name)
def test_every_context_defines_exactly_the_generic_slots(context):
    assert set(context.slots) == set(GENERIC.slots)


def test_every_slot_is_used():
    used = set()
    for spec in PROMPTS.values():
        used |= _placeholders(spec.template)
    assert set(GENERIC.slots) <= used


def test_every_slot_has_a_nonempty_value():
    for context in CONTEXTS:
        for slot, text in context.slots.items():
            assert text.strip(), (context.name, slot)


def test_slots_for_overlays_prompt_defaults_on_generic():
    merged = slots_for({"agent_kind": "household"})
    assert merged["agent_kind"] == "household"
    assert merged["task_tag"] == GENERIC.slots["task_tag"]
    assert slots_for({}) == {"protocol": "", **GENERIC.variables()}
    assert slots_for({"protocol": "the rules"})["protocol"] == "the rules"


def test_the_manifest_form_carries_every_slot():
    assert TB_CONTEXT.to_dict() == {
        "name": "tb", "slots": dict(TB_CONTEXT.slots)}


# -- the plugins declare their context ---------------------------------------

def test_each_plugin_declares_its_context():
    assert TBPlugin.prompt_pack.vocabulary is TB_CONTEXT
    assert ALFWorldPlugin.prompt_pack.vocabulary is ALFWORLD_CONTEXT
    assert SearchQAPlugin.prompt_pack.vocabulary is SEARCHQA_CONTEXT
    assert isinstance(GENERIC, LearningContext)


# -- the wording is grounded in the plugin prompts ---------------------------

def test_alfworld_commands_are_the_engine_vocabulary():
    # The unified execution prompt no longer enumerates commands: the
    # engine's own admissible list is shown at every step. The learning
    # prompts still need a stable command vocabulary, so it is pinned here.
    rules = ALFWORLD_CONTEXT.slots["executable_rules"]
    for command in ("go to", "take", "move", "examine", "inventory", "clean",
                    "heat", "cool"):
        assert command in rules


def test_searchqa_actions_come_from_the_task_prompt():
    from expedistill.benchmarks.searchqa.plugin import SEARCHQA_TASK_PROMPT
    rules = SEARCHQA_CONTEXT.slots["executable_rules"]
    for action in ("Search[<query>]", "Finish[<answer>]"):
        assert action in rules
        assert action in SEARCHQA_TASK_PROMPT.template
