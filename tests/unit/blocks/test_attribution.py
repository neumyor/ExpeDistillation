"""Unit tests for the G10/G11 live-run attribution blocks (llm.fake only).

Wiring is asserted exactly, test_author.py style: the user message sent to
the client must equal the named prompt template rendered with the expected
variables -- including the skills_listing the block derives from the Skill
objects -- so a variable wired to the wrong placeholder fails loudly. The
G10 contract branches (coverage, action vocabulary, problem requirement,
diagnosis) and the G11 contract branches (failure_category vocabulary,
non-empty skill_text / revision_reason) are each pinned by a ModelOutputError
test: a judge that breaks its contract is a terminal unit, never a guess
(blocks/exam.py's judge-validation pattern). The registration tests pin the
lazy-registration design: both prompt names are reachable through the block's
defensive import yet never enter the frozen METHOD_PROMPT_NAMES snapshot.
None of these tests touch tmp_path or run_dir: the blocks write nothing.
"""

import pytest

from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts import get as get_prompt
from expedistill.prompts import METHOD_PROMPT_NAMES, PROMPTS
from expedistill.blocks.attribution import (
    FAILURE_CATEGORIES,
    attribute_failure,
    revise_skill,
)
from expedistill.blocks.author import (
    FAILURE_CATEGORIES as AUTHOR_FAILURE_CATEGORIES,
)

TASK_INSTRUCTION = (
    "Task: fix-the-login-flow\n\nInstalled skills:\n(the group below)\n\n"
    "History:\n(start)"
)
BASELINE_SUMMARY = (
    "attempt 0: failure\nattempt 1: failure\nattempt 2: failure"
)
LIVE_VIEWS = (
    "attempt 0: failure\nstep 0\naction: run login-check\n"
    "observation: credentials rejected\n\n"
    "attempt 1: failure\nstep 0\naction: run login-check\n"
    "observation: credentials rejected"
)
WEAK_PROFILE = "A small model; known to skip intermediate steps."
ATTRIBUTION_REPORT = (
    "Group diagnosis: the skill fired on the wrong trigger in every "
    "attempt.\nThis skill's problem: the trigger names a concrete config "
    "file path instead of a recognizable situation."
)
FAILURE_VIEW = (
    "attempt 0: failure\nstep 0\naction: run login-check\n"
    "observation: credentials rejected"
)

SKILL_A_CONTENT = "\n".join([
    "[When to use] When the service rejects a login.",
    "[Steps]",
    "1. Check the credentials in the config file.",
    "2. Restart the service.",
    "[Verify] The service accepts the login.",
    "[Do not use when] The subtask is not about logging in.",
])
SKILL_B_CONTENT = "\n".join([
    "[When to use] When a config file must be edited safely.",
    "[Steps]",
    "1. Stop the service.",
    "2. Edit the config file.",
    "3. Start the service.",
    "[Verify] The service is running.",
    "[Do not use when] The config file is not writable.",
])
SKILL_C_CONTENT = "\n".join([
    "[When to use] When a restart is needed after a config change.",
    "[Steps]",
    "1. Restart the service.",
    "[Verify] The service log shows a clean start.",
    "[Do not use when] No config was changed.",
])

REVISED_CONTENT = "\n".join([
    "[When to use] When the service rejects a login.",
    "[Steps]",
    "1. Check the credentials in the config file.",
    "2. Restart the service.",
    "3. Retry the login once.",
    "[Verify] The service accepts the login.",
    "[Do not use when] The subtask is not about logging in.",
])


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def make_skill(ordinal: int, content: str) -> Skill:
    task_id = make_task_id("tb", "attribution-task")
    trajectory_id = make_trajectory_id(task_id, "strong", ordinal)
    subtask_id = make_subtask_id(trajectory_id, 1)
    return Skill(
        skill_id=make_skill_id(subtask_id),
        content=content,
        subtask_id=subtask_id,
    )


def make_group():
    return [
        make_skill(0, SKILL_A_CONTENT),
        make_skill(1, SKILL_B_CONTENT),
        make_skill(2, SKILL_C_CONTENT),
    ]


def valid_plan(skills):
    """The minimal legal G10 response for ``skills``: all keeps."""
    return {
        "diagnosis": "The skills never addressed the failing login check.",
        "skill_actions": [
            {"skill_id": skill.skill_id, "action": "keep", "problem": ""}
            for skill in skills
        ],
    }


# ---------------------------------------------------------------------------
# G10 attribute_failure
# ---------------------------------------------------------------------------

def test_attribute_failure_wiring_and_result():
    skills = make_group()
    diagnosis = ("The login skill fired on the wrong trigger and the weak "
                 "model skipped its verify step in every attempt.")
    revise_problem = ("The trigger names a concrete config file path the "
                      "model cannot recognize mid-task; phrase it as a "
                      "file-class check.")
    delete_problem = "The step contradicts the group's other skills."
    handle = make_fake_handle(script=json_script({
        "diagnosis": diagnosis,
        "skill_actions": [
            # A keep entry legally omits problem; it normalizes to "".
            {"skill_id": skills[0].skill_id, "action": "keep"},
            {"skill_id": skills[1].skill_id, "action": "revise",
             "problem": revise_problem},
            {"skill_id": skills[2].skill_id, "action": "delete",
             "problem": delete_problem},
        ],
    }))

    result = attribute_failure(
        skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
        WEAK_PROFILE, handle,
    )

    # Exactly one call, with the LIVE_RUN_ATTRIBUTION template rendered from
    # the declared variables; the skills listing is the block's deterministic
    # id-plus-content rendering of the group.
    assert len(handle.client.calls) == 1
    expected_listing = (
        f"skill_id: {skills[0].skill_id}\n{SKILL_A_CONTENT}\n\n"
        f"skill_id: {skills[1].skill_id}\n{SKILL_B_CONTENT}\n\n"
        f"skill_id: {skills[2].skill_id}\n{SKILL_C_CONTENT}"
    )
    expected = get_prompt("LIVE_RUN_ATTRIBUTION").format(
        task_instruction=TASK_INSTRUCTION,
        baseline_summary=BASELINE_SUMMARY,
        live_views=LIVE_VIEWS,
        skills_listing=expected_listing,
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected

    assert result == {
        "diagnosis": diagnosis,
        "skill_actions": [
            {"skill_id": skills[0].skill_id, "action": "keep",
             "problem": ""},
            {"skill_id": skills[1].skill_id, "action": "revise",
             "problem": revise_problem},
            {"skill_id": skills[2].skill_id, "action": "delete",
             "problem": delete_problem},
        ],
    }


@pytest.mark.parametrize("problem", [None, "", "   ", "not part of the problem"])
def test_attribute_failure_keep_problem_is_normalized(problem):
    # For keep, problem is optional metadata: missing or null normalizes to
    # "", anything else passes through verbatim. Only revise/delete demand
    # a non-empty one.
    skills = [make_skill(0, SKILL_A_CONTENT)]
    entry = {"skill_id": skills[0].skill_id, "action": "keep"}
    if problem is not None:
        entry["problem"] = problem
    handle = make_fake_handle(script=json_script({
        "diagnosis": "Not a skill problem.",
        "skill_actions": [entry],
    }))

    result = attribute_failure(
        skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
        WEAK_PROFILE, handle,
    )

    assert result["skill_actions"] == [
        {"skill_id": skills[0].skill_id, "action": "keep",
         "problem": "" if problem is None else problem},
    ]


def test_attribute_failure_empty_group_raises_before_any_call():
    # A group is non-empty by construction; an empty call is a caller bug
    # (ValueError, the write_skill precedent) and must not spend a call.
    handle = make_fake_handle(script=json_script({"diagnosis": "x",
                                                 "skill_actions": []}))

    with pytest.raises(ValueError, match="non-empty skill group"):
        attribute_failure(
            [], TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )

    assert handle.client.calls == []


def test_attribute_failure_missing_skill_rejected():
    # Coverage is set equality: an action left out is a broken plan.
    skills = make_group()
    plan = valid_plan(skills)
    plan["skill_actions"] = plan["skill_actions"][:-1]
    handle = make_fake_handle(script=json_script(plan))

    with pytest.raises(ModelOutputError, match="missing skill_ids"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


def test_attribute_failure_unknown_skill_rejected():
    # An id the group never contained is rejected, not silently dropped.
    skills = make_group()[:2]
    foreign = make_skill(9, SKILL_C_CONTENT)
    plan = valid_plan(skills)
    plan["skill_actions"].append(
        {"skill_id": foreign.skill_id, "action": "keep", "problem": ""})
    handle = make_fake_handle(script=json_script(plan))

    with pytest.raises(ModelOutputError, match="not in the group"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


def test_attribute_failure_duplicate_skill_rejected():
    # Exactly once: a repeated id is rejected even when nothing is missing.
    skills = make_group()[:2]
    plan = valid_plan(skills)
    plan["skill_actions"] = [
        dict(plan["skill_actions"][0]),
        dict(plan["skill_actions"][0]),
    ]
    handle = make_fake_handle(script=json_script(plan))

    with pytest.raises(ModelOutputError, match="exactly once"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


@pytest.mark.parametrize("action", ["revise", "delete"])
@pytest.mark.parametrize("problem", ["", "   ", None])
def test_attribute_failure_action_without_problem_rejected(action, problem):
    # revise/delete carry the revision instruction; without a concrete
    # problem the downstream G11 call would have nothing to work from.
    skills = [make_skill(0, SKILL_A_CONTENT)]
    entry = {"skill_id": skills[0].skill_id, "action": action}
    if problem is not None:
        entry["problem"] = problem
    handle = make_fake_handle(script=json_script({
        "diagnosis": "Something in the group misfired.",
        "skill_actions": [entry],
    }))

    with pytest.raises(ModelOutputError, match="non-empty 'problem'"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


@pytest.mark.parametrize(
    "key, value",
    [
        ("skill_id", 3),          # id must be a string
        ("skill_id", ""),         # id must be non-empty
        ("action", "destroy"),    # outside the closed vocabulary
        ("action", "KEEP"),       # vocabulary is case-sensitive
        ("problem", 3),           # problem must be a string, even for keep
    ],
)
def test_attribute_failure_bad_entry_fields_reject(key, value):
    skills = [make_skill(0, SKILL_A_CONTENT)]
    plan = valid_plan(skills)
    plan["skill_actions"][0][key] = value
    handle = make_fake_handle(script=json_script(plan))

    with pytest.raises(ModelOutputError):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


@pytest.mark.parametrize("diagnosis", ["", "   ", None, 7])
def test_attribute_failure_empty_diagnosis_rejected(diagnosis):
    # The diagnosis is what K6 stores in the attribution ledger; an empty
    # one means the model skipped step 1 of the prompt.
    skills = [make_skill(0, SKILL_A_CONTENT)]
    plan = valid_plan(skills)
    plan["diagnosis"] = diagnosis
    handle = make_fake_handle(script=json_script(plan))

    with pytest.raises(ModelOutputError, match="diagnosis"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


def test_attribute_failure_non_object_response_rejected():
    skills = [make_skill(0, SKILL_A_CONTENT)]
    handle = make_fake_handle(script=json_script(["not", "an", "object"]))

    with pytest.raises(ModelOutputError, match="JSON object"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


def test_attribute_failure_actions_not_a_list_rejected():
    skills = [make_skill(0, SKILL_A_CONTENT)]
    handle = make_fake_handle(script=json_script({
        "diagnosis": "plausible",
        "skill_actions": {"skill_id": skills[0].skill_id},
    }))

    with pytest.raises(ModelOutputError, match="must be a list"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


def test_attribute_failure_entry_not_an_object_rejected():
    skills = [make_skill(0, SKILL_A_CONTENT)]
    handle = make_fake_handle(script=json_script({
        "diagnosis": "plausible",
        "skill_actions": ["keep"],
    }))

    with pytest.raises(ModelOutputError, match="JSON object"):
        attribute_failure(
            skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
            WEAK_PROFILE, handle,
        )


def test_attribute_failure_passes_usage_through():
    skills = [make_skill(0, SKILL_A_CONTENT)]
    usage = RecordingUsage()
    handle = make_fake_handle(script=json_script(valid_plan(skills)))

    attribute_failure(
        skills, TASK_INSTRUCTION, BASELINE_SUMMARY, LIVE_VIEWS,
        WEAK_PROFILE, handle, usage=usage,
    )

    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "LIVE_RUN_ATTRIBUTION"
    assert usage.records[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# G11 revise_skill
# ---------------------------------------------------------------------------

def test_revise_skill_wiring_and_result():
    handle = make_fake_handle(script=json_script({
        "failure_category": "missing_step",
        "skill_text": f"  {REVISED_CONTENT}  ",
        "revision_reason": "Spelled out the skipped retry step.",
    }))

    result = revise_skill(
        SKILL_A_CONTENT, ATTRIBUTION_REPORT, FAILURE_VIEW,
        WEAK_PROFILE, handle,
    )

    assert len(handle.client.calls) == 1
    expected = get_prompt("LIVE_SKILL_REVISION").format(
        skill_text=SKILL_A_CONTENT,
        attribution_report=ATTRIBUTION_REPORT,
        failure_view=FAILURE_VIEW,
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected

    # The block returns the new version's fields only: version bumps and
    # rewrite_history appends are K6's bookkeeping, and the marker check
    # lives there too (blocks may not import each other).
    assert result == {
        "failure_category": "missing_step",
        "skill_text": REVISED_CONTENT,
        "revision_reason": "Spelled out the skipped retry step.",
    }


@pytest.mark.parametrize("category", ["catastrophe", "", None, 3])
def test_revise_skill_invalid_category_rejected(category):
    # Closed vocabulary, shared with G6: anything outside it is a format
    # error, never a guess (judge-validation pattern).
    handle = make_fake_handle(script=json_script({
        "failure_category": category,
        "skill_text": REVISED_CONTENT,
        "revision_reason": "reason",
    }))

    with pytest.raises(ModelOutputError, match="failure_category"):
        revise_skill(
            SKILL_A_CONTENT, ATTRIBUTION_REPORT, FAILURE_VIEW,
            WEAK_PROFILE, handle,
        )


@pytest.mark.parametrize("skill_text", ["", "   ", None, 7])
def test_revise_skill_empty_skill_text_rejected(skill_text):
    handle = make_fake_handle(script=json_script({
        "failure_category": "missing_step",
        "skill_text": skill_text,
        "revision_reason": "reason",
    }))

    with pytest.raises(ModelOutputError, match="skill_text"):
        revise_skill(
            SKILL_A_CONTENT, ATTRIBUTION_REPORT, FAILURE_VIEW,
            WEAK_PROFILE, handle,
        )


@pytest.mark.parametrize("reason", ["", "   ", None, 7])
def test_revise_skill_empty_revision_reason_rejected(reason):
    handle = make_fake_handle(script=json_script({
        "failure_category": "missing_step",
        "skill_text": REVISED_CONTENT,
        "revision_reason": reason,
    }))

    with pytest.raises(ModelOutputError, match="revision_reason"):
        revise_skill(
            SKILL_A_CONTENT, ATTRIBUTION_REPORT, FAILURE_VIEW,
            WEAK_PROFILE, handle,
        )


def test_revise_skill_non_object_response_rejected():
    handle = make_fake_handle(script=json_script(["not", "an", "object"]))

    with pytest.raises(ModelOutputError, match="JSON object"):
        revise_skill(
            SKILL_A_CONTENT, ATTRIBUTION_REPORT, FAILURE_VIEW,
            WEAK_PROFILE, handle,
        )


def test_revise_skill_passes_usage_through():
    usage = RecordingUsage()
    handle = make_fake_handle(script=json_script({
        "failure_category": "missing_step",
        "skill_text": REVISED_CONTENT,
        "revision_reason": "Spelled out the skipped retry step.",
    }))

    revise_skill(
        SKILL_A_CONTENT, ATTRIBUTION_REPORT, FAILURE_VIEW,
        WEAK_PROFILE, handle, usage=usage,
    )

    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "LIVE_SKILL_REVISION"
    assert usage.records[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Registration and the shared vocabulary
# ---------------------------------------------------------------------------

def test_defensive_import_registers_the_live_prompts():
    # The two names never enter prompts/__init__ (lazy registration keeps
    # the manifest's frozen METHOD_PROMPT_NAMES stable for in-flight exam
    # runs); importing the block is what registers them, so any caller that
    # bypasses stage 0 finds them instead of hitting PromptNotFoundError.
    import expedistill.blocks.attribution  # noqa: F401

    assert "LIVE_RUN_ATTRIBUTION" in PROMPTS
    assert "LIVE_SKILL_REVISION" in PROMPTS
    # The frozen method-name snapshot stays free of them: adding either to
    # prompts/__init__ would change every in-flight run's manifest
    # comparison and break resume.
    assert "LIVE_RUN_ATTRIBUTION" not in METHOD_PROMPT_NAMES
    assert "LIVE_SKILL_REVISION" not in METHOD_PROMPT_NAMES


def test_failure_categories_match_author_vocabulary():
    # The tuple is restated in blocks/attribution.py because blocks may not
    # import each other, but it is ONE contract with G6: this test fails
    # the moment the two drift apart, forcing a conscious prompt change on
    # both sides (a vocabulary change is a new experiment either way).
    assert FAILURE_CATEGORIES == AUTHOR_FAILURE_CATEGORIES
