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
prompt. Like the exam execution it carries NO parameters (the M6 review
removed ``n_attempts`` and ``max_injection_tokens``, which no legal phase
ever consumed -- folder is pinned to the test role, and the test phase
samples at the global ``n_attempts_weak`` depth exactly as it always did
under ``real``; a config key under ``real_folder`` fails loudly at the
params builder's unknown-key check, the correct behaviour for a method
with nothing to configure).
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
    """The generalization exam's knobs (schema v7).

    One text exam per skill version: the model reads the whole-bank
    catalog, opens up to ``max_skill_reads`` skill files, and writes a
    plan. There is no sampling depth (a baseline phase cannot run it and
    the load-time checklist enforces that) and no injection cap.

    * ``max_skill_reads`` -- how many skill files the model may open
      before answering; at least one, because an exam with no readable
      skill would test nothing.
    * ``bare_control`` -- run the old bare-answer arm beside the exam
      (default off).
    * ``leak_audit`` -- run the old leak audit beside the exam (default
      off). Both controls are retrospective arms of the old exam; the
      generalization exam keeps them available but opt-in.
    """

    max_skill_reads: int = 3
    bare_control: bool = False
    leak_audit: bool = False

    def __post_init__(self) -> None:
        if self.max_skill_reads < 1:
            raise ConfigError(
                "execution.exam max_skill_reads must be >= 1, got "
                f"{self.max_skill_reads!r}")


@dataclass(frozen=True)
class RealFolderExecutionParams:
    """A folder benchmark execution: no parameters, like the exam one.

    The ``real_folder`` execution (the skill-folder play, M3): the
    benchmark runs for real like ``real``, but the phase's ``folder``
    delivery hands the agent a materialized folder of skill files plus a
    catalog, and the prompt carries only the folder guidance. The
    load-time checklist pins the pairing (folder delivery <->
    real_folder execution, test role only), and the phase is a test phase:
    it samples at the global ``n_attempts_weak`` depth -- the same depth
    the bare control arm runs, the same rule the old test phase ran under
    ``real`` -- and nothing about the folder run is budgeted at execution
    time (which skill files the agent reads is M4's post-hoc trajectory
    parse, never a run-time interception). The class is therefore empty
    on purpose: a config key here fails loudly at the params builder's
    unknown-key check (the M6 review removed ``n_attempts`` and
    ``max_injection_tokens``, which no legal phase ever consumed)."""
