"""Template-level tests: the 11 method prompts are registered, each template's
placeholders are exactly its documented variables, and every template renders.

Protocol v2 (decision section 3.11) also pins the structure contract from
both sides: the two G2 templates carry the JSON field names only (the
renderer in blocks/author.py adds the bracket section markers), while the
REWRITE/MERGE/SIMILARITY templates must reproduce the markers literally
(their outputs have to carry them back)."""

import re
import string

from expedistill.core.models import SKILL_MARKERS
from expedistill.blocks.author import SKILL_SECTION_FIELDS
from expedistill.prompts import PROMPTS, get

# name -> (owning block, documented variables)
EXPECTED = {
    "SEGMENT": ("G1", {"trajectory_view", "K"}),
    "SKILL_DISTILL": (
        "G2",
        {"label", "description", "steps_view", "segment_position",
         "profile_section"},
    ),
    "SKILL_DIAGNOSE": (
        "G2",
        {"label", "description", "steps_view", "segment_position",
         "context_summary", "profile_section"},
    ),
    "REWRITE": (
        "G6",
        {"skill_text", "question_text", "weak_answer", "error_description", "profile_section"},
    ),
    "EXAM_SCENE": (
        "G3",
        {"task_instruction", "prior_steps", "future_steps", "skill_content",
         "change_note"},
    ),
    "EXAM_TRIGGER": (
        "G3",
        {"scene_text", "skill_catalog", "max_skill_reads"},
    ),
    "EXAM_PLAN": ("G4", {"scene_text", "selection_reason", "selected_skills"}),
    "EXAM_JUDGE": (
        "G5",
        {"scene_text", "expected_behavior", "reference_answer",
         "skill_catalog", "selected_skills", "selection_reason", "plan"},
    ),
    "LEAK_AUDIT_JUDGE": ("D8", {"skill_text", "scene_text", "reference_answer"}),
    "SIMILARITY": ("G7", {"skills_listing"}),
    "MERGE": ("G8", {"mode", "skills_block"}),
    "GROUP": ("G9", {"skills_listing"}),
    "CLUSTER_TRIGGER_FAMILY": (
        "Cluster authoring",
        {"label", "description", "source", "success", "segment_position",
         "steps_view"},
    ),
    "CLUSTER_SUBTASKS": (
        "Cluster authoring",
        {"family", "family_definition", "cards"},
    ),
    "CLUSTER_SKILL": (
        "Cluster authoring",
        {"family", "family_trigger", "theme", "reason", "cards",
         "profile_section"},
    ),
}

ALL_MARKERS = set(SKILL_MARKERS)

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


def test_all_ten_core_prompts_registered():
    # Benchmark task prompts (one per plugin) join the same registry by
    # design (README 1.6), so this is a superset check, not an exact set.
    assert set(EXPECTED) <= set(PROMPTS)


def test_descriptions_are_one_line_and_name_the_owning_block():
    for name, (block, _) in EXPECTED.items():
        description = PROMPTS[name].description
        assert description
        assert "\n" not in description
        assert block in description, f"{name}: description must name block {block}"


def test_placeholders_are_exactly_the_documented_variables():
    for name, (_, variables) in EXPECTED.items():
        assert _placeholders(get(name)) == variables, name


def test_format_with_dummy_values_succeeds():
    # format() itself is the check: a stray unescaped brace raises ValueError,
    # an undocumented placeholder raises KeyError.
    for name, (_, variables) in EXPECTED.items():
        rendered = get(name).format(**{var: DUMMY for var in variables})
        assert rendered.strip()


def test_profile_section_off_is_neutral():
    from expedistill.prompts.profile import (
        NO_WEAK_PROFILE_SECTION,
        profile_section,
    )
    assert profile_section("HEADER", "") == NO_WEAK_PROFILE_SECTION
    assert profile_section("HEADER", None) == NO_WEAK_PROFILE_SECTION
    assert profile_section("HEADER", "a profile") == "HEADER\na profile"


def test_templates_are_nonempty():
    for name in EXPECTED:
        assert get(name).strip()


def test_retired_exam_prompt_names_are_gone():
    # The generalization exam (docs/decisions/generalization_exam_20261027.md)
    # replaces the single-question exam; nothing may still register the old
    # names, or a stale caller would silently keep the old protocol alive.
    for retired in ("EXAM_GEN", "ANSWER", "JUDGE"):
        assert retired not in PROMPTS


def test_exam_json_contracts_name_the_validated_fields():
    # Each "Output ONLY JSON" example must name exactly the fields its block
    # validates; drift surfaces as model_output_failed units at run time.
    assert _json_contract_keys(get("EXAM_SCENE")) == {
        "scene_text", "expected_behavior", "reference_answer"}
    assert _json_contract_keys(get("EXAM_TRIGGER")) == {"skills", "reason"}
    assert _json_contract_keys(get("EXAM_PLAN")) == {"plan"}
    assert _json_contract_keys(get("EXAM_JUDGE")) == {"verdict", "reason"}


def test_exam_scene_must_not_name_the_skill_or_the_exam():
    # The scene is what the examined reader sees before it looks at the
    # catalog: a scene that names the skill under examination hands over the
    # trigger answer.
    text = _normalized(get("EXAM_SCENE"))
    assert "must NOT name or allude to any skill" in text
    assert "the skill catalog" in text
    assert "from an exam or a trajectory" in text


def test_exam_judge_forbids_handing_over_the_fix():
    # The judge's reason is evidence for the review/rewrite path, so it may
    # say WHAT is wrong but never how to fix it -- otherwise the rewriter is
    # handed the fix instead of rephrasing the idea.
    text = _normalized(get("EXAM_JUDGE"))
    assert "say only WHAT is wrong with the plan" in text
    assert "Do not state or imply how to fix it" in text
    assert "what the plan should have said instead" in text
    assert '"verdict": "<success|failure>"' in text


# ---------------------------------------------------------------------------
# Protocol v2 structure contract (decision section 3.11)
# ---------------------------------------------------------------------------


def test_g2_templates_carry_no_section_markers():
    # The G2 prompts output JSON field names; the bracket section markers
    # are added downstream by blocks/author.py's renderer. A marker
    # literal in a G2 template would leak renderer structure into the model
    # output contract.
    for name in ("SKILL_DISTILL", "SKILL_DIAGNOSE"):
        for marker in ALL_MARKERS:
            assert marker not in get(name), (name, marker)


def test_g2_json_contract_fields_match_validate_skill_sections():
    # The "Output ONLY JSON with exactly these fields" examples must name
    # exactly the keys validate_skill_sections requires: a drift between
    # prompt example and validator would surface as model_output_failed
    # units, not as a loud wiring error. Schema v3 adds the non-section
    # summary field; since 2026-10-19 both modes share one field set.
    from expedistill.blocks.author import SKILL_SUMMARY_FIELD
    for name in ("SKILL_DISTILL", "SKILL_DIAGNOSE"):
        assert _json_contract_keys(get(name)) == set(
            SKILL_SECTION_FIELDS) | {SKILL_SUMMARY_FIELD}, name


#: The five trigger-timing phrases (docs/decisions/
#: p02_trigger_timing_and_length_20261023.md): each G2 card's when_to_use
#: opens with exactly one of them, so the prompts must spell all five.
TIMING_PHRASES = (
    '"At task start,"',
    '"After the first environment check,"',
    '"When a command fails, hangs, or repeats,"',
    '"Before submitting,"',
    '"At any step,"',
)


def test_g2_templates_name_the_five_timing_phrases_and_the_length_rule():
    for name in ("SKILL_DISTILL", "SKILL_DIAGNOSE"):
        text = _normalized(get(name))
        for phrase in TIMING_PHRASES:
            assert phrase in text, (name, phrase)
        assert ("Use this only when none of the four moments above fits."
                in text), name
        assert ("summary and when_to_use together should stay under about "
                "400 characters and must never go over 1,000.") in text, name
        assert "never cut a sentence off" in text, name
        assert "Do not put the trigger in the summary." in text, name


def test_rewrite_template_output_contract_carries_the_summary():
    # Schema v3: the REWRITE JSON example carries the fresh summary, so
    # a prompt edit that drops it fails here instead of surfacing as
    # model_output_failed units at run time. (The category vocabulary's
    # quoted names also match the key regex, hence the subset pin.)
    assert {"failure_category", "skill_text", "rewrite_reason",
            "summary"} <= _json_contract_keys(get("REWRITE"))


def test_rewrite_template_names_every_section_marker():
    # The rewritten skill_text must keep the original's three bracket
    # sections, so the REWRITE prompt has to name all four markers (each
    # contiguous so the model can copy it) and to demand line-start-
    # accurate reproduction: a bolded or preamble-shifted variant would
    # make has_skill_sections read False.
    template = get("REWRITE")
    for marker in ALL_MARKERS:
        assert marker in template, marker
    text = _normalized(template)
    assert ("Reproduce the bracketed section headers exactly as written, "
            "each on its own line.") in text
    # The old "Change the wording, structure, granularity" conflicted with
    # keeping the section structure; "structure" is gone.
    assert "wording, structure, granularity" not in text


def test_no_prompt_names_a_retired_marker():
    # The 2026-10-19 ruling: the failure-reference structure is gone, and
    # G7 no longer restricts groups to one skill kind.
    for name in PROMPTS:
        for marker in RETIRED_MARKERS:
            assert marker not in get(name), (name, marker)
    assert "same kind: procedure skills" not in _normalized(get("SIMILARITY"))


def test_merge_template_preserves_section_structure():
    # G8's merged skill must carry the three sections, and the section
    # headers must be reproduced exactly (line-start anchored markers).
    template = get("MERGE")
    for marker in ALL_MARKERS:
        assert marker in template, marker
    assert ("Reproduce the bracketed section headers exactly as written, "
            "each on its own line.") in _normalized(template)


def test_merge_template_puts_generalization_first_and_asks_for_json():
    # The 2026-10-19 ruling: generalization is G8's first requirement,
    # dedup distills a general lesson, conflict analyses the cause first,
    # and the output is JSON with the merged skill and its summary.
    text = _normalized(get("MERGE"))
    assert text.index("Generalization comes first.") < \
        text.index("MERGE MODE:")
    assert "Distill them into one general lesson." in text
    assert "First work out why they conflict" in text
    assert '"content":' in text and '"summary":' in text


def test_similarity_template_has_one_criterion_and_a_low_value_field():
    # The 2026-10-19 ruling: no "same trigger conditions" next to "judge
    # by literal meaning" -- one criterion (the behavior taught), and a
    # low_value output field.
    text = _normalized(get("SIMILARITY"))
    assert "same trigger conditions" not in text
    assert "judge the behavior they teach, not their wording" in text
    assert '"low_value": ["skill_id", ...]' in text
