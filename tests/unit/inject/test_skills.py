"""Unit tests for D9 skill injection."""
from expedistill.core.models import Skill
from expedistill.inject.skill_text import SKILL_MARKERS, render_card
from expedistill.inject.skills import (
    bank_reference,
    estimate_tokens,
    format_skills_block,
)


def make_skill(skill_id, when_to_use, version=1, steps=("Do it.",)):
    return Skill(
        skill_id=skill_id,
        subtask_type=f"type-of-{skill_id}",
        source_subtasks=[f"subtask-of-{skill_id}"],
        summary=f"Skill {skill_id}.",
        when_to_use=when_to_use,
        steps=list(steps),
        not_applicable_when="never",
        version=version,
    )


def imported(skill_id, text):
    return {"skill_id": skill_id, "summary": f"Skill {skill_id}.",
            "verbatim_text": text}


# ---------------------------------------------------------------------------
# format_skills_block
# ---------------------------------------------------------------------------

def test_empty_skill_list_is_bare_empty_string():
    # A bare run inserts nothing: never a header with no bullets.
    assert format_skills_block([]) == ""


def test_single_skill_is_its_card_on_one_bullet():
    skill = make_skill("s1", "Before restarting.")
    assert format_skills_block([skill]) == (
        "## Skill rules\n"
        "- [When to use] Before restarting. [Steps] 1. Do it. "
        "[Do not use when] never"
    )


def test_input_order_is_kept():
    skills = [make_skill(name, name) for name in ("c", "a", "b")]
    lines = format_skills_block(skills).split("\n")[1:]
    assert [line.split("] ", 1)[1][0] for line in lines] == ["c", "a", "b"]


def test_internal_newlines_collapse_to_single_spaces():
    skill = make_skill("s1", "Step one.\n\nStep two.\n  Step three.\n")
    block = format_skills_block([skill])
    assert block.count("\n") == 1  # header + one single-line bullet
    assert "[When to use] Step one. Step two. Step three. [Steps]" in block


def test_multiline_heredoc_step_compresses_and_markers_survive():
    # The 2026-10-13 incident payload class: a multi-line heredoc step.
    # The bullet is ONE line with every marker literal and the command
    # text intact -- one skill, one line, nothing silently truncated.
    skill = make_skill("s1", "when a service reports an unexpected state",
                       steps=["run this exact block:\npython3 - <<'PY'\n"
                              "print('ok')\nPY"])
    block = format_skills_block([skill])
    assert block.count("\n") == 1
    for marker in SKILL_MARKERS:
        assert marker in block
    assert "python3 - <<'PY'" in block
    assert "print('ok')" in block


def test_dict_and_object_inputs_render_identically():
    skills = [make_skill("s1", "a"), make_skill("s2", "b")]
    assert (format_skills_block(skills)
            == format_skills_block([s.to_dict() for s in skills]))


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_is_a_fourth_of_the_length_with_floor_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 3) == 1
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 100) == 25


# ---------------------------------------------------------------------------
# bank_reference
# ---------------------------------------------------------------------------

def test_bank_reference_format_and_stability():
    skills = [make_skill("s1", "a"), make_skill("s2", "b", version=3)]
    ref = bank_reference(skills)
    assert ref.startswith("bank:")
    assert len(ref) == len("bank:") + 12
    assert all(c in "0123456789abcdef" for c in ref[len("bank:"):])
    assert bank_reference(skills) == ref  # deterministic


def test_bank_reference_is_sensitive_to_card_version_and_membership():
    base = [make_skill("s1", "a")]
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a changed")])
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a", steps=["Do something else."])])
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a", version=2)])
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a"), make_skill("s2", "b")])
    assert bank_reference(base) != bank_reference([])


def test_bank_reference_accepts_dicts():
    skills = [make_skill("s1", "a", version=2)]
    assert bank_reference(skills) == bank_reference(
        [s.to_dict() for s in skills])


# ---------------------------------------------------------------------------
# verbatim: skill documents from other methods reach the model intact
# ---------------------------------------------------------------------------

_DOCUMENT = "# Playbook\n\n## Step 1\n- check the fridge\n- open it\n\n## Step 2\nclose it"


def test_an_imported_skill_renders_its_document():
    assert render_card(imported("doc", _DOCUMENT)) == _DOCUMENT


def test_verbatim_keeps_the_document_layout_byte_for_byte():
    block = format_skills_block([imported("doc", _DOCUMENT)], verbatim=True)
    assert block == "## Skill rules\n\n" + _DOCUMENT


def test_the_default_still_flattens_the_same_document():
    block = format_skills_block([imported("doc", _DOCUMENT)])
    assert "\n\n" not in block.split("\n", 1)[1]
    assert block.splitlines()[1].startswith("- # Playbook ## Step 1")


def test_verbatim_separates_several_skills_by_one_blank_line():
    block = format_skills_block(
        [imported("a", "first\nline"), imported("b", "second")],
        verbatim=True)
    assert block == "## Skill rules\n\nfirst\nline\n\nsecond"


def test_an_empty_list_is_empty_in_both_modes():
    assert format_skills_block([], verbatim=True) == ""
    assert format_skills_block([]) == ""
