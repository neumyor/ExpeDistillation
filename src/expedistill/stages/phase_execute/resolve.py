"""The phase's raw config mappings, resolved into drivers and params.

:func:`resolve_phase` is the one call a role makes: it resolves the
execution params (and, for a practice or test phase, its executor role
and sampling depth), the delivery driver, the playbook text, the verdict
rule and the invalid-redispatch budget into a :class:`ResolvedPhase`.
Each role resolves its own phase (the package entry ``run`` does not
pre-resolve anything), so an unknown name fails inside the role that
consumes it, before any of its work.
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.composites import evolving_text, verdicts
from expedistill.core.config import (
    BASELINE_ARMS,
    ConfigError as CoreConfigError,
    FOLDER_DELIVERY_NAME,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_PRACTICE,
    PHASE_ROLE_TEST,
    PLAYBOOK_EVOLVED,
    ExecutePhaseSpec,
    PhaseInstance,
)
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.methods import registry as method_registry
from expedistill.methods.delivery import units as delivery_units
from expedistill.methods.execution import (
    RealExecutionParams,
    RealFolderExecutionParams,
)
from expedistill.stages.context import StageContext

__all__ = [
    "ROLE_OF_ARM",
    "ResolvedPhase",
    "bare_delivery_rows",
    "execution_params",
    "executor_depth",
    "executor_role",
    "resolve_phase",
]

ROLE_OF_ARM = {"executor_strong": "strong", "executor_weak": "weak"}

#: The execution params class each execute role accepts. The config
#: loader owns the same rule for YAML; this table is the driver-side
#: twin for a directly constructed spec (a mismatched pair must be a
#: ConfigError here, never an AttributeError or a silently different
#: execution path inside the driver).
_ALLOWED_EXECUTION_PARAMS = {
    PHASE_ROLE_BASELINE: (RealExecutionParams,),
    PHASE_ROLE_PRACTICE: (RealFolderExecutionParams,),
    PHASE_ROLE_TEST: (RealExecutionParams, RealFolderExecutionParams),
}

#: The roles that run ONE executor arm (the baseline runs both arms its
#: execution names).
_SINGLE_EXECUTOR_ROLES = (PHASE_ROLE_PRACTICE, PHASE_ROLE_TEST)


@dataclass(frozen=True)
class ResolvedPhase:
    """Everything a role reads from its phase's config mappings.

    ``executor_role`` is the model role (``"strong"`` / ``"weak"``) of a
    practice or test phase and ``n_attempts`` its sampling depth; both are
    None for the baseline, which runs the arms its execution names at the
    global depths.
    """

    execution: Any
    delivery: Any
    delivery_params: Any
    playbook: str
    verdict_rule: str
    redispatch_limit: int
    executor_role: Optional[str]
    n_attempts: Optional[int]

    def delivery_rows(self, bank: Any, tasks: Any
                      ) -> List[ExecutionUnitRow]:
        """The delivery driver's rows with the phase's playbook above
        their skills (an empty playbook leaves them as they are)."""
        return delivery_units.with_playbook(
            self.delivery.resolve(bank, tasks, self.delivery_params),
            self.playbook)


def resolve_phase(ctx: StageContext, phase: PhaseInstance,
                  spec: ExecutePhaseSpec) -> ResolvedPhase:
    """Resolve the phase's mappings: execution (and, for a practice or
    test phase, its executor role, sampling depth and folder capability),
    delivery, playbook, verdict rule, redispatch budget -- in that order,
    so the first bad mapping is the one reported."""
    benchmark = ctx.config.benchmark
    execution = execution_params(spec)
    role = None
    depth = None
    if spec.role in _SINGLE_EXECUTOR_ROLES:
        role = executor_role(execution)
        depth = executor_depth(ctx, execution, role)
        if spec.role == PHASE_ROLE_PRACTICE \
                and spec.delivery.get("name") != FOLDER_DELIVERY_NAME:
            raise ConfigError(
                f"a practice phase delivers through {FOLDER_DELIVERY_NAME!r}"
                f" (the test phase's presentation), got delivery "
                f"{spec.delivery.get('name')!r}")
        _require_folder_capability(spec, benchmark)
    delivery, delivery_params = method_registry.resolve(
        "delivery", dict(spec.delivery))
    return ResolvedPhase(
        execution=execution,
        delivery=delivery,
        delivery_params=delivery_params,
        playbook=_playbook_text(ctx, phase, spec),
        verdict_rule=_verdict_rule(spec, benchmark),
        redispatch_limit=int(ctx.config.invalid_redispatch_limit),
        executor_role=role,
        n_attempts=depth,
    )


def bare_delivery_rows(tasks: Sequence[Any]) -> List[ExecutionUnitRow]:
    """The bare control arm's rows: the ``none`` delivery (not a config
    choice) over the phase's tasks."""
    delivery, params = method_registry.resolve_fixed("delivery", "none")
    return delivery.resolve(None, tasks, params)


def execution_params(spec: ExecutePhaseSpec) -> Any:
    """The phase's execution parameters through the registry's execution
    family (``real`` -> RealExecutionParams, ``real_folder`` ->
    RealFolderExecutionParams);
    unknown keys are refused loudly by the
    params builder, the values' contracts by the params classes. The
    params layer speaks core's ConfigError (its stdlib-only
    vocabulary); this stage re-raises it as the infra ConfigError the
    run's failure handling and the tests expect.

    The role/params pairing and the ARMS consistency are re-checked here
    although core's load-time checklist owns them: a driver can receive a
    directly constructed spec (parse_phases never saw it), and the same
    loud refusal must hold.  A mismatched pair is a ConfigError here, not
    an AttributeError or a silently different execution path inside the
    driver.
    """
    try:
        params = method_registry.resolve_params("execution", spec.execution)
    except CoreConfigError as exc:
        raise ConfigError(str(exc)) from exc
    allowed = _ALLOWED_EXECUTION_PARAMS.get(spec.role)
    if allowed is None:
        raise ConfigError(
            f"phase role {spec.role!r} is not an execute role")
    if not isinstance(params, allowed):
        expected = " or ".join(cls.__name__ for cls in allowed)
        raise ConfigError(
            f"phase role {spec.role!r} does not accept execution "
            f"{spec.execution.get('name')!r}; expected {expected}")
    if not isinstance(params, RealExecutionParams):
        return params
    if spec.role == PHASE_ROLE_BASELINE:
        if tuple(params.arms or ()) != BASELINE_ARMS:
            raise ConfigError(
                f"execution.arms must be exactly {list(BASELINE_ARMS)} for "
                f"a baseline phase, got {list(params.arms or ())}")
    elif params.arms is not None:
        raise ConfigError(
            "the real execution carries arms but only a baseline phase "
            "is two-armed; every other execute phase runs the weak model")
    return params


def executor_role(params: Any) -> str:
    """The model role (``"strong"`` / ``"weak"``) a practice or test
    phase runs.

    Only the folder execution can name its executor; every other
    execution runs the weak model, as the test phase always did."""
    executor = (params.executor
                if isinstance(params, RealFolderExecutionParams)
                else "executor_weak")
    return ROLE_OF_ARM[executor]


def executor_depth(ctx: StageContext, params: Any, role: str) -> int:
    """How many times each task runs: the folder execution's
    ``attempts`` when it sets one, else the executor's global depth."""
    attempts = (params.attempts
                if isinstance(params, RealFolderExecutionParams) else None)
    if attempts is not None:
        return attempts
    return (ctx.config.n_attempts_strong if role == "strong"
            else ctx.config.n_attempts_weak)


def _require_folder_capability(spec: ExecutePhaseSpec,
                               benchmark: str) -> None:
    """A folder delivery needs a plugin that declares its folder prompt."""
    if spec.delivery["name"] != FOLDER_DELIVERY_NAME:
        return
    if resolve_plugin(benchmark).skill_folder_prompt_name is None:
        raise ConfigError(
            f"benchmark {benchmark!r} does not support skill folders "
            "(the real_folder execution): its plugin declares no "
            "skill_folder_prompt_name")


def _playbook_text(ctx: StageContext, phase: PhaseInstance,
                   spec: ExecutePhaseSpec) -> str:
    """The playbook this phase shows: the immediately preceding learn
    phase's version when the switch is ``evolved``, else nothing."""
    if spec.playbook != PLAYBOOK_EVOLVED:
        return ""
    evolving_text.check_playbook_capability(spec, ctx.config.benchmark)
    item = evolving_text.load_previous(
        ctx.run_dir, ctx.config.sequence, ctx.config.phases, phase.index,
        evolving_text.PLAYBOOK)
    if item is None:
        raise CodeBugError(
            f"phase {phase.label!r} shows the evolved playbook but no learn "
            "phase precedes it (the config check should have refused it)")
    return item.text


def _verdict_rule(spec: ExecutePhaseSpec, benchmark: str) -> str:
    """The rule the role grades its trials under; an unknown name fails
    before any work.

    The baseline and practice phases grade under the config's
    ``acceptance``, else the benchmark's default binding; the test phase
    under the benchmark's default binding.
    """
    if spec.role == PHASE_ROLE_TEST:
        return verdicts.default_verdict_rule(benchmark)
    if spec.role not in (PHASE_ROLE_BASELINE, PHASE_ROLE_PRACTICE):
        raise CodeBugError(f"unknown execute role {spec.role!r}")
    name = (spec.acceptance if spec.acceptance is not None
            else verdicts.default_verdict_rule(benchmark))
    verdicts.get_verdict_rule(name)
    return name
