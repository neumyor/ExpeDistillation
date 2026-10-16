"""Benchmark plugin interface (layer 3): Task, RunOutcome, ViewRules, BaseEnv.

README 1.1 rule 3: everything benchmark-specific lives in one plugin directory
(runner, task loading, view rules, task prompt); this module defines the shared
vocabulary those plugins speak, so ``blocks/`` and above stay benchmark-blind.

Migrated from the SkillExpand repo (ExpeToSkill-merged @ TB-eval 4f436af):

- ``benchmarks/base.py`` BaseEnv + ``environment_call`` deadline decorator.
  The gym.Env base class is dropped (nothing used the gym API beyond the two
  methods we keep here); the 5-tuple ``step`` return is collapsed to a plain
  observation string because the loop reads termination state from
  ``is_terminated``/``is_truncated`` instead of unpacking a tuple.
- The pipe-error translation the old repo registered globally
  (BrokenPipeError/ConnectionResetError/EOFError -> EnvironmentFailure) is kept
  as a lesson: native environments run in subprocesses, and losing the pipes is
  an infrastructure failure, never evidence about the agent. Here the
  translation lives inside ``environment_call`` so it is the single boundary.

Deliberately NOT here: any stop-convention helper ("answer:"/"Finish[...]"
extraction). In the old repo the action parser was per-benchmark
(l1/adapters.py), so it is plugin territory, not shared code.
"""

import abc
import functools
import math
import os
import signal
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from expedistill.core.models import Trajectory
from expedistill.infra.reliability import CodeBugError, ConfigError, InfrastructureError

__all__ = [
    "ENV_TIMEOUT_ENV",
    "DEFAULT_ENV_TIMEOUT_SECONDS",
    "Task",
    "RunOutcome",
    "ViewRules",
    "BaseEnv",
    "BenchmarkPlugin",
    "environment_call",
]

#: Environment-variable override for the default environment-call deadline.
ENV_TIMEOUT_ENV = "EXPEDISTILL_ENV_TIMEOUT_SECONDS"
DEFAULT_ENV_TIMEOUT_SECONDS = 120.0

#: Subprocess/pipe failures that the deadline wrapper reclassifies as infra:
#: the environment died under us, the agent did nothing wrong.
_NATIVE_ENV_ERRORS = (BrokenPipeError, ConnectionResetError, EOFError)


@dataclass
class Task:
    """One benchmark task, as loaded by a plugin's ``load_tasks``.

    ``task_id`` is the raw benchmark id with no plugin prefix (E1 adds the
    ``benchmark`` field to the Trajectory; ids stay benchmark-native).
    ``instruction`` is the task text handed to the model. ``meta`` carries
    anything the plugin's own runner needs (gamefile path, gold answer, ...);
    other layers treat it as opaque.
    """

    task_id: str
    instruction: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunOutcome:
    """Uniform result of one execution of one task (E1's runner output).

    ``correctness`` is the benchmark's own scorer verdict, passed through
    verbatim: ``True`` the scorer judged the run right, ``False`` wrong,
    ``None`` no scorer verdict came back (a benchmark without a scorer, or
    a run that completed without a measurable result). Execution draws no
    other conclusion -- whether a ``None`` run counts, and what made a run
    invalid, is acceptance's named-rule decision. An infrastructure failure
    (sandbox timeout, network error, dead subprocess) never lands here: the
    runner raises ``InfrastructureError`` and K1 retries the unit. ``steps``
    holds the full, untruncated action/observation pairs -- view truncation
    (D5) happens later, at render time, never here. ``meta`` is free-form
    plugin data (scorer facts, remote run id, error text).
    """

    steps: List[Dict[str, str]] = field(default_factory=list)
    correctness: Optional[bool] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.correctness is not None \
                and not isinstance(self.correctness, bool):
            raise CodeBugError(
                f"RunOutcome.correctness must be True, False or None, got "
                f"{self.correctness!r}")


@dataclass(frozen=True)
class ViewRules:
    """Per-benchmark observation truncation rules (D5's configuration).

    D5 (blocks/view.py) renders trajectories for the model using these rules;
    react_loop reuses the same ``truncate`` for in-prompt history so a long
    rollout stays in budget without the stored steps ever losing content.
    ``truncation_marker`` may contain the ``{omitted}`` placeholder, filled
    with the number of dropped characters -- truncation must be clearly
    marked (README 1.3, D5 row), never silent.
    """

    max_obs_chars: int = 4000
    head_keep: int = 200
    tail_keep: int = 200
    truncation_marker: str = "[... {omitted} characters truncated ...]"

    def __post_init__(self) -> None:
        if self.max_obs_chars < 1 or self.head_keep < 0 or self.tail_keep < 0:
            raise ConfigError(
                "ViewRules needs max_obs_chars >= 1 and head_keep/tail_keep >= 0, "
                f"got max_obs_chars={self.max_obs_chars}, "
                f"head_keep={self.head_keep}, tail_keep={self.tail_keep}")
        if self.head_keep + self.tail_keep >= self.max_obs_chars:
            raise ConfigError(
                "ViewRules head_keep + tail_keep must leave room for the "
                f"truncation marker inside max_obs_chars "
                f"(got {self.head_keep} + {self.tail_keep} >= {self.max_obs_chars})")

    def truncate(self, text: str) -> str:
        """Apply the rules to one observation; short texts pass through."""
        text = str(text)
        if len(text) <= self.max_obs_chars:
            return text
        head = text[: self.head_keep]
        tail = text[len(text) - self.tail_keep:] if self.tail_keep else ""
        omitted = len(text) - self.head_keep - self.tail_keep
        marker = self.truncation_marker
        if "{omitted}" in marker:
            marker = marker.replace("{omitted}", str(omitted))
        return "\n".join(part for part in (head, marker, tail) if part)


class BaseEnv(abc.ABC):
    """Local stepwise environment interface (SearchQA/ALFWorld style).

    Contract for plugin envs:

    - ``step(action)`` returns the observation as a string; invalid actions are
      the env's business (return an "invalid action" observation) and must not
      raise -- an env exception that is not an InfrastructureError is a code
      bug and stops everything.
    - The env tracks its own state: the loop polls ``is_terminated`` /
      ``is_truncated`` after each step and reads ``success_fn()`` once at the
      end. An env that reports success must also flag termination, otherwise
      a budget-exhausted episode scores ``False`` even if the goal was
      reached (the loop trusts the termination flags, not the clock).

    The default ``is_terminated``/``is_truncated`` read the same attributes
    the SkillExpand envs maintained (``terminated``, ``curr_step``,
    ``max_steps``); envs that track state differently override them.
    """

    @abc.abstractmethod
    def reset(self) -> None:
        """Start a fresh episode; also used to retry an interrupted one."""

    @abc.abstractmethod
    def step(self, action: str) -> str:
        """Apply one action, return the observation."""

    @abc.abstractmethod
    def success_fn(self) -> bool:
        """Whether the goal is achieved in the current episode state."""

    def is_terminated(self) -> bool:
        return bool(getattr(self, "terminated", False))

    def is_truncated(self) -> bool:
        budget = getattr(self, "max_steps", None)
        if budget is None:
            return False
        return getattr(self, "curr_step", 0) > budget


def environment_call(function=None, *, seconds: Optional[float] = None):
    """Deadline wrapper for environment methods, hardened at SkillExpand.

    Usable bare (``@environment_call``) or with an explicit bound
    (``@environment_call(seconds=30)``); without an explicit bound the value
    comes from ``EXPEDISTILL_ENV_TIMEOUT_SECONDS`` (default 120s), read at
    each call so tests and ops can tune it without rebuilding.

    - A method exceeding the deadline raises InfrastructureError (a hung
      sandbox is an interrupted unit, not the agent's failure).
    - A subprocess/pipe death inside the method is reclassified to
      InfrastructureError, the old repo's global translation lesson.
    - A malformed deadline value is a ConfigError (stop and fix the config).
    - signal.setitimer only works on the process main thread; from any other
      thread this raises RuntimeError instead of silently losing the deadline.
      Run environments that need deadlines on the main thread or through a
      process pool (B5 ``thread=False``).
    """
    if function is not None:
        return _bounded_call(function, seconds)
    def decorator(fn):
        return _bounded_call(fn, seconds)
    return decorator


def _bounded_call(function, explicit_seconds):
    @functools.wraps(function)
    def bounded(*args, **kwargs):
        seconds = explicit_seconds
        if seconds is None:
            raw = os.environ.get(ENV_TIMEOUT_ENV, "")
            try:
                seconds = float(raw) if raw.strip() else DEFAULT_ENV_TIMEOUT_SECONDS
            except ValueError:
                raise ConfigError(
                    f"{ENV_TIMEOUT_ENV} must be a number, got {raw!r}") from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ConfigError(
                f"{ENV_TIMEOUT_ENV} must be finite and positive, got {seconds!r}")
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(
                "environment_call deadlines require the process main thread; "
                "run this environment on the main thread or a process pool")

        def expired(signum, frame):
            raise InfrastructureError(
                f"environment call {function.__name__} exceeded {seconds:g}s")

        previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            return function(*args, **kwargs)
        except _NATIVE_ENV_ERRORS as exc:
            raise InfrastructureError(
                f"environment call {function.__name__} lost its subprocess: "
                f"{type(exc).__name__}: {exc}") from exc
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    return bounded


class BenchmarkPlugin(abc.ABC):
    """The four duties every benchmark plugin implements (AGENTS.md section 7).

    A plugin is the only place benchmark knowledge may exist. Subclass this,
    register the instance in ``benchmarks.registry`` from the plugin package's
    ``__init__``, and register the task prompt in the B2 registry under
    ``task_prompt_name``.

    Contract details:

    - ``load_tasks(tasks_spec)``: ``None`` = all tasks, ``list[str]`` = task
      ids, ``str`` = a path (file or directory); the interpretation is the
      plugin's. Unknown ids in a list are a ConfigError.
    - ``run(task, handle, skills_block, workdir)``: one task, one attempt.
      ``skills_block`` is the D9 block verbatim and may be empty -- bare and
      skills runs use the SAME prompt template (README 1.1 rule 4 / G4 rule),
      so the template must contain a ``{skills_block}`` slot that renders to
      nothing when empty. All model calls go through ``llm.call.call_model``
      (local runners: via ``benchmarks.react_loop.run_react``; the TB runner's
      calls happen remotely in Harbor and are accounted from its results).
      Expected infrastructure failures (sandbox timeout, network error)
      raise ``InfrastructureError`` -- K1 retries the unit; anything else
      that raises is a code bug and stops everything. Files the
      runner produces go under ``workdir`` and nowhere else. B1's request
      dumps are not runner files: ``call_model`` writes them under the
      handle's ``run_dir`` (``<run>/llm_requests/``, set by K1), so a runner
      must not pass ``run_dir=workdir`` to ``run_react`` / ``call_model``; a
      handle without a ``run_dir`` writes no dumps.
    - ``view_rules``: this benchmark's D5 truncation configuration.
    - ``task_prompt_name``: the B2 name this plugin registered its task
      execution prompt under; the template's variables are
      ``{instruction}``, ``{skills_block}`` and ``{history}``.
    - the optional AUXILIARY metric (``aux_metric_name`` /
      ``aux_success``): a second, pre-registered success notion reported
      ALWAYS beside the primary one (which stays the scorer verdict,
      ``correctness``), never
      swapped in or picked between after seeing results. The primary
      metric's strictness (e.g. SearchQA's exact-match EM) is exactly why
      an auxiliary view exists; see ``aux_metric_name`` below for the
      freeze / change discipline.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Registry name; also the ``benchmark`` field of every Trajectory."""

    @abc.abstractmethod
    def load_tasks(self, tasks_spec: Union[None, str, List[str]]) -> List[Task]:
        """Resolve a tasks spec into concrete Task objects."""

    @abc.abstractmethod
    def run(self, task: Task, handle: Any, skills_block: str,
            workdir: Any) -> RunOutcome:
        """Execute one task once; infra failures raise
        ``InfrastructureError`` (K1 retries the unit)."""

    @property
    @abc.abstractmethod
    def view_rules(self) -> ViewRules:
        """This benchmark's observation truncation rules (D5 config)."""

    @property
    @abc.abstractmethod
    def task_prompt_name(self) -> str:
        """B2 registry name of this benchmark's task execution prompt."""

    #: Name of the auxiliary metric this plugin pre-registers (e.g.
    #: ``"sub_em"``), or None (the default) when the benchmark has none.
    #: Stage 0 freezes this name into the manifest as ``aux_metric`` -- the
    #: auxiliary metric is part of the experiment identity. It is CODE,
    #: not config: changing the scorer is a plugin code change that does
    #: NOT change any prompt digest, so an old run directory will not
    #: refuse a resume across it (README's auxiliary-metric contract
    #: records this limitation; compare against run directories, not
    #: across scorer versions).
    aux_metric_name: Optional[str] = None

    def aux_success(self, traj: Trajectory) -> Optional[bool]:
        """Auxiliary success of one trajectory.

        The default returns None: this plugin declares no auxiliary metric
        (``aux_metric_name`` is None) and no stage ever calls this method.
        A plugin that declares ``aux_metric_name`` must override this to
        answer for every SCORED trajectory (``correctness`` is True or
        False): True / False is the measurement; a None from a declared
        scorer is a contract violation and the stage wiring raises on it.
        Unscored trajectories (``correctness`` is None) and missing units
        are never shown to the scorer (a run without a scorer verdict is
        not a measurement of anything).
        """
        return None
