"""The P04/P06 attribution and revision model blocks."""

import json

import pytest

from expedistill.blocks.attribution import (
    SKILL_SUMMARY_FIELD,
    attribute_attempts,
    revise_from_attempts,
)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from tests.helpers.skills import make_skill

SKILL_A = make_skill("traj/fake/t1/strong/00/seg00",
                     content="[When to use] a\n[Steps]\n1. do\n"
                             "[Do not use when] c",
                     summary="first skill")
SKILL_B = make_skill("traj/fake/t1/strong/00/seg01",
                     content="[When to use] d\n[Steps]\n1. do\n"
                             "[Do not use when] f",
                     summary="second skill")


def _attribution_reply():
    return json_script({
        "skills": [
            {"skill_id": SKILL_A.skill_id,
             "trigger_context": "the task needs a restart",
             "behavior_after_trigger": "the model restarted the service",
             "improvement": "name the exact service"},
            {"skill_id": SKILL_B.skill_id,
             "trigger_context": "the log was unreadable",
             "behavior_after_trigger": "the model inspected the log",
             "improvement": ""},
        ]})


def _call_attribution(handle, skills=(SKILL_A, SKILL_B),
                      skill_aliases=None, validation_attempts=1):
    return attribute_attempts(
        task_instruction="restart the service",
        attempt=1,
        outcome="failure",
        window_kind="invocation",
        step_start=0,
        step_end=3,
        trajectory_window="step 0\ncat SKILL.md",
        skills=list(skills),
        weak_profile="weak profile",
        handle=handle,
        skill_aliases=skill_aliases,
        validation_attempts=validation_attempts)


def test_attribution_reports_every_skill_once():
    handle = make_fake_handle("author", [_attribution_reply()])

    result = _call_attribution(handle)

    assert result[SKILL_A.skill_id]["trigger_context"] == (
        "the task needs a restart")
    assert result[SKILL_B.skill_id]["improvement"] == ""
    assert len(handle.client.calls) == 1
    prompt = handle.client.calls[0]["user"]
    assert SKILL_A.skill_id in prompt
    assert "the task needs a restart" not in prompt


def test_attribution_rejects_missing_skill():
    handle = make_fake_handle("author", [json_script({
        "skills": [{"skill_id": SKILL_A.skill_id,
                    "trigger_context": "x",
                    "behavior_after_trigger": "y",
                    "improvement": ""}]})])
    with pytest.raises(ModelOutputError, match="missing skills"):
        _call_attribution(handle)


def test_attribution_maps_folder_alias_and_ignores_other_windows():
    alias = "traj-fake-t1-strong-00-seg00-skill"
    handle = make_fake_handle("author", [json_script({
        "skills": [
            {"skill_id": alias,
             "trigger_context": "a", "behavior_after_trigger": "b",
             "improvement": ""},
            {"skill_id": SKILL_B.skill_id,
             "trigger_context": "c", "behavior_after_trigger": "d",
             "improvement": ""},
            {"skill_id": "traj/fake/t1/strong/00/seg09/skill",
             "trigger_context": "other window", "behavior_after_trigger": "x",
             "improvement": ""},
        ]})])

    result = _call_attribution(
        handle, skill_aliases={alias: SKILL_A.skill_id})

    assert set(result) == {SKILL_A.skill_id, SKILL_B.skill_id}
    assert result[SKILL_A.skill_id]["trigger_context"] == "a"
    assert result[SKILL_B.skill_id]["trigger_context"] == "c"


def test_attribution_ignores_a_valid_id_outside_the_window():
    handle = make_fake_handle("author", [json_script({
        "skills": [
            {"skill_id": SKILL_A.skill_id,
             "trigger_context": "a", "behavior_after_trigger": "b",
             "improvement": ""},
            {"skill_id": SKILL_B.skill_id,
             "trigger_context": "c", "behavior_after_trigger": "d",
             "improvement": ""},
        ]})])

    result = _call_attribution(handle, skills=(SKILL_A,))

    assert set(result) == {SKILL_A.skill_id}


def test_attribution_retries_a_contract_violation():
    handle = make_fake_handle("author", [
        json_script({"skills": None}),
        _attribution_reply(),
    ])

    result = _call_attribution(handle, validation_attempts=2)

    assert set(result) == {SKILL_A.skill_id, SKILL_B.skill_id}
    assert len(handle.client.calls) == 2


def test_attribution_deduplicates_alias_and_full_id():
    alias = "traj-fake-t1-strong-00-seg00-skill"
    handle = make_fake_handle("author", [json_script({
        "skills": [
            {"skill_id": SKILL_A.skill_id,
             "trigger_context": "a", "behavior_after_trigger": "b",
             "improvement": ""},
            {"skill_id": alias,
             "trigger_context": "duplicate", "behavior_after_trigger": "x",
             "improvement": ""},
            {"skill_id": SKILL_B.skill_id,
             "trigger_context": "c", "behavior_after_trigger": "d",
             "improvement": ""},
        ]})])

    result = _call_attribution(
        handle, skill_aliases={alias: SKILL_A.skill_id})

    assert result[SKILL_A.skill_id]["trigger_context"] == "a"


def test_attribution_rejects_empty_behavior():
    handle = make_fake_handle("author", [json_script({
        "skills": [
            {"skill_id": SKILL_A.skill_id, "trigger_context": "x",
             "behavior_after_trigger": "", "improvement": ""},
            {"skill_id": SKILL_B.skill_id, "trigger_context": "x",
             "behavior_after_trigger": "y", "improvement": ""}]})])
    with pytest.raises(ModelOutputError, match="behavior_after_trigger"):
        _call_attribution(handle)


def test_revision_returns_the_three_fields():
    handle = make_fake_handle("rewriter", [json_script({
        "skill_text": "[When to use] z\n[Steps]\n1. do\n"
                      "[Do not use when] x",
        "revision_reason": "made the trigger concrete",
        "summary": "restart a service"})])

    result = revise_from_attempts(
        skill_text=SKILL_A.content,
        attempt_evidence=json.dumps({"windows": []}),
        weak_profile="weak",
        handle=handle)

    assert result["summary"] == "restart a service"
    assert result["revision_reason"] == "made the trigger concrete"
    assert "[When to use] z" in result["skill_text"]


def test_revision_rejects_an_empty_summary():
    handle = make_fake_handle("rewriter", [json_script({
        "skill_text": "x", "revision_reason": "y", "summary": ""})])
    with pytest.raises(ModelOutputError, match=SKILL_SUMMARY_FIELD):
        revise_from_attempts(
            skill_text=SKILL_A.content, attempt_evidence="e",
            weak_profile="weak", handle=handle)
