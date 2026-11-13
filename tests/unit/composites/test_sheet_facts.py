"""What the executor of one trial saw and opened (composites.sheet_facts)."""

import pytest

from expedistill.composites.sheet_facts import sheet_facts, skill_at_version
from expedistill.infra.reliability import CodeBugError
from expedistill.inject.skill_text import render_card
from tests.helpers.skills import make_skill
from tests.helpers.traces import execution_sheet


def _folder(*skills):
    return {"files": [{"skill_id": skill.skill_id, "version": skill.version,
                       "filename": skill.skill_id.replace("/", "-")
                       + "/SKILL.md"} for skill in skills],
            "catalog_text": "the catalog", "playbook_text": "the playbook",
            "bank_fingerprint": "bank:x"}


def _revised(skill):
    """``skill`` one content rewrite later."""
    old = {"steps": skill.steps,
           "not_applicable_when": skill.not_applicable_when}
    return make_skill(skill.subtask_type, skill.when_to_use,
                      steps=["A new first step.", "A new second step."],
                      not_applicable_when="never now", version=2,
                      rewrite_history=[{"side": "content", "old": old,
                                        "reason": "r"}])


def test_a_bare_trial_saw_nothing_and_opened_nothing():
    sheet = execution_sheet("tb:t1", "weak", 0, n_steps=3)
    facts = sheet_facts(sheet, lambda skill_id: None)
    assert (facts.catalog, facts.playbook) == ("", "")
    assert facts.opened == {} and facts.opened_cards == {}
    assert facts.n_steps == 3
    assert facts.trajectory_id == sheet.execution.artifacts["trajectory_id"]


def test_a_practice_trial_reports_the_first_read_of_each_card():
    one = make_skill("type/001", "first")
    two = make_skill("type/002", "second")
    sheet = execution_sheet(
        "tb:t1", "weak", 0, phase_label="p03:practice",
        skill_folder=_folder(one, two),
        actions=["look", "ReadSkill[type-002-skill]", "act",
                 "ReadSkill[type-002-skill]", "cat type-001-skill/SKILL.md"])
    stored = {one.skill_id: one, two.skill_id: two}

    facts = sheet_facts(sheet, stored.get)

    assert facts.opened == {two.skill_id: 1, one.skill_id: 4}
    assert facts.opened_cards[two.skill_id] == render_card(two)
    assert (facts.catalog, facts.playbook) == ("the catalog", "the playbook")


def test_an_opened_card_reads_as_the_delivered_version():
    delivered = make_skill("type/001", "first")
    sheet = execution_sheet(
        "tb:t1", "weak", 0, phase_label="p03:practice",
        skill_folder=_folder(delivered),
        actions=["ReadSkill[type-001-skill]", "act"])
    now = _revised(delivered)

    facts = sheet_facts(sheet, {now.skill_id: now}.get)

    assert facts.opened_cards[delivered.skill_id] == render_card(delivered)


def test_skill_at_version_undoes_the_newer_rewrites():
    first = make_skill("type/001", "first")
    later = _revised(first)
    assert skill_at_version(later, 1).to_dict() == first.to_dict()
    assert skill_at_version(later, 2).to_dict() == later.to_dict()
    with pytest.raises(CodeBugError):
        skill_at_version(later, 3)


def test_a_sheet_without_a_trajectory_id_is_refused():
    sheet = execution_sheet("tb:t1", "weak", 0)
    sheet.execution.artifacts = {}
    with pytest.raises(CodeBugError, match="trajectory id"):
        sheet_facts(sheet, lambda skill_id: None)
