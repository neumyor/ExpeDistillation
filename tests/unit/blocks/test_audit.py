"""D8 leak-audit unit tests: the judge block, scripted via llm.fake.

One call_model per audit. Validation follows the judge pattern of
blocks/exam.py: after call_model's JSON-repair budget, ``leaked`` must be a
JSON boolean and ``reason`` a non-empty string -- anything else is a
ModelOutputError raised after exactly one client call, never a guess.
"""

import pytest

from expedistill.blocks.audit import audit_leak
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle

QUESTION = 'The service prints "svc-token-9" and stops. What next?'
REFERENCE = 'Rotate the credential "svc-token-9" and restart the service.'
SKILL = "Rotate the printed credential, then restart the service."


def verdict(leaked, reason="names a concrete value from the exam"):
    return json_script({"leaked": leaked, "reason": reason})


def run_audit(script, skill=SKILL, question=QUESTION, reference=REFERENCE):
    handle = make_fake_handle("auditor", script)
    result = audit_leak(skill, question, reference, handle)
    return result, handle


def test_clean_skill_passes_with_reason():
    (leaked, reason), handle = run_audit(verdict(False))

    assert leaked is False
    assert reason == "names a concrete value from the exam"
    assert len(handle.client.calls) == 1


def test_leaky_skill_fails_with_reason():
    (leaked, reason), _ = run_audit(
        verdict(True, "the skill quotes the credential"))

    assert leaked is True
    assert reason == "the skill quotes the credential"


def test_prompt_carries_skill_question_and_reference():
    _, handle = run_audit(verdict(False))
    user = handle.client.calls[0]["user"]
    assert SKILL in user
    assert QUESTION in user
    assert REFERENCE in user


def test_prompt_states_the_leak_rule_and_the_carve_outs():
    # The false-positive classes that killed the mechanical audit are the
    # prompt's explicit non-leaks; the reason constraint (reused as G6
    # rewrite context) forbids restating the answer.
    _, handle = run_audit(verdict(False))
    user = handle.client.calls[0]["user"]
    assert "help on ANY similar task" in user
    assert "Protocol words" in user
    assert "action names" in user
    assert "environment feedback strings" in user
    assert "NEVER restate" in user


def test_leaked_must_be_a_json_boolean():
    for bad in ("yes", "true", 1, 0, None, [True]):
        with pytest.raises(ModelOutputError, match="leaked"):
            run_audit(json_script({"leaked": bad, "reason": "x"}))


def test_leaked_must_be_present():
    with pytest.raises(ModelOutputError, match="leaked"):
        run_audit(json_script({"reason": "x"}))


def test_reason_must_be_a_nonempty_string():
    for bad in ("", "   ", None, 7):
        with pytest.raises(ModelOutputError, match="reason"):
            run_audit(json_script({"leaked": False, "reason": bad}))


def test_reason_is_returned_stripped():
    (leaked, reason), _ = run_audit(
        json_script({"leaked": False, "reason": "  generic advice only  "}))
    assert reason == "generic advice only"


def test_non_object_json_rejected():
    with pytest.raises(ModelOutputError, match="JSON object"):
        run_audit(json_script([{"leaked": False, "reason": "x"}]))


def test_unparsable_json_raises_after_the_repair_budget():
    # call_model re-asks with REPAIR_INSTRUCTION; the budget is exhausted
    # before audit_leak's own validation is ever reached.
    with pytest.raises(ModelOutputError, match="not valid JSON"):
        run_audit(["not json"] * 4)
