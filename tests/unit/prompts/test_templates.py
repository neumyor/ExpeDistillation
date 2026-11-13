"""Template-level tests: the method prompts are registered, each template's
placeholders are exactly its documented caller variables plus the learning-
context slots it uses (prompts/learning_context.py), and every template
renders.

Every writing prompt (CARD_WRITE, the two rewrites) also pins its JSON
contract to the skill fields blocks/author.py's parsers read; the bracket
section markers only appear in the card renderer (inject/skill_text.py).
The diagnosis and the typing prompts pin the JSON their parsers read."""

import re
import string

import pytest

from expedistill.blocks.author import SKILL_SECTION_FIELDS, SKILL_SUMMARY_FIELD
from expedistill.inject.skill_text import SKILL_MARKERS
from expedistill.prompts import PROMPTS, get
from expedistill.prompts.learning_context import GENERIC, PROTOCOL_SLOT

# name -> (owning step, documented caller variables)
EXPECTED = {
    "SEGMENT": ("G1", {"trajectory_view", "K"}),
    "SUBTASK_TYPING": ("G2", {"type_table", "cards", "correction_note"}),
    "CARD_WRITE": (
        "G3",
        {"type_name", "entry_state", "strong_segments", "weak_segments",
         "playbook", "profile_section", "correction_note"},
    ),
    "CARDS_CORRECTION": ("Learn steps 2 and 5", {"problem"}),
    "TRIAL_FAILURE_ANALYSIS": (
        "Learn step 1",
        {"task_instruction", "playbook", "skill_catalog", "opened_skills",
         "trial_steps", "last_step", "correction_note"},
    ),
    "DIAGNOSIS_CORRECTION": ("Learn step 1", {"problem"}),
    "PROFILE_UPDATE": (
        "Learn step 4", {"old_profile", "profile_notes", "correction_note"}),
    "PLAYBOOK_UPDATE": (
        "Learn step 4",
        {"old_playbook", "playbook_notes", "facts", "profile_section",
         "correction_note"},
    ),
    "REVISE_TRIGGER": (
        "Learn step 5",
        {"current_summary", "current_when_to_use", "skill_catalog",
         "playbook", "evidence_steps", "diagnosis", "profile_section"},
    ),
    "REVISE_CONTENT": (
        "Learn step 5",
        {"skill_card", "playbook", "evidence_steps", "diagnosis",
         "profile_section"},
    ),
    "REVISION_DIAGNOSIS": (
        "Learn step 5", {"number", "failing_step", "reason"}),
}

#: name -> the learning-context slots the template uses, in addition to its
#: caller variables. A prompt that is not listed uses no slot.
EXPECTED_SLOTS = {
    "SEGMENT": {"benchmark_name", "setup_steps", "segment_label_examples"},
    "SUBTASK_TYPING": {"agent_kind", "benchmark_name", "observable_signs"},
    "CARD_WRITE": {
        "agent_kind", "benchmark_name", "task_tag", "observable_signs",
        "executable_rules", PROTOCOL_SLOT},
    "TRIAL_FAILURE_ANALYSIS": {
        "agent_kind", "benchmark_name", "action_noun", PROTOCOL_SLOT},
    "PROFILE_UPDATE": {"agent_kind", "benchmark_name", PROTOCOL_SLOT},
    "PLAYBOOK_UPDATE": {"agent_kind", "benchmark_name", PROTOCOL_SLOT},
    "REVISE_TRIGGER": {
        "agent_kind", "benchmark_name", "observable_signs", PROTOCOL_SLOT},
    "REVISE_CONTENT": {
        "agent_kind", "benchmark_name", "skill_description",
        "executable_rules", "discipline_examples", PROTOCOL_SLOT},
}

ALL_MARKERS = set(SKILL_MARKERS)

#: The four fields every full writing prompt's JSON example names.
SKILL_FIELDS = set(SKILL_SECTION_FIELDS) | {SKILL_SUMMARY_FIELD}

#: The retired failure-reference markers: no prompt may name them.
RETIRED_MARKERS = ("[Trigger]", "[Why it fails]", "[Do instead]")

DUMMY = "dummy"


def _placeholders(template):
    fields = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is not None:
            fields.add(field_name)
    return fields


def _json_contract_keys(template):
    """The quoted keys of a template's JSON example ("field": ...)."""
    return set(re.findall(r'"([a-z_]+)":', template))


def _normalized(template):
    """Whitespace-collapsed template text: pins wording, tolerates wrapping."""
    return " ".join(template.split())


def test_every_method_prompt_is_registered():
    # Benchmark task prompts (one per plugin) join the same registry by
    # design, so this is a superset check, not an exact set.
    assert set(EXPECTED) <= set(PROMPTS)


def test_descriptions_are_one_line_and_name_the_owning_block():
    for name, (block, _) in EXPECTED.items():
        description = PROMPTS[name].description
        assert description
        assert "\n" not in description
        assert block in description, f"{name}: description must name {block}"


def _render_generic(name):
    """The template filled with GENERIC's slots and dummy caller variables."""
    variables = EXPECTED[name][1]
    return get(name).format(
        **GENERIC.variables(), protocol=DUMMY,
        **{var: DUMMY for var in variables})


def test_placeholders_are_exactly_the_documented_variables():
    for name, (_, variables) in EXPECTED.items():
        expected = variables | EXPECTED_SLOTS.get(name, set())
        assert _placeholders(get(name)) == expected, name


def test_every_documented_slot_is_defined_by_generic():
    # A slot a template uses but GENERIC lacks would be a KeyError for any
    # plugin that declares no context of its own.
    for name, slots in EXPECTED_SLOTS.items():
        assert slots - {PROTOCOL_SLOT} <= set(GENERIC.slots), name


def test_format_with_dummy_values_succeeds():
    # format() itself is the check: a stray unescaped brace raises ValueError,
    # an undocumented placeholder raises KeyError.
    for name in EXPECTED:
        assert _render_generic(name).strip()


def test_profile_section_off_is_neutral():
    from expedistill.prompts.profile import profile_section
    neutral = profile_section("skill_reader", "")
    assert profile_section("skill_reader", None) == neutral
    assert profile_section("skill_reader", "  ") == neutral
    assert GENERIC.slots["agent_kind"] in neutral


def test_a_profile_is_followed_by_the_binding_line():
    from expedistill.prompts.profile import PROFILE_ROLES, profile_section
    section = profile_section("skill_reader", "a profile")
    assert section.startswith(
        PROFILE_ROLES["skill_reader"] + "\na profile\n\n")
    # Whatever the profile states (capability or execution context) binds
    # every node the writer produces.
    assert "information the profile says the reader will not have" in section


def test_the_profile_roles_are_the_two_readers():
    from expedistill.prompts.profile import PROFILE_ROLES
    assert set(PROFILE_ROLES) == {"skill_reader", "playbook_reader"}


def test_unknown_profile_role_is_rejected():
    from expedistill.prompts.profile import profile_section
    with pytest.raises(ValueError, match="unknown profile role"):
        profile_section("no_such_role", "a profile")


def test_neutral_profile_section_names_the_benchmarks_agent_kind():
    from expedistill.prompts.profile import PROFILE_ROLES, profile_section
    assert "generally capable household agent" in profile_section(
        "skill_reader", "", "household")
    # A profile text never carries the kind.
    assert profile_section("skill_reader", "a profile", "household").startswith(
        PROFILE_ROLES["skill_reader"] + "\na profile\n\n")


def test_templates_are_nonempty():
    for name in EXPECTED:
        assert get(name).strip()


def test_retired_prompt_names_are_gone():
    # The text exam, the curation and the trigger-family clustering are
    # gone; nothing may still register their prompts, or a stale caller
    # would silently keep the old protocol alive.
    for retired in ("EXAM_GEN", "ANSWER", "JUDGE", "EXAM_SCENE",
                    "EXAM_TRIGGER", "EXAM_PLAN", "EXAM_JUDGE", "SIMILARITY",
                    "MERGE", "CLUSTER_TRIGGER_FAMILY", "CLUSTER_SUBTASKS",
                    "CLUSTER_SKILL", "BASELINE_FAILURE_ANALYSIS",
                    "TRIAL_EXAM_ANALYSIS"):
        assert retired not in PROMPTS


def test_no_prompt_names_a_timing_slot():
    # Triggers state a subtask's entry state, never a moment in time.
    for name in PROMPTS:
        assert not {"timing_menu", "family_menu"} & _placeholders(get(name))
        assert not any(field.startswith(("phrase_", "meaning_"))
                       for field in _placeholders(get(name)))


# ---------------------------------------------------------------------------
# The diagnosis and the typing
# ---------------------------------------------------------------------------

def test_the_diagnosis_asks_for_the_failing_step_and_the_scope():
    assert _json_contract_keys(get("TRIAL_FAILURE_ANALYSIS")) == {
        "reason", "failing_step", "scope", "model_note", "playbook_note"}
    text = _normalized(get("TRIAL_FAILURE_ANALYSIS"))
    assert "an integer from 0 to {last_step}" in text
    assert '"subtask"' in text and '"general"' in text
    assert "opening a skill card is a step too" in text


def test_the_typing_reply_is_new_types_plus_one_assignment_per_segment():
    assert _json_contract_keys(get("SUBTASK_TYPING")) == {
        "new_types", "key", "name", "entry_state", "assignments",
        "subtask_id", "type"}
    text = _normalized(get("SUBTASK_TYPING"))
    assert "Put every SUBTASK_ID from the segments exactly once." in text
    assert "Never invent a SUBTASK_ID and never repeat one." in text
    assert "It is not a moment in time" in text


# ---------------------------------------------------------------------------
# The writing prompts' JSON contracts
# ---------------------------------------------------------------------------

WRITERS = ("CARD_WRITE", "REVISE_TRIGGER", "REVISE_CONTENT")


def test_writer_templates_carry_no_section_markers():
    # The writers output JSON field names; the bracket section markers are
    # added downstream by the card renderer.
    for name in WRITERS:
        for marker in ALL_MARKERS:
            assert marker not in get(name), (name, marker)


def test_the_card_writer_names_exactly_the_skill_fields():
    assert _json_contract_keys(get("CARD_WRITE")) == SKILL_FIELDS
    text = _normalized(get("CARD_WRITE"))
    assert "steps: 3 to 7 steps" in text
    assert "adds only what the playbook does not already give" in text
    assert "the subtask's observable entry state" in text


def test_each_rewrite_names_only_its_own_side():
    assert _json_contract_keys(get("REVISE_TRIGGER")) == {
        "summary", "when_to_use", "revision_reason"}
    assert _json_contract_keys(get("REVISE_CONTENT")) == {
        "steps", "not_applicable_when", "revision_reason"}


def test_the_rewrites_read_the_evidence_and_the_playbook():
    trigger = _normalized(get("REVISE_TRIGGER"))
    assert "never opened the card before the step" in trigger
    assert "observable entry state" in trigger
    content = _normalized(get("REVISE_CONTENT"))
    assert "opened it before the step where the trial went wrong" in content
    assert "never repeat a playbook rule" in content


def test_the_playbook_update_keeps_to_task_general_practice():
    text = _normalized(get("PLAYBOOK_UPDATE"))
    assert "Never write a step-by-step recipe" in text
    assert "open that card before acting" in text
    assert "the trigger-evidence counts" in text
    assert text.endswith("<playbook> (the whole playbook) </playbook>")


def test_no_prompt_names_a_retired_marker():
    for name in PROMPTS:
        for marker in RETIRED_MARKERS:
            assert marker not in get(name), (name, marker)
