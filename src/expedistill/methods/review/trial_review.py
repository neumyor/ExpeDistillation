"""The trial-level review driver: analysis, aggregation and profile update."""

from typing import Any, Callable, Mapping, Optional, Sequence

from expedistill.core.answer_sheet import PhaseReport
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
)
from expedistill.methods.review import aggregation, profile, trial
from expedistill.methods.review.contracts import (
    ReviewContext,
    ReviewResult,
    TrialFinding,
)

__all__ = ["run_trial_review"]


def run_trial_review(
    *,
    current: PhaseReport,
    bank: Sequence[Skill],
    context: ReviewContext,
    handles: Mapping[str, Any],
    usage: Any = None,
    run_dir: Any = None,
    existing_findings: Sequence[TrialFinding] = (),
    store: Any = None,
    skip_evidence: Optional[
        Callable[[Sequence[TaskListRow]], Sequence[str]]] = None,
) -> ReviewResult:
    """Run the phase's trial review with one analysis call per failed trial."""
    findings, failed, model_errors = trial.analyze_trials(
        current=current, bank=bank, context=context,
        handle=handles["author"], usage=usage, run_dir=run_dir,
        existing=existing_findings, store=store)
    notes = [finding.profile_note for finding in findings]
    try:
        updated_profile = profile.update_profile(
            old_profile=context.weak_profile,
            notes=notes,
            handle=handles["author"],
            usage=usage,
            run_dir=run_dir)
    except ModelOutputError:
        updated_profile = context.weak_profile
        model_errors.add("profile")
    except InfrastructureError:
        updated_profile = context.weak_profile
        failed.add("profile")
    if not findings:
        return ReviewResult(
            trial_findings=[],
            profile_update=updated_profile,
            failed_targets=failed,
            model_errors=model_errors)
    try:
        items, requirements = aggregation.aggregate_findings(
            findings=findings, bank=bank, current=current,
            context=context, handle=handles["author"],
            usage=usage, run_dir=run_dir)
        if skip_evidence is not None:
            settled = set(skip_evidence([item.row for item in items]))
            items = [item for item in items
                     if not (item.row.action == "revise"
                             and item.row.target_id in settled)]
    except ModelOutputError:
        items, requirements = [], []
        model_errors.add("aggregation")
    except InfrastructureError:
        items, requirements = [], []
        failed.add("aggregation")
    return ReviewResult(
        items=items,
        trial_findings=findings,
        new_skill_requirements=requirements,
        profile_update=updated_profile,
        failed_targets=failed,
        model_errors=model_errors)
