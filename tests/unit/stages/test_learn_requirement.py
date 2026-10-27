"""The requirement landing: one skill per requirement, resumable."""

from types import SimpleNamespace

from expedistill.composites.evidence_package import EvidencePackage
from expedistill.core.models import Subtask
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.review.contracts import NewSkillRequirement
from expedistill.stages import learn_requirement


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
            target="req/abc", facts=[],
            segments=[{"sheet_id": "s1", "step_range": [0, 2]}],
            source_experiences=[{
                "subtask": subtask, "steps_view": "steps",
                "mode": "diagnose", "segment_position": "window",
            }]),
        source_sheet_ids=["s1"])


def test_writes_one_skill_and_skips_a_replayed_requirement(tmp_path):
    run_dir = tmp_path / "run"
    store = Store(run_dir)
    handle = make_fake_handle("author", [json_script({
        "summary": "recover a stopped service",
        "when_to_use": "When a command fails, check the service state.",
        "steps": ["check", "restart", "verify"],
        "not_applicable_when": "the service is already running",
    })])
    ctx = SimpleNamespace(
        run_dir=run_dir, store=store, usage=None,
        handle=lambda role: handle)
    phase = SimpleNamespace(label="p04:learn")

    failed = learn_requirement.execute_requirements(
        ctx, phase, [_requirement()], weak_profile="")
    failed_again = learn_requirement.execute_requirements(
        ctx, phase, [_requirement()], weak_profile="")

    assert failed == set() and failed_again == set()
    assert len(handle.client.calls) == 1
    stored = store.ids("skills")
    assert len(stored) == 1
    outcomes = read_jsonl(
        run_dir / "phases" / "p04_learn"
        / learn_requirement.OUTCOMES_NAME)
    assert [row["outcome"] for row in outcomes] == ["added"]
