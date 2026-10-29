"""Land aggregated review requirements as new pending skills."""

from typing import Any, List, Sequence, Set


from expedistill.benchmarks.registry import get_plugin
from expedistill.composites import trace
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.core.models import Exam
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import append_jsonl, read_jsonl
from expedistill.methods.author.requirement import write_requirement
from expedistill.methods.review import attribution
from expedistill.methods.review.contracts import NewSkillRequirement
from expedistill.stages import learn_ledger as ledger

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


def _scenes_of(ctx: Any, requirement: NewSkillRequirement) -> List[TraceRef]:
    """The real places a requirement's evidence came from.

    A source sheet that ran a real execution is cut at the start of the
    failure window the review named. A paper exam answer is not a scene:
    its exam's own ``scene`` is the real situation it was cut from, so the
    new skill's exam never becomes an exam of an exam.
    """
    scenes: List[TraceRef] = []
    for sheet_id in requirement.source_sheet_ids:
        sheet = _load_source_sheet(ctx, requirement, sheet_id)
        if trace.is_exam_answer(sheet):
            exam = ctx.store.load("exams", sheet.execution.task_ref, Exam)
            ref = trace.scene_of(exam)
        else:
            step = requirement.source_steps.get(sheet_id, 0)
            steps = len(sheet.execution.steps or [])
            ref = trace.trace_at(sheet, max(0, min(step, steps - 1)))
        if ref not in scenes:
            scenes.append(ref)
    if not scenes:
        raise CodeBugError(
            f"requirement {requirement.requirement_id!r} names no source "
            "sheet to cut a scene from")
    return scenes


def _render_scenes(ctx: Any, scenes: List[TraceRef]) -> str:
    """The real steps around every source, each execution shown once."""
    return trace.render_scenes(
        ctx.run_dir, scenes,
        instruction_for=lambda task: attribution.task_instruction_for(
            ctx.config.benchmark, task),
        view_rules=get_plugin(ctx.config.benchmark).view_rules)


def _load_source_sheet(ctx: Any, requirement: NewSkillRequirement,
                       sheet_id: str):
    label = requirement.source_phase_label
    if not label:
        raise CodeBugError(
            f"requirement {requirement.requirement_id!r} has no source "
            "phase label")
    return trace.load_sheet(ctx.run_dir, TraceRef(label, sheet_id, 0))


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
        scenes = _scenes_of(ctx, requirement)
        try:
            skill = write_requirement(
                requirement,
                weak_profile=weak_profile,
                source_context=_render_scenes(ctx, scenes),
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
        ledger.record_version(
            ctx.store, phase_dir, ctx.run_dir, skill,
            Provenance(
                skill_id=skill.skill_id, version=skill.version,
                phase_label=phase.label, cause="requirement",
                scene=scenes[0], sources=scenes,
                note=requirement.title + ": " + requirement.description))
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
