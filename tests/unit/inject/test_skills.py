"""Unit tests for D9 skill injection and D10 bank-length accounting."""
from expedistill.core.models import Skill
from expedistill.inject.skills import (
    bank_reference,
    count_bank_tokens,
    estimate_tokens,
    format_skills_block,
    skill_ref,
)


def make_skill(skill_id, content, version=1, group=None):
    return Skill(
        skill_id=skill_id,
        content=content,
        subtask_id=f"subtask-of-{skill_id}",
        version=version,
        group=group,
    )


def as_dict(skill):
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content": skill.content,
        "group": skill.group,
    }


# ---------------------------------------------------------------------------
# format_skills_block
# ---------------------------------------------------------------------------

def test_empty_skill_list_is_bare_empty_string():
    # A bare run inserts nothing: never a header with no bullets.
    assert format_skills_block([]) == ""


def test_single_ungrouped_skill():
    skill = make_skill("s1", "Check the config before restarting.")
    assert format_skills_block([skill]) == (
        "## Skill rules\n"
        "- Check the config before restarting."
    )


def test_groups_are_alphabetical_and_ungrouped_last():
    skills = [
        make_skill("s1", "first", group="zeta"),
        make_skill("s2", "second", group=None),
        make_skill("s3", "third", group="alpha"),
        make_skill("s4", "fourth", group="zeta"),
    ]
    block = format_skills_block(skills)
    assert block == (
        "## Skill rules\n"
        "### alpha\n"
        "- third\n"
        "### zeta\n"
        "- first\n"
        "- fourth\n"
        "- second"
    )


def test_input_order_is_stable_within_a_group():
    skills = [
        make_skill("s1", "a", group="g"),
        make_skill("s2", "b", group="g"),
        make_skill("s3", "c", group="g"),
    ]
    assert format_skills_block(skills).endswith("- a\n- b\n- c")


def test_all_grouped_skills_have_no_ungrouped_tail():
    skills = [make_skill("s1", "a", group="g")]
    assert format_skills_block(skills) == "## Skill rules\n### g\n- a"


def test_groups_override_wins_and_none_un_groups():
    skills = [
        make_skill("s1", "a", group="own"),
        make_skill("s2", "b", group="own"),
    ]
    block = format_skills_block(skills, groups={"s1": "override", "s2": None})
    assert block == "## Skill rules\n### override\n- a\n- b"


def test_blank_group_is_treated_as_ungrouped():
    skill = make_skill("s1", "a", group="")
    assert format_skills_block([skill]) == "## Skill rules\n- a"


def test_internal_newlines_collapse_to_single_spaces():
    skill = make_skill("s1", "Step one.\n\nStep two.\n  Step three.\n")
    block = format_skills_block([skill])
    assert block.count("\n") == 1  # header + one single-line bullet
    assert block == "## Skill rules\n- Step one. Step two. Step three."


def test_dict_and_object_inputs_render_identically():
    skills = [
        make_skill("s1", "a", group="g"),
        make_skill("s2", "b"),
    ]
    assert (format_skills_block(skills)
            == format_skills_block([as_dict(s) for s in skills]))


def test_dict_input_without_optional_keys():
    # version defaults to 1, group to None: same defaults as Skill.
    assert format_skills_block([{"skill_id": "s1", "content": "a"}]) == (
        "## Skill rules\n- a"
    )


# ---------------------------------------------------------------------------
# estimate_tokens / count_bank_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_is_a_fourth_of_the_length_with_floor_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 3) == 1
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 100) == 25


def test_count_bank_tokens_empty_bank_costs_nothing():
    # 0, not estimate_tokens("") == 1: an empty bank spends no phantom token.
    assert count_bank_tokens([]) == 0


def test_count_bank_tokens_matches_block_estimate():
    skills = [
        make_skill("s1", "a" * 40, group="g"),
        make_skill("s2", "b" * 60),
    ]
    block = format_skills_block(skills)
    assert count_bank_tokens(skills) == estimate_tokens(block)
    assert count_bank_tokens(skills) == count_bank_tokens(
        skills, groups={"s1": "h"})
    assert count_bank_tokens(skills) > 0


# ---------------------------------------------------------------------------
# bank_reference / skill_ref
# ---------------------------------------------------------------------------

def test_bank_reference_format_and_stability():
    skills = [
        make_skill("s1", "a"),
        make_skill("s2", "b", version=3),
    ]
    ref = bank_reference(skills)
    assert ref.startswith("bank:")
    assert len(ref) == len("bank:") + 12
    assert all(c in "0123456789abcdef" for c in ref[len("bank:"):])
    assert bank_reference(skills) == ref  # deterministic


def test_bank_reference_is_sensitive_to_content_version_and_membership():
    base = [make_skill("s1", "a")]
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a changed")])
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a", version=2)])
    assert bank_reference(base) != bank_reference(
        [make_skill("s1", "a"), make_skill("s2", "b")])
    assert bank_reference(base) != bank_reference([])


def test_bank_reference_ignores_grouping():
    # The digest covers skills, not the rendered block: regrouping is free.
    assert bank_reference([make_skill("s1", "a", group="g")]) == \
        bank_reference([make_skill("s1", "a", group=None)])


def test_bank_reference_accepts_dicts():
    skills = [make_skill("s1", "a", version=2)]
    assert bank_reference(skills) == bank_reference([as_dict(s) for s in skills])


def test_skill_ref_format():
    assert skill_ref(make_skill("s1", "a")) == "s1@v1"
    assert skill_ref(make_skill("s1", "a", version=7)) == "s1@v7"
    assert skill_ref({"skill_id": "s1", "content": "a"}) == "s1@v1"
