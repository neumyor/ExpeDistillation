"""The execution family's contracts: how an execute phase runs.

The execution family names WHAT KIND of run an execute phase makes --
``real`` (a benchmark execution) or ``real_folder`` (a benchmark execution
whose skills reach the agent as a folder of files). The registry's entries
are driver-less like the review/author ones: the phase driver
(``stages/phase_execute``) consumes the params directly and calls the
execution machinery -- there is no uniform driver surface to dispatch to,
and inventing one ahead of a consumer is over-design.

``RealExecutionParams`` mirrors the execute phase's ``execution``
mapping (core.config validates the shape and the role consistency at
load; this contract validates the values at resolution, stage 0). Its
one parameter is ``arms``: only the baseline phase's fixed two arms;
``None`` for the single-arm phases (the weak model). Every such phase
samples at the global ``n_attempts_strong`` / ``n_attempts_weak`` depth.

``RealFolderExecutionParams`` is ``real``'s folder twin (practice and
test phases): the same benchmark execution, but the delivery is the
``folder`` one and the phase driver materializes the delivered folder into
the run environment instead of injecting the skills block into the
prompt. It names its executor and may override the sampling depth.
"""

from dataclasses import dataclass
from typing import List, Optional

from expedistill.core.config import ConfigError

__all__ = [
    "EXECUTOR_ROLES",
    "RealExecutionParams",
    "RealFolderExecutionParams",
]


@dataclass(frozen=True)
class RealExecutionParams:
    """A benchmark execution: the baseline's two arms, or the weak model."""

    arms: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.arms is not None and (not isinstance(self.arms, list)
                                      or not self.arms
                                      or not all(
                                          isinstance(a, str) and a
                                          for a in self.arms)):
            raise ConfigError(
                f"execution.real arms must be a list of model-role names "
                f"or absent, got {self.arms!r}")


EXECUTOR_ROLES = ("executor_weak", "executor_strong")


@dataclass(frozen=True)
class RealFolderExecutionParams:
    """A folder benchmark execution: the executor and the sampling depth.

    The ``real_folder`` execution: the benchmark runs for real like
    ``real``, but the phase's ``folder`` delivery hands the agent a
    materialized folder of skill files plus a catalog, and the prompt
    carries only the folder guidance. The load-time checklist pins the
    pairing (folder delivery <-> real_folder execution).

    * ``executor`` -- the model role that runs the phase: ``executor_weak``
      (the default) or ``executor_strong`` (the same bank handed to the
      strong model; pair it with a control pinned to that model's
      campaign column).
    * ``attempts`` -- how many times each task runs: ``None`` (the
      default) is the executor's global depth (``n_attempts_weak`` or
      ``n_attempts_strong``, the depth a bare control arm runs); an int
      >= 1 overrides it (a practice phase's sampling depth).
    """

    executor: str = "executor_weak"
    attempts: Optional[int] = None

    def __post_init__(self) -> None:
        if self.executor not in EXECUTOR_ROLES:
            raise ConfigError(
                f"execution.real_folder executor must be one of "
                f"{list(EXECUTOR_ROLES)}, got {self.executor!r}")
        if self.attempts is not None and (
                isinstance(self.attempts, bool)
                or not isinstance(self.attempts, int) or self.attempts < 1):
            raise ConfigError(
                "execution.real_folder attempts must be an int >= 1 or "
                f"absent (the executor's global depth), got "
                f"{self.attempts!r}")
