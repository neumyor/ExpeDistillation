"""Unit tests for the G2/G3/G6 authoring blocks (llm.fake only, no disk IO).

Wiring is asserted exactly: the user message sent to the client must equal the
named prompt template rendered with the expected variables, so a variable that
is wired to the wrong field (e.g. context_summary leaking into distill mode)
fails loudly. None of these tests touch tmp_path or run_dir: the blocks must
not write anything anywhere.
"""

import pytest

from expedistill.core.ids import (
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Subtask
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts.registry import get as get_prompt
from expedistill.blocks.author import gen_exam, rewrite_skill, write_skill

STEPS_VIEW = "step 0: read config\nstep 1: restart the service"
WEAK_PROFILE = "A small model; known to skip intermediate steps."


def make_subtask(**overrides):
    task_id = make_task_id("tb", "task-42")
    trajectory_id = make_trajectory_id(task_id, "strong", 0)
    defaults = {
        "subtask_id": make_subtask_id(trajectory_id, 1),
        "task_id": task_id,
        "trajectory_id": trajectory_id,
        "source": "strong",
        "trajectory_success": True,
        "segment_index": 1,
        "step_range": (2, 4),
        "label": "Authenticate",
        "description": "Log in to the service with the stored credentials",
        "success": True,
        "context_summary": "The agent located the config file in step 0.",
    }
    defaults.update(overrides)
    return Subtask(**defaults)


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def make_skill(subtask):
    return Skill(
        skill_id=make_skill_id(subtask.subtask_id),
        content="Check the config exists before reading it.",
        subtask_id=subtask.subtask_id,
    )


# ---------------------------------------------------------------------------
# G2 write_skill
# ---------------------------------------------------------------------------

def test_write_skill_distill_wiring_and_fields():
    subtask = make_subtask()
    handle = make_fake_handle(script="  Do X when Y. Then do Z.  ")

    skill = write_skill(
        subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle,
        usage=RecordingUsage(),
    )

    # Exactly one call, with the SKILL_DISTILL template rendered from the
    # subtask fields (and no context_summary in distill mode).
    assert len(handle.client.calls) == 1
    expected = get_prompt("SKILL_DISTILL").format(
        label=subtask.label,
        description=subtask.description,
        steps_view=STEPS_VIEW,
        success="true",
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected

    assert skill.skill_id == make_skill_id(subtask.subtask_id)
    assert skill.subtask_id == subtask.subtask_id
    assert skill.content == "Do X when Y. Then do Z."
    assert skill.version == 1
    assert skill.status == "pending"
    assert skill.rewrite_history == []
    assert skill.merged_from == []


def test_write_skill_diagnose_wiring_and_fields():
    subtask = make_subtask(
        source="weak",
        trajectory_success=False,
        success=False,
    )
    handle = make_fake_handle(script="lesson text")

    skill = write_skill(
        subtask, STEPS_VIEW, WEAK_PROFILE, "diagnose", handle
    )

    assert len(handle.client.calls) == 1
    expected = get_prompt("SKILL_DIAGNOSE").format(
        label=subtask.label,
        description=subtask.description,
        steps_view=STEPS_VIEW,
        context_summary=subtask.context_summary,
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected
    assert skill.content == "lesson text"


def test_write_skill_invalid_mode_raises_before_any_call():
    subtask = make_subtask()
    handle = make_fake_handle(script="unused")

    with pytest.raises(ValueError, match="distill"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "bogus", handle)

    assert handle.client.calls == []


def test_write_skill_passes_usage_through():
    subtask = make_subtask()
    usage = RecordingUsage()
    handle = make_fake_handle(script="s")

    write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle, usage=usage)

    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "SKILL_DISTILL"
    assert usage.records[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# G3 gen_exam
# ---------------------------------------------------------------------------

def test_gen_exam_wiring_and_fields():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        "question_text": "A service rejects your login. What do you check first?",
        "reference_answer": "Whether the credentials in the config are valid.",
    }))

    exam = gen_exam(subtask, STEPS_VIEW, handle)

    assert len(handle.client.calls) == 1
    expected = get_prompt("EXAM_GEN").format(
        label=subtask.label,
        description=subtask.description,
        steps_view=STEPS_VIEW,
    )
    assert handle.client.calls[0]["user"] == expected

    assert exam.exam_id == make_exam_id(subtask.subtask_id)
    assert exam.subtask_id == subtask.subtask_id
    assert exam.question_text == "A service rejects your login. What do you check first?"
    assert exam.reference_answer == "Whether the credentials in the config are valid."


def test_gen_exam_empty_question_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        "question_text": "",
        "reference_answer": "valid reference",
    }))

    with pytest.raises(ModelOutputError, match="question_text"):
        gen_exam(subtask, STEPS_VIEW, handle)


def test_gen_exam_missing_reference_answer_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({"question_text": "q"}))

    with pytest.raises(ModelOutputError, match="reference_answer"):
        gen_exam(subtask, STEPS_VIEW, handle)


def test_gen_exam_non_string_field_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        "question_text": 7,
        "reference_answer": "r",
    }))

    with pytest.raises(ModelOutputError, match="question_text"):
        gen_exam(subtask, STEPS_VIEW, handle)


# ---------------------------------------------------------------------------
# G6 rewrite_skill
# ---------------------------------------------------------------------------

def test_rewrite_skill_wiring_and_result():
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "missing_step",
        "skill_text": "  Do X. Then do Y explicitly.  ",
        "rewrite_reason": "Spelled out the skipped intermediate step.",
    }))

    result = rewrite_skill(
        skill,
        question_text="What should you do when X?",
        weak_answer="I would do X.",
        error_description="The answer skips the verification step.",
        weak_profile=WEAK_PROFILE,
        handle=handle,
    )

    assert len(handle.client.calls) == 1
    expected = get_prompt("REWRITE").format(
        skill_text=skill.content,
        question_text="What should you do when X?",
        weak_answer="I would do X.",
        error_description="The answer skips the verification step.",
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected

    assert result == {
        "content": "Do X. Then do Y explicitly.",
        "failure_category": "missing_step",
        "rewrite_reason": "Spelled out the skipped intermediate step.",
    }

    # The block must not mutate the Skill: composites own version bumps and
    # rewrite_history appends.
    assert skill.content == "Check the config exists before reading it."
    assert skill.version == 1
    assert skill.rewrite_history == []


@pytest.mark.parametrize("category", ["catastrophe", "", None, 3])
def test_rewrite_skill_invalid_category_raises(category):
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": category,
        "skill_text": "rewritten",
        "rewrite_reason": "reason",
    }))

    with pytest.raises(ModelOutputError, match="failure_category"):
        rewrite_skill(
            skill, "q", "a", "err", WEAK_PROFILE, handle
        )


def test_rewrite_skill_empty_skill_text_raises():
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "misread",
        "skill_text": "   ",
        "rewrite_reason": "reason",
    }))

    with pytest.raises(ModelOutputError, match="skill_text"):
        rewrite_skill(skill, "q", "a", "err", WEAK_PROFILE, handle)
