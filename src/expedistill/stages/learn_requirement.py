"""Land aggregated review requirements as new pending skills."""

from typing import Any, Sequence, Set

import json

from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.skill_origins import append_origin, source_ref
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import append_jsonl, read_jsonl
from expedistill.methods.author.requirement import write_requirement
from expedistill.methods.review.contracts import NewSkillRequirement

__all__ = ["DEFERRED_OUTCOME", "OUTCOMES_NAME", "execute_requirements",
           "record_deferred"]

OUTCOMES_NAME = "requirement_outcomes.jsonl"

#: Terminal outcome for a requirement the last learn phase must not write:
#: no later execution phase exists to test the skill it would produce.
DEFERRED_OUTCOME = "deferred_no_later_check"


def record_deferred(phase_dir, phase_label: str,
                    requirements: Sequence[NewSkillRequirement]) -> int:
    """Record requirements this phase deliberately does not write.

    The frozen action table writes nothing on the last check (no later
    execution phase could test a rewrite). The requirements stay in
    ``new_skill_requirements.jsonl`` and get one terminal outcome row each,
    so the ledger says why no skill exists instead of leaving them looking
    merely unwritten. Returns how many rows were appended; a resumed phase
    skips ids that already have a terminal row.
    """
    path = phase_dir / OUTCOMES_NAME
    done = ({row.get("requirement_id") for row in read_jsonl(path)}
            if path.exists() else set())
    written = 0
    for requirement in requirements:
        if requirement.requirement_id in done:
            continue
        append_jsonl(path, {
            "schema": "requirement-outcome-v1",
            "phase_label": phase_label,
            "requirement_id": requirement.requirement_id,
            "skill_id": None,
            "outcome": DEFERRED_OUTCOME,
            "version": None,
            "reason": "no later check: a rewrite could not be tested",
        })
        done.add(requirement.requirement_id)
        written += 1
    return written


def execute_requirements(
    ctx: Any,
    phase: Any,
    requirements: Sequence[NewSkillRequirement],
    *,
    weak_profile: str = "",
    source_phase_label: str = "",
) -> Set[str]:
    """Write one skill per requirement; return infra-failed requirement ids."""
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    recorder = UnitFailureRecorder(ctx.run_dir)
    path = phase_dir / OUTCOMES_NAME
    done = ({row.get("requirement_id") for row in read_jsonl(path)}
            if path.exists() else set())
    failed: Set[str] = set()
    for requirement in requirements:
        if requirement.requirement_id in done:
            continue
        try:
            skill = write_requirement(
                requirement,
                weak_profile=weak_profile,
                handle=ctx.handle("author"),
                usage=ctx.usage,
                run_dir=ctx.run_dir)
        except InfrastructureError as exc:
            recorder.record(requirement.requirement_id, phase.label, exc, exc)
            failed.add(requirement.requirement_id)
            continue
        except ModelOutputError as exc:
            recorder.record(requirement.requirement_id, phase.label, exc, exc)
            continue
        ctx.store.save(skill, "skills")
        segment = next(iter(requirement.evidence.segments), {})
        sheet_id = segment.get("sheet_id")
        step_range = segment.get("step_range") or [0, 0]
        scene_step = (int(step_range[0])
                      if isinstance(step_range, list) and step_range else 0)
        append_origin(
            phase_dir, skill_id=skill.skill_id, version=skill.version,
            phase_label=phase.label,
            change_note=(requirement.title + ": " +
                         requirement.description),
            context_text=json.dumps(
                requirement.evidence.facts, ensure_ascii=False),
            source_ref=source_ref(
                requirement.source_phase_label or source_phase_label,
                sheet_id=sheet_id, scene_step=scene_step))
        append_jsonl(path, {
            "schema": "requirement-outcome-v1",
            "phase_label": phase.label,
            "requirement_id": requirement.requirement_id,
            "skill_id": skill.skill_id,
            "outcome": "added",
            "version": skill.version,
        })
        done.add(requirement.requirement_id)
    return failed
