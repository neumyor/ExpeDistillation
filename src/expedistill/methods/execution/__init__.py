"""The execution family's contracts (R8/R9): how an execute phase runs.

The execution family names WHAT KIND of run an execute phase makes --
``real`` (a benchmark execution) or ``exam`` (questions generated at
execution time; grading belongs to acceptance, never to execution). The
registry's two entries are driver-less like the evidence/author ones:
the phase driver (``stages/phase_execute.py``) consumes the params
directly and calls the execution machinery -- there is no uniform
driver surface to dispatch to yet, and inventing one ahead of the first
real consumer is the over-design the repair plan forbids.

``RealExecutionParams`` mirrors the execute phase's ``execution``
mapping (core.config validates the shape and the role consistency at
load; this contract validates the values at resolution, stage 0):

* ``n_attempts`` -- the weak model's sampling depth. ``None`` (the
  default) means the global ``n_attempts_weak`` at run time (the same
  depth the bare control arm runs, so a comparison never asks for
  executions the control did not run); a baseline phase must not carry
  it (its two arms run the global depths) and the load-time checklist
  caps it at its control's depth.
* ``max_injection_tokens`` -- the injection cap (null = uncapped).
* ``arms`` -- only a baseline phase's fixed two arms; ``None`` for the
  single-arm phases (the weak model).
"""

from dataclasses import dataclass
from typing import List, Optional

from expedistill.core.config import ConfigError

__all__ = ["ExamExecutionParams", "RealExecutionParams"]


@dataclass(frozen=True)
class RealExecutionParams:
    """A benchmark execution: sampling depth, injection cap, arms."""

    n_attempts: Optional[int] = None
    max_injection_tokens: Optional[int] = None
    arms: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.n_attempts is not None and self.n_attempts < 1:
            raise ConfigError(
                "execution.real n_attempts must be a positive int or "
                f"absent, got {self.n_attempts!r}")
        if (self.max_injection_tokens is not None
                and self.max_injection_tokens < 1):
            raise ConfigError(
                "execution.real max_injection_tokens must be a positive "
                "int or absent, got "
                f"{self.max_injection_tokens!r}")
        if self.arms is not None and (not isinstance(self.arms, list)
                                      or not self.arms
                                      or not all(
                                          isinstance(a, str) and a
                                          for a in self.arms)):
            raise ConfigError(
                f"execution.real arms must be a list of model-role names "
                f"or absent, got {self.arms!r}")


@dataclass(frozen=True)
class ExamExecutionParams:
    """The exam execution has no parameters: one answer per exam, no
    sampling depth (a baseline phase cannot run it and the load-time
    checklist enforces that), no injection cap (a single skill's exam
    is bounded by the skill itself)."""
