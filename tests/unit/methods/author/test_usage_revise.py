"""The usage-aware group author: policy outcomes and trigger-only rewrites."""

from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import (
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.author import UsageReviseParams
from expedistill.methods.author.usage_revise import revise_group
from expedistill.methods.evidence.package import EvidencePackage
from tests.helpers.skills import make_skill

TASK_ID = make_task_id("tb", "t1")
WEAK_PROFILE = "A small model."
TASK_INSTRUCTION = "Fix the stuck service."

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


def _skill(history=None):
    subtask_id = make_subtask_id(
        make_trajectory_id(TASK_ID, "strong", 0), 0)
    return make_skill(subtask_id, MARKED, summary="Old summary.",
                      rewrite_history=history or [])


def _row():
    return TaskListRow(target_kind="group", target_id=TASK_ID,
                       action="revise", reason="no_gain",
                       basis_unit=f"{TASK_ID}:weak:0")


def _package(skill, **overrides):
    facts = {
        "delivery_status": "ok",
        "selection_status": "some_read",
        "skill_relevance": "relevant",
        "skill_application": "unclear",
        "skill_read_attempts": "0",
        "skill_usage": "read 0/2; first_step=None",
        "sibling_descriptions": "other: Another skill.",
        "first_actions": "ls\ncat filter.py",
        "failure_view": "step 1\naction: ls",
        "group_diagnosis": "the trigger did not select the skill",
    }
    facts.update(overrides)
    return EvidencePackage(
        target=skill.skill_id,
        facts=[{"kind": key, "text": value}
               for key, value in facts.items() if value is not None],
        old_skill=skill)


def _retrigger_script():
    return json_script({
        "summary": "Restart a stuck service after checking its status.",
        "when_to_use": "Use this when a service rejects connections.",
        "reason": "the old trigger was too vague",
    })


def _revision_script():
    return json_script({
        "failure_category": "missing_step",
        "skill_text": "\n".join([
            "[When to use] The service rejects connections.",
            "[Steps]",
            "1. Check the service status.",
            "2. Restart the service.",
            "[Verify] The status command reports running.",
            "[Do not use when] The status command already reports running.",
        ]),
        "revision_reason": "add the missing restart step",
        "summary": "Restart a stuck service.",
    })


def test_an_unread_relevant_skill_is_retriggered():
    skill = _skill()
    outcomes = revise_group(
        task_row=_row(), packages=[_package(skill)],
        rewriter_handle=make_fake_handle("rewriter", _retrigger_script()),
        weak_profile=WEAK_PROFILE, task_instruction=TASK_INSTRUCTION,
        revision_attempts=1, params=UsageReviseParams())

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome == "retriggered"
    assert "Restart a stuck service after checking" in outcome.summary
    assert "[When to use] Use this when a service rejects connections." \
        in outcome.content
    assert "[Steps]\n1. Check the service status." in outcome.content


def test_a_retriggered_skill_that_stays_unread_is_deleted():
    skill = _skill(history=[{"error_category": "wrong_trigger",
                             "reason": "previous retrigger"}])
    outcomes = revise_group(
        task_row=_row(), packages=[_package(skill)],
        rewriter_handle=make_fake_handle("rewriter", ""),
        weak_profile=WEAK_PROFILE, task_instruction=TASK_INSTRUCTION,
        revision_attempts=1, params=UsageReviseParams())

    assert outcomes[0].outcome == "deleted"


def test_a_delivery_failure_is_observed_without_a_model_call():
    skill = _skill()
    outcomes = revise_group(
        task_row=_row(),
        packages=[_package(skill, delivery_status="manifest_mismatch")],
        rewriter_handle=make_fake_handle("rewriter", ""),
        weak_profile=WEAK_PROFILE, task_instruction=TASK_INSTRUCTION,
        revision_attempts=1, params=UsageReviseParams())

    assert outcomes[0].outcome == "observed"


def test_a_read_skill_with_an_application_problem_is_revised():
    skill = _skill()
    outcomes = revise_group(
        task_row=_row(),
        packages=[_package(skill, skill_read_attempts="1",
                           skill_application="applied")],
        rewriter_handle=make_fake_handle("rewriter", _revision_script()),
        weak_profile=WEAK_PROFILE, task_instruction=TASK_INSTRUCTION,
        revision_attempts=1, params=UsageReviseParams())

    assert outcomes[0].outcome == "revised"
    assert "2. Restart the service." in outcomes[0].content
