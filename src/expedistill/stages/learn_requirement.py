"""Land aggregated review requirements as new pending skills."""

from typing import Any, Sequence, Set

from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import append_jsonl, read_jsonl
from expedistill.methods.author.requirement import write_requirement
from expedistill.methods.review.contracts import NewSkillRequirement

__all__ = ["OUTCOMES_NAME", "execute_requirements"]

OUTCOMES_NAME = "requirement_outcomes.jsonl"


def execute_requirements(
    ctx: Any,
    phase: Any,
    requirements: Sequence[NewSkillRequirement],
    *,
    weak_profile: str = "",
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
