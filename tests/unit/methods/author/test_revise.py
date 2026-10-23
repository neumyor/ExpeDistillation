"""The shared P04/P06 writer: full rewrite vs trigger-only sub-mode."""

from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.author.revise import revise_one
from expedistill.methods.evidence.package import EvidencePackage
from tests.helpers.skills import make_skill

CONTENT = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports running.",
    "[Do not use when] The service already runs.",
])
REVISED = "\n".join([
    "[When to use] The service rejects connections after a restart.",
    "[Steps]",
    "1. Run the status command.",
    "2. Restart with the restart command.",
    "[Verify] The second status command reports running.",
    "[Do not use when] The service already runs.",
])
WEAK = "a weak model that needs short sentences"


def _package(**facts):
    skill = make_skill("traj/fake/task/strong/00/seg00", content=CONTENT,
                       summary="old one-liner")
    base = {
        "group_diagnosis": "the skill was not followed",
        "skill_problem": "the trigger is too vague",
        "failure_view": "step 0: did something else",
        "skill_usage": "read 1/2",
        "sibling_descriptions": "other: do another thing",
        "task_instruction": "restart the service",
        "first_actions": "ls\ncat file",
    }
    base.update(facts)
    entries = [{"kind": kind, "text": text}
               for kind, text in base.items() if text is not None]
    return EvidencePackage(target=skill.skill_id, facts=entries,
                           old_skill=skill)


def test_full_revision_writes_a_new_version():
    package = _package(skill_read_attempts="1",
                       skill_relevance="relevant",
                       skill_application="not_applied")
    handle = make_fake_handle("rewriter", [json_script({
        "skill_text": REVISED, "failure_category": "missing_step",
        "revision_reason": "spelled out the steps",
        "summary": "a sharper one-liner"})])

    outcome = revise_one(package, weak_profile=WEAK, handle=handle)

    assert outcome.outcome == "revised"
    assert outcome.content == REVISED
    assert outcome.summary == "a sharper one-liner"
    assert "the trigger is too vague" in handle.client.calls[0]["user"]


def test_trigger_only_when_relevant_but_never_read():
    package = _package(skill_read_attempts="0",
                       skill_relevance="relevant",
                       skill_application="unclear")
    handle = make_fake_handle("rewriter", [json_script({
        "summary": "a sharper trigger",
        "when_to_use": "At task start, when the service rejects connections.",
        "reason": "the old trigger was too vague"})])

    outcome = revise_one(package, weak_profile=WEAK, handle=handle)

    assert outcome.outcome == "retriggered"
    assert "At task start, when the service rejects connections." \
        in outcome.content
    assert "[Steps]" in outcome.content
    assert "1. Check the service status." in outcome.content
    assert outcome.summary == "a sharper trigger"


def test_identical_rewrite_is_unchanged():
    package = _package(skill_read_attempts="1",
                       skill_relevance="relevant",
                       skill_application="applied")
    handle = make_fake_handle("rewriter", [json_script({
        "skill_text": CONTENT, "failure_category": "other",
        "revision_reason": "nothing to change", "summary": "same"})])

    outcome = revise_one(package, weak_profile=WEAK, handle=handle)

    assert outcome.outcome == "unchanged"
    assert outcome.content is None
