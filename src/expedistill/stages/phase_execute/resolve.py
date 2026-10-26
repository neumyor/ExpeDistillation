"""The phase's raw config mappings, resolved into drivers and params.

One helper per mapping: delivery, execution params, the named verdict
rule, the compare rule and the invalid-redispatch budget. Each role
derives its own rule (the package entry ``run`` does not pre-resolve
anything), so an unknown name fails inside the role that consumes it,
before any of its work.
"""

from typing import Tuple, Any

from expedistill.composites import decision, verdicts
from expedistill.core.config import (
    BASELINE_ARMS,
    ConfigError as CoreConfigError,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
)
from expedistill.infra.reliability import ConfigError
from expedistill.methods import registry as method_registry
from expedistill.methods.execution import (
    ExamExecutionParams,
    RealExecutionParams,
    RealFolderExecutionParams,
)
from expedistill.stages.context import StageContext

__all__ = [
    "_ROLE_OF_ARM",
    "_compare_rule",
    "_execution_params",
    "_redispatch_limit",
    "_resolve_delivery",
    "_resolve_verdict_rule",
]

_ROLE_OF_ARM = {"executor_strong": "strong", "executor_weak": "weak"}

#: The execution params class each execute role accepts. The config
#: loader owns the same rule for YAML; this table is the driver-side
#: twin for a directly constructed spec (a mismatched pair must be a
#: ConfigError here, never an AttributeError or a silently different
#: execution path inside the driver).
_ALLOWED_EXECUTION_PARAMS = {
    PHASE_ROLE_BASELINE: (RealExecutionParams,),
    PHASE_ROLE_EXAM: (ExamExecutionParams,),
    PHASE_ROLE_LIVE: (RealExecutionParams,),
    PHASE_ROLE_TEST: (RealExecutionParams, RealFolderExecutionParams),
}


def _resolve_delivery(spec: ExecutePhaseSpec) -> Tuple[Any, Any]:
    """(driver module, params) for the phase's delivery mapping: the
    registry's delivery family over the phase's OWN raw mapping (v4 --
    the ``name`` key plus that method's parameters)."""
    return method_registry.resolve("delivery", spec.delivery)


def _execution_params(spec: ExecutePhaseSpec) -> Any:
    """The phase's execution parameters through the registry's execution
    family (``real`` -> RealExecutionParams, ``exam`` ->
    ExamExecutionParams, ``real_folder`` -> RealFolderExecutionParams);
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


def _resolve_verdict_rule(spec: ExecutePhaseSpec, benchmark: str) -> str:
    """The phase's named verdict rule (the config's ``acceptance``, else
    the benchmark's default binding); an unknown name fails before any
    work (get_verdict_rule's ConfigError)."""
    name = (spec.acceptance if spec.acceptance is not None
            else verdicts.default_verdict_rule(benchmark))
    verdicts.get_verdict_rule(name)
    return name


def _compare_rule(spec: ExecutePhaseSpec) -> str:
    """The phase's compare rule name (decision's compare family; the
    family's default when the config does not name one)."""
    rule = spec.compare.rule if spec.compare is not None else None
    return rule or decision.DEFAULT_COMPARE_RULE


def _redispatch_limit(ctx: StageContext) -> int:
    """The config's invalid-redispatch budget (the v4 global)."""
    return int(ctx.config.invalid_redispatch_limit)
