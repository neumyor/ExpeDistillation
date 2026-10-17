"""Template-level tests: the 11 method prompts are registered, each template's
placeholders are exactly its documented variables, and every template renders.

Protocol v2 (decision section 3.11) also pins the structure contract from
both sides: the two G2 templates carry the JSON field names only (the
renderer in blocks/author.py adds the bracket section markers), while the
REWRITE/MERGE/SIMILARITY templates must reproduce the markers literally
(their outputs have to carry them back)."""

import re
import string

from expedistill.core.models import FAILURE_REFERENCE_MARKERS, PROCEDURE_MARKERS
from expedistill.blocks.author import SKILL_SECTION_FIELDS
from expedistill.prompts import PROMPTS, get

# name -> (owning block, documented variables)
EXPECTED = {
    "SEGMENT": ("G1", {"trajectory_view", "K"}),
    "SKILL_DISTILL": ("G2", {"label", "description", "steps_view", "weak_profile"}),
    "SKILL_DIAGNOSE": (
        "G2",
        {"label", "description", "steps_view", "context_summary", "weak_profile"},
    ),
    "REWRITE": (
        "G6",
        {"skill_text", "question_text", "weak_answer", "error_description", "weak_profile"},
    ),
    "EXAM_GEN": ("G3", {"label", "description", "steps_view"}),
    "ANSWER": ("G4", {"question_text", "skills_block"}),
    "JUDGE": ("G5", {"question_text", "reference_answer", "weak_answer"}),
    "LEAK_AUDIT_JUDGE": ("D8", {"skill_text", "question_text", "reference_answer"}),
    "SIMILARITY": ("G7", {"skills_listing"}),
    "MERGE": ("G8", {"mode", "skills_block"}),
    "GROUP": ("G9", {"skills_listing"}),
}

ALL_MARKERS = set(PROCEDURE_MARKERS) | set(FAILURE_REFERENCE_MARKERS)

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


def test_answer_template_renders_with_empty_skills_block():
    rendered = get("ANSWER").format(question_text="q?", skills_block="")
    assert "q?" in rendered


def test_templates_are_nonempty():
    for name in EXPECTED:
        assert get(name).strip()


def test_judge_error_description_forbids_handing_over_the_fix():
    # G6 (rewrite) consumes error_description verbatim, so if the judge may
    # say or imply how to fix the answer, "rewrite changes wording, not the
    # idea" loses its independence. The JUDGE template must keep the
    # never-reveal-the-answer rule AND explicitly forbid the fix direction.
    template = get("JUDGE")
    assert "Never restate, quote, or paraphrase" in template
    assert "how to fix" in template
    assert "should have said instead" in template
    # The JSON field description repeats both prohibitions.
    assert "never how to fix it" in template


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
    # exactly the keys validate_skill_sections requires for the mode: a
    # drift between prompt example and validator would surface as
    # model_output_failed units, not as a loud wiring error. Schema v3
    # adds the non-section summary field to every write contract.
    from expedistill.blocks.author import SKILL_SUMMARY_FIELD
    assert _json_contract_keys(get("SKILL_DISTILL")) == set(
        SKILL_SECTION_FIELDS["distill"]) | {SKILL_SUMMARY_FIELD}
    assert _json_contract_keys(get("SKILL_DIAGNOSE")) == set(
        SKILL_SECTION_FIELDS["diagnose"]) | {SKILL_SUMMARY_FIELD}


def test_rewrite_template_output_contract_carries_the_summary():
    # Schema v3: the REWRITE JSON example carries the fresh summary, so
    # a prompt edit that drops it fails here instead of surfacing as
    # model_output_failed units at run time. (The category vocabulary's
    # quoted names also match the key regex, hence the subset pin.)
    assert {"failure_category", "skill_text", "rewrite_reason",
            "summary"} <= _json_contract_keys(get("REWRITE"))


def test_rewrite_template_names_every_section_marker():
    # The rewritten skill_text must keep the original's four bracket
    # sections, so the REWRITE prompt has to name all eight markers (both
    # kinds, each contiguous so the model can copy it) and to demand
    # line-start-accurate reproduction: a bolded or preamble-shifted
    # variant would make skill_markers read None.
    template = get("REWRITE")
    for marker in ALL_MARKERS:
        assert marker in template, marker
    text = _normalized(template)
    assert ("Reproduce the bracketed section headers exactly as written, "
            "each on its own line.") in text
    # The old "Change the wording, structure, granularity" conflicted with
    # keeping the section structure; "structure" is gone.
    assert "wording, structure, granularity" not in text


def test_similarity_template_groups_by_skill_kind():
    # G7 must group/pair procedure skills with procedure skills and failure
    # references with failure references, in similar_groups AND in conflicts
    # (a cross-kind pair has no legal merge product).
    template = get("SIMILARITY")
    assert "'[When to use]' section header" in template
    assert "'[Trigger]' section header" in template
    assert "in similar_groups AND in conflicts" in _normalized(template)


def test_merge_template_preserves_section_structure():
    # G8's merged skill must carry the same four sections, and the section
    # headers must be reproduced exactly (line-start anchored markers).
    text = _normalized(get("MERGE"))
    assert ("Preserve the bracketed section structure of the source skills: "
            "the merged skill carries the same four sections, merged "
            "section by section.") in text
    assert ("Reproduce the bracketed section headers exactly as written, "
            "each on its own line.") in text
