"""The phase's raw config mappings, resolved into drivers and params.

One helper per mapping: delivery, execution params, the named verdict
rule, the compare rule and the invalid-redispatch budget. Each role
derives its own rule (the package entry ``run`` does not pre-resolve
anything), so an unknown name fails inside the role that consumes it,
before any of its work.
"""

import hashlib
from pathlib import Path
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
from expedistill.composites import playbook_store
from expedistill.stages.context import StageContext

__all__ = [
    "_ROLE_OF_ARM",
    "_compare_rule",
    "_execution_params",
    "_redispatch_limit",
    "_resolve_delivery",
    "_resolve_verdict_rule",
    "_test_role",
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


def _resolve_delivery(spec: ExecutePhaseSpec, ctx: Any = None,
                      phase: Any = None) -> Tuple[Any, Any]:
    """(driver module, params) for the phase's delivery mapping: the
    registry's delivery family over the phase's OWN raw mapping (v4 --
    the ``name`` key plus that method's parameters).

    ``playbook_file`` is a delivery-level switch (2026-11-04 P0): the
    file is read here, its sha256 checked against the config's frozen
    ``playbook_sha256``, and its text passed inline as ``playbook``. The
    file path is repo-relative (the run scripts start in the repo root);
    an absolute path also works. No playbook key means the delivery is
    byte-identical to the pre-playbook behaviour.

    ``playbook_source: learned`` reads the playbook an earlier learn phase
    wrote (``ctx`` and ``phase`` locate it); a caller that has no run
    context cannot ask for one.
    """
    delivery = dict(spec.delivery)
    if delivery.get("playbook_source") and delivery.get("playbook_file"):
        raise ConfigError(
            "delivery sets playbook_source "
            f"{delivery['playbook_source']!r} and also playbook_file; a "
            "delivery has one playbook source")
    if delivery.get("playbook_source") == "learned":
        # The source key selects where the text comes from; once the text is
        # injected as ``playbook`` the key must go, or the params check
        # would see two playbook sources.
        delivery.pop("playbook_source")
        if ctx is None or phase is None:
            raise ConfigError(
                "playbook_source 'learned' needs the run context to find "
                "the learned playbook; this caller has none")
        delivery["playbook"] = playbook_store.load_learned_playbook(
            ctx.run_dir, phase.index)
    playbook_file = delivery.get("playbook_file", "")
    playbook_sha = delivery.get("playbook_sha256", "")
    if playbook_file:
        path = Path(str(playbook_file))
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(
                f"delivery playbook_file {playbook_file!r} cannot be read "
                f"({exc}); run from the repository root or use an "
                "absolute path") from exc
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if playbook_sha and digest != playbook_sha:
            raise ConfigError(
                f"delivery playbook_file {playbook_file!r} has sha256 "
                f"{digest}, but the config froze {playbook_sha}; the file "
                "changed after the config was generated")
        delivery["playbook"] = text
    return method_registry.resolve("delivery", delivery)


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


def _test_role(params: Any) -> str:
    """The model role (``"strong"`` / ``"weak"``) a test phase runs.

    Only the folder execution can name its executor; every other
    execution runs the weak model, as the test phase always did."""
    executor = (params.executor
                if isinstance(params, RealFolderExecutionParams)
                else "executor_weak")
    return _ROLE_OF_ARM[executor]


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
