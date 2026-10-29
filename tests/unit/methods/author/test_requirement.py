"""The requirement writer: one aggregated requirement -> one pending skill."""

from expedistill.composites.evidence_package import EvidencePackage
from expedistill.core.ids import make_skill_id, make_subtask_id
from expedistill.core.models import Skill, Subtask
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.author.requirement import write_requirement
from expedistill.methods.review.contracts import NewSkillRequirement


def _requirement():
    subtask = Subtask(
        subtask_id="traj/tb:t1/weak/00/seg00", task_id="tb:t1",
        trajectory_id="traj/tb:t1/weak/00", source="weak",
        trajectory_success=False, segment_index=0, step_range=(0, 2),
        label="recovery", description="recover a stuck service",
        success=False, context_summary="")
    return NewSkillRequirement(
        requirement_id="req/abc",
        title="recover a stuck service",
        description="teach the recovery procedure",
        trigger="When a command fails,",
        evidence=EvidencePackage(
            target="req/abc",
            facts=[{"kind": "requirement", "text": "recover"}],
            segments=[{"sheet_id": "s1", "step_range": [0, 2]}],
            source_experiences=[{
                "subtask": subtask,
                "steps_view": "step 0\naction: check\nobservation: error",
                "mode": "diagnose",
                "segment_position": "trial window steps 0 to 1",
            }]),
        source_sheet_ids=["s1"])


def test_writes_one_pending_skill_anchored_on_the_source_subtask():
    handle = make_fake_handle("author", [json_script({
        "summary": "recover a stopped service",
        "when_to_use": "When a command fails, check the service state.",
        "steps": ["check the service", "restart it", "verify the result"],
        "not_applicable_when": "the service is running normally",
    })])

    skill = write_requirement(
        _requirement(), weak_profile="be concrete",
        source_context="step 0\naction: check\nobservation: error",
        handle=handle)

    assert isinstance(skill, Skill)
    assert skill.skill_id == make_skill_id(make_subtask_id("req/abc", 0))
    assert skill.status == "pending"
    assert skill.version == 1
    assert skill.source_subtasks == ["traj/tb:t1/weak/00/seg00"]
    assert "When a command fails" in skill.content

    # The writer reads the resolved real steps it was given, not any
    # window text carried on the requirement's evidence.
    prompt = handle.client.calls[0]["user"]
    assert "action: check" in prompt
