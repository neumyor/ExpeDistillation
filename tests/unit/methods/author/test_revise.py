"""The P04/P06 writer: new SkillAttemptEvidence only, no policy."""

from expedistill.composites.skill_attempt_evidence import (
    SkillAttemptEvidence,
    WindowEvidence,
)
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.author.revise import revise_one
from tests.helpers.skills import make_skill

CONTENT = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Do not use when] The service already runs.",
])
REVISED = "\n".join([
    "[When to use] The service rejects connections after a restart.",
    "[Steps]",
    "1. Run the status command.",
    "2. Restart with the restart command.",
    "[Do not use when] The service already runs.",
])
WEAK = "a weak model that needs short sentences"


def _evidence(skill, windows=None):
    return SkillAttemptEvidence(
        skill_id=skill.skill_id, skill_version=skill.version,
        windows=(windows if windows is not None else [
            WindowEvidence(task_id="tb:t1", attempt=0, success=False,
                           kind="invocation", step_start=0, step_end=3,
                           trigger_steps=[0],
                           trigger_context="the service rejects connections",
                           behavior_after_trigger="the model restarted it",
                           improvement="name the exact service")]))


def test_revision_writes_a_new_version():
    skill = make_skill("traj/tb:t1/strong/00/seg00", content=CONTENT)
    handle = make_fake_handle("rewriter", [json_script({
        "skill_text": REVISED,
        "revision_reason": "spelled out the steps",
        "summary": "a sharper one-liner"})])

    outcome = revise_one(skill, _evidence(skill), weak_profile=WEAK,
                         handle=handle)

    assert outcome.outcome == "revised"
    assert outcome.content == REVISED
    assert outcome.summary == "a sharper one-liner"
    assert "the service rejects connections" in handle.client.calls[0]["user"]


def test_no_windows_means_unchanged_without_a_call():
    skill = make_skill("traj/tb:t1/strong/00/seg00", content=CONTENT)
    handle = make_fake_handle("rewriter", [])

    outcome = revise_one(skill, _evidence(skill, windows=[]),
                         weak_profile=WEAK, handle=handle)

    assert outcome.outcome == "unchanged"
    assert handle.client.calls == []


def test_identical_rewrite_is_unchanged():
    skill = make_skill("traj/tb:t1/strong/00/seg00", content=CONTENT)
    handle = make_fake_handle("rewriter", [json_script({
        "skill_text": CONTENT, "revision_reason": "nothing",
        "summary": "same"})])

    outcome = revise_one(skill, _evidence(skill), weak_profile=WEAK,
                         handle=handle)

    assert outcome.outcome == "unchanged"


def test_a_rewrite_that_drops_sections_is_retried():
    skill = make_skill("traj/tb:t1/strong/00/seg00", content=CONTENT)
    handle = make_fake_handle("rewriter", [
        json_script({"skill_text": "plain prose",
                     "revision_reason": "bad", "summary": "bad"}),
        json_script({"skill_text": REVISED,
                     "revision_reason": "fixed", "summary": "good"})])

    outcome = revise_one(skill, _evidence(skill), weak_profile=WEAK,
                         handle=handle)

    assert outcome.outcome == "revised"
    assert len(handle.client.calls) == 2
