"""The authoring slot's contract (stage 3, G2/G3) -- v3 shim + check.

``AuthoringParams`` lives in the author family package since M3 (plan
section 3's registry key set); this slot package re-exports it and keeps
the exams switch's v3 cross-slot check. Both die with the slot in M4.

One method today (distill/diagnose, dispatched per subtask source). The
``exams`` switch opens the pre-registered ablation pair with
``curation: none``: exam generation is skipped entirely -- and because
nothing downstream of an exam-less run can consume exams, the check
below refuses the combinations that would silently break (the exam
acceptance protocol, and the agentbrew curator's regression exams).
"""

from expedistill.core.config import METHOD_DEFAULTS, ConfigError
from expedistill.methods.author import AuthoringParams

__all__ = ["AuthoringParams", "check_authoring"]


def check_authoring(params: AuthoringParams, experiment) -> None:
    """The exams switch's cross-slot conflicts, in the one place that
    owns them (resolved for every slot at stage 0, before anything
    freezes): exam generation off is only meaningful when no consumer
    of exams is configured."""
    if params.exams:
        return
    acceptance = experiment.methods["acceptance"]["name"]
    if acceptance == METHOD_DEFAULTS["acceptance"]["name"]:
        raise ConfigError(
            "methods.authoring.exams is false but the acceptance method "
            "is 'exam' -- the text-exam protocol judges skills on the "
            "exams this switch turns off; use live_run acceptance or "
            "generate exams")
    curation = experiment.methods["curation"]["name"]
    if curation == METHOD_DEFAULTS["curation"]["name"]:
        raise ConfigError(
            "methods.authoring.exams is false but the curation method "
            "is 'agentbrew' -- K5 proves every merge on regression "
            "exams this switch turns off; use curation: none or "
            "generate exams")
