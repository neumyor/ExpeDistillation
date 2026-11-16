"""Learn step 1: the trial diagnosis and its repairs."""

import json

import pytest

from expedistill.composites.sheet_facts import SheetFacts, sheet_facts
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from tests.helpers.fake_llm import json_script, make_fake_handle
from expedistill.methods.diagnose import REPAIRS, Finding, diagnose_trial
from tests.helpers.traces import execution_sheet


def _reply(**fields):
    payload = {"reason": "it skipped the check", "failing_step": 2,
               "scope": "subtask", "model_note": "skips checks",
               "playbook_note": ""}
    payload.update(fields)
    return json_script(payload)


def _baseline(n_steps=4):
    sheet = execution_sheet("tb:t1", "weak", 0, n_steps=n_steps)
    return sheet, sheet_facts(sheet, lambda skill_id: None)


def test_a_usable_reply_becomes_a_finding():
    sheet, facts = _baseline()
    handle = make_fake_handle(script=[_reply()])

    finding = diagnose_trial(sheet, facts, handle=handle)

    assert finding == Finding(sheet_id=sheet.sheet_id,
                              reason="it skipped the check", failing_step=2,
                              scope="subtask", model_note="skips checks",
                              playbook_note="")
    prompt = handle.client.calls[0]["user"]
    assert "an integer from 0 to 3" in prompt
    assert "step 0" in prompt and "step 3" in prompt
    # A bare baseline trial saw no catalog, no playbook and opened nothing.
    assert prompt.count("(none)") >= 3


def test_the_prompt_shows_what_a_practice_trial_saw_and_opened():
    folder = {"files": [{"skill_id": "type/001/skill", "version": 1,
                         "filename": "type-001-skill/SKILL.md"}],
              "catalog_text": "type-001-skill/SKILL.md | THE CATALOG LINE",
              "playbook_text": "THE PLAYBOOK", "bank_fingerprint": "bank:x"}
    sheet = execution_sheet(
        "tb:t1", "weak", 0, phase_label="p03:practice", skill_folder=folder,
        actions=["look", "ReadSkill[type-001-skill]", "act"])
    facts = SheetFacts(sheet_id=sheet.sheet_id, unit_id="u",
                       task_ref="tb:t1", trajectory_id="t", n_steps=3,
                       catalog="THE CATALOG LINE", playbook="THE PLAYBOOK",
                       opened={"type/001/skill": 1},
                       opened_cards={"type/001/skill": "THE CARD TEXT"})
    handle = make_fake_handle(script=[_reply(failing_step=2)])

    diagnose_trial(sheet, facts, handle=handle)

    prompt = handle.client.calls[0]["user"]
    for text in ("THE CATALOG LINE", "THE PLAYBOOK", "THE CARD TEXT",
                 "first read at step 1", "ReadSkill[type-001-skill]"):
        assert text in prompt


@pytest.mark.parametrize("bad, problem", [
    ({"failing_step": 4}, "failing_step 4"),
    ({"failing_step": -1}, "failing_step -1"),
    ({"failing_step": "2"}, "failing_step '2'"),
    ({"failing_step": True}, "failing_step True"),
    ({"scope": "subtasks"}, "scope 'subtasks'"),
    ({"reason": "  "}, "reason"),
])
def test_an_unusable_reply_is_sent_back_with_the_problem(bad, problem):
    sheet, facts = _baseline()
    handle = make_fake_handle(script=[_reply(**bad), _reply()])

    finding = diagnose_trial(sheet, facts, handle=handle)

    assert finding.failing_step == 2
    second = handle.client.calls[1]["user"]
    assert "Your previous answer gave " in second and problem in second


def test_the_repairs_are_bounded():
    sheet, facts = _baseline()
    replies = [_reply(scope="nowhere")] * (REPAIRS + 1)
    handle = make_fake_handle(script=list(replies))

    with pytest.raises(ModelOutputError, match="after 2 repairs"):
        diagnose_trial(sheet, facts, handle=handle)
    assert len(handle.client.calls) == REPAIRS + 1


def test_a_trial_without_steps_fails_without_a_call():
    sheet, facts = _baseline(n_steps=0)
    handle = make_fake_handle(script=[])

    with pytest.raises(ModelOutputError, match="no step"):
        diagnose_trial(sheet, facts, handle=handle)
    assert handle.client.calls == []


def test_the_facts_must_be_the_sheets_own():
    sheet, _ = _baseline()
    other, facts = _baseline()
    other_facts = SheetFacts(**{**facts.__dict__, "sheet_id": "elsewhere"})
    with pytest.raises(CodeBugError):
        diagnose_trial(sheet, other_facts, handle=make_fake_handle())


def test_a_finding_round_trips():
    finding = Finding(sheet_id="s", reason="r", failing_step=3,
                      scope="general", model_note="m", playbook_note="p")
    assert Finding.from_dict(json.loads(json.dumps(
        finding.to_dict()))) == finding
