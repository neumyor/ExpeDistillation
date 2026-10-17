"""The execution family's contracts (R8/R9): how an execute phase runs.

The execution family names WHAT KIND of run an execute phase makes --
``real`` (a benchmark execution), ``real_folder`` (a benchmark execution
whose skills reach the agent as a folder of files, the skill-folder
play's M3) or ``exam`` (questions generated at execution time; grading
belongs to acceptance, never to execution). The registry's entries are
driver-less like the evidence/author ones: the phase driver
(``stages/phase_execute.py``) consumes the params directly and calls the
execution machinery -- there is no uniform driver surface to dispatch
to yet, and inventing one ahead of the first real consumer is the
over-design the repair plan forbids.

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

``RealFolderExecutionParams`` is ``real``'s folder twin (the skill-folder
play, M3): the same benchmark execution, but the delivery is the
``folder`` one and the phase driver materializes the delivered folder
into the run environment instead of injecting the skills block into the
prompt. The load-time checklist pins the pairing (folder delivery <->
real_folder execution, test role only); the params mirror ``real``'s
two single-arm fields -- ``n_attempts`` with the same None-means-global
contract, and ``max_injection_tokens`` capping the CATALOG text (the
folder delivery's ``skills_block`` is the catalog, so that text is what
the cap counts). No ``arms``: a folder phase is a C phase, and C runs
the weak model single-armed.
"""

from dataclasses import dataclass
from typing import List, Optional

from expedistill.core.config import ConfigError

__all__ = [
    "ExamExecutionParams",
    "RealExecutionParams",
    "RealFolderExecutionParams",
]


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


@dataclass(frozen=True)
class RealFolderExecutionParams:
    """A folder benchmark execution: sampling depth, catalog-token cap.

    The ``real_folder`` execution (the skill-folder play, M3): the
    benchmark runs for real like ``real``, but the phase's ``folder``
    delivery hands the agent a materialized folder of skill files plus a
    catalog, and the prompt carries only the folder guidance -- never
    the catalog text. The load-time checklist owns the pairing rules
    (folder delivery <-> real_folder execution, test role only); the
    values' contracts are ``real``'s, minus ``arms``:

    * ``n_attempts`` -- the weak model's sampling depth; ``None`` (the
      default) means the global ``n_attempts_weak`` at run time (the
      same depth the bare control arm runs).
    * ``max_injection_tokens`` -- the injection cap, counting the
      CATALOG text's tokens (the folder delivery's injection is the
      catalog listing; the skill files themselves live in the
      environment, and execution neither budgets nor tracks which the
      agent reads -- that audit is M4's trajectory parsing, never a
      run-time interception). Null = uncapped.
    """

    n_attempts: Optional[int] = None
    max_injection_tokens: Optional[int] = None

    def __post_init__(self) -> None:
        if self.n_attempts is not None and self.n_attempts < 1:
            raise ConfigError(
                "execution.real_folder n_attempts must be a positive int "
                f"or absent, got {self.n_attempts!r}")
        if (self.max_injection_tokens is not None
                and self.max_injection_tokens < 1):
            raise ConfigError(
                "execution.real_folder max_injection_tokens must be a "
                "positive int or absent, got "
                f"{self.max_injection_tokens!r}")
