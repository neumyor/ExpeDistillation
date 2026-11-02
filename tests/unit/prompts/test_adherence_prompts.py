"""The two skill-use judge prompts: variables, escaping and neutral wording."""

import re
import string

import pytest

from expedistill.prompts import PROMPTS, get

EXPECTED = {
    "SKILL_APPLICABILITY_JUDGE": {
        "task_instruction", "skill_listing", "trajectory_view"},
    "SKILL_ADHERENCE_JUDGE": {"card_text", "trajectory_view", "n_steps"},
}

#: Words that tie a prompt to one benchmark; the judges read every
#: benchmark's trials with the same text.
BENCHMARK_WORDS = re.compile(
    r"terminal|shell|\bTB\b|alfworld|searchqa|harbor|tmux", re.I)


def _placeholders(template):
    return {field for _, field, _, _ in string.Formatter().parse(template)
            if field is not None}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_registered_with_exactly_the_documented_variables(name):
    assert name in PROMPTS
    assert _placeholders(get(name)) == EXPECTED[name]
    assert "\n" not in PROMPTS[name].description


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_renders_and_keeps_the_json_shape_literal(name):
    # A stray unescaped brace would raise here, and the escaped JSON example
    # must come out with single braces.
    rendered = get(name).format(**{var: "X" for var in EXPECTED[name]})
    assert '{"skills": [' in rendered or '{"steps": [' in rendered
    assert "{{" not in rendered


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_wording_is_benchmark_neutral(name):
    assert not BENCHMARK_WORDS.search(get(name))


def test_adherence_prompt_names_all_four_statuses():
    template = get("SKILL_ADHERENCE_JUDGE")
    for status in ("followed", "partial", "skipped", "not_reached"):
        assert f"- {status}:" in template


def test_applicability_prompt_hides_the_outcome():
    # The judge is told not to use how the trial ended; nothing in the
    # template takes the outcome as input.
    template = get("SKILL_APPLICABILITY_JUDGE")
    assert "Do not use how the trial ended" in " ".join(template.split())
    assert "outcome" not in _placeholders(template)
