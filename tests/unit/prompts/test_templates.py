"""Template-level tests: the 11 method prompts are registered, each template's
placeholders are exactly its documented variables, and every template renders."""

import string

from expedistill.prompts import PROMPTS, get

# name -> (owning block, documented variables)
EXPECTED = {
    "SEGMENT": ("G1", {"trajectory_view", "K"}),
    "SKILL_DISTILL": ("G2", {"label", "description", "steps_view", "success", "weak_profile"}),
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

DUMMY = "dummy"


def _placeholders(template):
    fields = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is not None:
            fields.add(field_name)
    return fields


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
