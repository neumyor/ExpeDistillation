"""The benchmark vocabulary of the learning prompts (prompts/learning_context.py).

Pins four things: the TB context renders every learning prompt to the wording
the prompts carried before the vocabulary became slots (a fixture rendered
from the pre-change templates); the other contexts leave no TB vocabulary in
any prompt; every context defines exactly the documented slots and every slot
is used; each plugin declares its context.
"""

import json
import string
from pathlib import Path

import pytest

from expedistill.benchmarks.alfworld.learning import ALFWORLD_CONTEXT
from expedistill.benchmarks.alfworld.plugin import ALFWorldPlugin
from expedistill.benchmarks.searchqa.learning import SEARCHQA_CONTEXT
from expedistill.benchmarks.searchqa.plugin import SearchQAPlugin
from expedistill.benchmarks.tb.learning import TB_CONTEXT
from expedistill.benchmarks.tb.plugin import TBPlugin
from expedistill.prompts import METHOD_PROMPT_NAMES, PROMPTS, get
from expedistill.prompts.learning_context import (
    GENERIC,
    SLOT_DOCS,
    TRIGGER_FAMILY_KEYS,
    LearningContext,
    slots_for,
    trigger_timing_slots,
)
from expedistill.prompts.profile import profile_section

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


# -- TB renders the wording the prompts had before the slots ----------------

@pytest.mark.parametrize("name", sorted(SNAPSHOT["prompts"]))
def test_tb_context_renders_the_pre_slot_wording(name):
    # Line wrapping may differ (a slot value cannot know in which template
    # it ends up and where that template wrapped); the words may not.
    assert _normalized(_render(name, TB_CONTEXT)) == _normalized(
        SNAPSHOT["prompts"][name])


def test_the_neutral_profile_section_renders_the_tb_wording():
    kind = TB_CONTEXT.slots["agent_kind"]
    assert profile_section("HEADER", "", kind) == (
        SNAPSHOT["no_weak_profile_section"])


def test_the_tb_family_tables_are_the_pre_slot_ones():
    assert {key: TB_CONTEXT.slots[f"meaning_{key}"]
            for key in TRIGGER_FAMILY_KEYS} == SNAPSHOT["trigger_families"]
    assert {key: TB_CONTEXT.slots[f"phrase_{key}"]
            for key in TRIGGER_FAMILY_KEYS} == SNAPSHOT["trigger_phrases"]


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
    for text in (profile_section("HEADER", "", context.slots["agent_kind"]),
                 *context.variables().values()):
        for word in TB_VOCABULARY:
            assert word.lower() not in text.lower(), (context.name, word)


def test_tb_vocabulary_is_in_the_tb_context():
    # The guard above would pass vacuously if the words were never there.
    text = _render("SKILL_DISTILL", TB_CONTEXT)
    for word in ("Terminal Bench", "TB21", "terminal", "mark_task_complete"):
        assert word in text


# -- the slots ---------------------------------------------------------------

@pytest.mark.parametrize("context", CONTEXTS, ids=lambda context: context.name)
def test_every_context_defines_exactly_the_documented_slots(context):
    assert set(context.slots) == set(SLOT_DOCS)


def test_every_slot_is_used():
    # The phrase_ / meaning_ slots are read by the cluster authoring driver,
    # not by a template.
    used = set()
    for spec in PROMPTS.values():
        used |= _placeholders(spec.template)
    for key in TRIGGER_FAMILY_KEYS:
        used |= {f"phrase_{key}", f"meaning_{key}"}
    assert set(SLOT_DOCS) <= used


def test_every_slot_has_a_nonempty_value_and_documentation():
    for context in CONTEXTS:
        for slot, text in context.slots.items():
            assert text.strip(), (context.name, slot)
    for slot, doc in SLOT_DOCS.items():
        assert doc.strip(), slot


def test_slots_for_overlays_prompt_defaults_on_generic():
    merged = slots_for({"agent_kind": "household"})
    assert merged["agent_kind"] == "household"
    assert merged["task_tag"] == GENERIC.slots["task_tag"]
    assert slots_for({}) == GENERIC.variables()


def test_the_manifest_form_carries_every_slot():
    assert TB_CONTEXT.to_dict() == {
        "name": "tb", "slots": dict(TB_CONTEXT.slots)}


# -- trigger_timing_slots ----------------------------------------------------

def _table(prefix):
    return {key: f"{prefix} {key}" for key in TRIGGER_FAMILY_KEYS}


def test_trigger_timing_slots_build_the_menus():
    slots = trigger_timing_slots(
        phrases=_table("phrase"), moments=_table("moment"),
        family_meanings=_table("family"), subtask_meanings=_table("meaning"))
    assert slots["phrase_at_task_start"] == "phrase at_task_start"
    assert slots["meaning_at_any_step"] == "meaning at_any_step"
    menu = slots["timing_menu"].split("\n")
    assert menu[0] == '  "phrase at_task_start"'
    assert menu[1] == "      moment at_task_start;"
    assert menu[-1] == "      moment at_any_step."
    family_menu = slots["family_menu"].split("\n")
    assert family_menu[0] == '- "at_task_start": family at_task_start.'
    assert len(family_menu) == len(TRIGGER_FAMILY_KEYS)


def test_trigger_timing_slots_refuse_a_table_with_the_wrong_keys():
    tables = dict(phrases=_table("p"), moments=_table("m"),
                  family_meanings=_table("f"), subtask_meanings=_table("s"))
    tables["moments"] = {"at_task_start": "only one"}
    with pytest.raises(ValueError, match="moments"):
        trigger_timing_slots(**tables)


# -- the plugins declare their context ---------------------------------------

def test_each_plugin_declares_its_context():
    assert TBPlugin.learning_context is TB_CONTEXT
    assert ALFWorldPlugin.learning_context is ALFWORLD_CONTEXT
    assert SearchQAPlugin.learning_context is SEARCHQA_CONTEXT
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
    assert "Nothing happens." in ALFWORLD_CONTEXT.slots["timing_menu"]


def test_searchqa_actions_come_from_the_task_prompt():
    from expedistill.benchmarks.searchqa.plugin import SEARCHQA_TASK_PROMPT
    rules = SEARCHQA_CONTEXT.slots["executable_rules"]
    for action in ("Search[<query>]", "Finish[<answer>]"):
        assert action in rules
        assert action in SEARCHQA_TASK_PROMPT.template
