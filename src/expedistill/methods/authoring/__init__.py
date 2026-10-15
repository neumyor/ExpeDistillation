"""The authoring slot's contract (stage 3, G2/G3).

One method today (distill/diagnose, dispatched per subtask source). The
``exams`` switch opens the pre-registered ablation pair with
``curation: none``: exam generation is skipped entirely -- and because
nothing downstream of an exam-less run can consume exams, the check
below refuses the combinations that would silently break (the exam
acceptance protocol, and the agentbrew curator's regression exams).
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["AuthoringParams", "check_authoring"]


@dataclass(frozen=True)
class AuthoringParams:
    """Whether the authoring stage generates exam questions."""

    exams: bool = True


def check_authoring(params: AuthoringParams, experiment) -> None:
    """The exams switch's cross-slot conflicts, in the one place that
    owns them (resolved for every slot at stage 0, before anything
    freezes): exam generation off is only meaningful when no consumer
    of exams is configured."""
    if params.exams:
        return
    acceptance = experiment.methods["acceptance"].get("name", "exam")
    if acceptance == "exam":
        raise ConfigError(
            "methods.authoring.exams is false but the acceptance method "
            "is 'exam' -- the text-exam protocol judges skills on the "
            "exams this switch turns off; use live_run acceptance or "
            "generate exams")
    curation = experiment.methods["curation"].get("name", "agentbrew")
    if curation == "agentbrew":
        raise ConfigError(
            "methods.authoring.exams is false but the curation method "
            "is 'agentbrew' -- K5 proves every merge on regression "
            "exams this switch turns off; use curation: none or "
            "generate exams")
