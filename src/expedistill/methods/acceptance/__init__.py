"""The stage-4 acceptance drivers' shared contract: layout constants and
the two methods' params.

The stage directory and the summary file name are shared by both drivers
(and read by the audit tooling). The failure-ledger label is NOT here:
each driver writes ``failed_units.jsonl`` rows under its own stage
identity (exam: ``stage4_acceptance``, live: ``stage4_live``), so the
labels live with their drivers.

The params classes live in this light package (never a driver import)
so validation can run -- in stage 0, before anything freezes -- without
loading any driver module.
"""

from dataclasses import dataclass
from typing import Optional

from expedistill.core.config import ConfigError

__all__ = ["STAGE4_DIR", "SUMMARY_FILENAME", "ExamParams", "LiveRunParams",
           "check_live_run"]

#: The stage's directory in the run layout (README section 2).
STAGE4_DIR = "stage4_acceptance"

#: Name of the stage summary inside ``STAGE4_DIR``.
SUMMARY_FILENAME = "stage4_summary.json"


@dataclass(frozen=True)
class ExamParams:
    """The text-exam protocol's rewrite budget (K4 rounds)."""

    retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.retry_limit < 0:
            raise ConfigError(
                "methods.acceptance.exam retry_limit must be >= 0, "
                f"got {self.retry_limit}")


@dataclass(frozen=True)
class LiveRunParams:
    """The live-run protocol (decision doc 2026-10-14): sampling depth,
    the pre-registered improvement rule, and the injection cap."""

    n_attempts: int
    improvement: str
    max_injection_tokens: Optional[int] = None

    def __post_init__(self) -> None:
        # >= 2, not >= 1: one attempt per arm makes the comparison's
        # false pass rate equal the model's own success rate (ruling A2).
        if self.n_attempts < 2:
            raise ConfigError(
                f"methods.acceptance.live_run n_attempts must be >= 2, "
                f"got {self.n_attempts}")
        if not self.improvement:
            raise ConfigError(
                "methods.acceptance.live_run improvement must name a "
                "registered rule from ACCEPTANCE_IMPROVE")
        if (self.max_injection_tokens is not None
                and self.max_injection_tokens < 1):
            raise ConfigError(
                "methods.acceptance.live_run max_injection_tokens must be "
                f"a positive int or null, got {self.max_injection_tokens}")


def check_live_run(params: LiveRunParams, experiment) -> None:
    """The live method's cross-constraint, checked at stage-0 resolution.

    The live sampling depth must not exceed the frozen weak-arm depth
    (stage 1's baseline units exist by id; beyond it the driver would
    report missing baseline units as infrastructure errors), and the
    improvement rule name must be registered.
    """
    from expedistill.blocks.rules import get_acceptance_improve

    if params.n_attempts > experiment.n_attempts_weak:
        raise ConfigError(
            f"methods.acceptance.live_run n_attempts ({params.n_attempts}) "
            f"exceeds n_attempts_weak ({experiment.n_attempts_weak}); the "
            "stage-1 weak baseline would not cover the live executions")
    get_acceptance_improve(params.improvement)
