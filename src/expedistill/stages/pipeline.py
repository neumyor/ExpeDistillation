"""Layer 6: the straight-line stage 0 -> 6 pipeline over one run directory.

The pipeline is deliberately dumb: it loads the config, takes the run lock,
builds one client per unique model and one handle per role, hands every stage
the same StageContext, and passes each stage's returned values to the next.
Every rule ("which tasks does stage 1 distill on", "what is the control in
stage 6") lives in the stage modules; nothing here branches on the experiment
mode beyond passing stage 0's frozen train/test id lists onward.

The run's progress ledger is ``<run_dir>/pipeline_log.jsonl``: one appended
line per completed stage (``{"stage": name, "summary": ...}``). A resumed run
re-runs the stages (their internal unit-level resume skips finished work) and
appends fresh lines, so the ledger keeps the full history; readers take the
LAST line per stage (see cli.summary).

Locking and usage accounting follow AGENTS.md section 6: the run directory is
exclusive (RunLock, B7), and usage is folded from the on-disk ledger so a
resumed run never under-reports.
"""

import json
import math
import sys
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from expedistill.blocks import tasks as tasks_block
from expedistill.core.config import ROLE_DEFAULTS, load_config
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import Store, append_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import make_handle
from expedistill.llm import providers
from expedistill.stages import (
    stage0_freeze,
    stage1_baseline,
    stage2_segmentation,
    stage3_generation,
    stage4_acceptance,
    stage5_curation,
    stage6_test,
)
from expedistill.stages.context import RunSettings, StageContext

__all__ = [
    "LOG_NAME",
    "STAGE_NAMES",
    "USAGE_PATH",
    "ledger_path",
    "usage_path",
    "run_pipeline",
]

#: The progress ledger, one line per completed stage.
LOG_NAME = "pipeline_log.jsonl"

#: Stage modules in execution order; also the ledger's stage names and the
#: completeness checklist of cli.summary.
STAGE_NAMES = (
    "stage0_freeze",
    "stage1_baseline",
    "stage2_segmentation",
    "stage3_generation",
    "stage4_acceptance",
    "stage5_curation",
    "stage6_test",
)

#: B7 usage ledger (llm_requests/usage.jsonl, the same path the K-composite
#: tests construct; the module docstring of infra.usage.py pins it).
USAGE_PATH = ("llm_requests", "usage.jsonl")


def usage_path(run_dir: Path) -> Path:
    """The B7 usage ledger path inside ``run_dir``."""
    return Path(run_dir).joinpath(*USAGE_PATH)


def _json_safe(summary: Dict[str, Any]) -> Dict[str, Any]:
    """A ledger-writable copy of ``summary``.

    The ledger is a progress record, not an object store: a stage summary that
    carries a non-JSON value (an object a future stage deems useful to return)
    must never crash the run AFTER the stage's work was done and persisted.
    ``default=repr`` keeps everything JSON-native intact and renders anything
    else as its repr. A summary that needs full fidelity has its own summary
    file (every stage writes one); the ledger only needs to be readable.
    """
    return json.loads(json.dumps(summary, default=repr))


def ledger_path(run_dir: Path) -> Path:
    """The pipeline progress ledger path inside ``run_dir``."""
    return Path(run_dir) / LOG_NAME


def _default_client(model_name: str,
                    timeout_sec: float = 300.0) -> Any:
    """The real-endpoint builder: one client per unique model, from the
    provider registry (``llm.providers``).

    The provider NAME comes from ``EXPEDISTILL_LLM_PROVIDER`` (default
    ``openai_compat``); the adapter gives the builder's client the
    ``make_client(model_name)`` shape, and building one per unique model
    keeps the documented one-client-per-model layout, so a later
    per-model endpoint needs no pipeline change. ``timeout_sec`` is the
    run's per-request client timeout; _build_handles threads it in as a
    partial so the ``make_client`` injection point keeps its
    one-argument signature.
    """
    return providers.default_client(timeout_sec=timeout_sec)


def _build_handles(
    cfg: Any,
    config_path: Path,
    make_client: Optional[Callable[[str], Any]],
    *,
    infra_retries: int,
    infra_backoff_seconds: float,
    llm_timeout_sec: float = 300.0,
) -> Dict[str, Any]:
    """One handle per role, one client per unique model name.

    Every handle's ``max_tokens`` comes from the config's token budgets:
    ``cfg.max_output_tokens_for(role)`` answers a per-role
    ``max_output_tokens`` override, else the config's
    ``default_max_output_tokens`` -- role granularity, one budget per role,
    and never the ModelHandle's built-in default (the dead 5120 is gone
    from this path). The helper raises ConfigError when the config carries
    no ``token_budgets``; the failure era that produces is deliberate: the
    run stops while handles are being built, before stage 0 freezes
    anything and before any client is built. Budget boundary: only requests
    through call_model are capped. TB's remote execution arm does NOT go
    through call_model (the sandbox runs the model behind the TBENCH_* env
    knobs), so executor output budgets do not apply to that arm -- real
    TB-side budgets live in those env knobs, not in this table.

    Every handle carries the same per-call infra retry settings (call_model
    resends a request that failed with InfrastructureError); they are run
    settings, not method parameters, so they never enter the manifest. The
    same goes for ``llm_timeout_sec``, the per-request client timeout the
    default builder bakes into its clients (an injected ``make_client``
    owns its own timeout).

    A role missing from the config's role table is a ConfigError naming the
    role AND the config file (the reader must know which experiment is broken,
    not just that one is). The check runs before any client is built so a bad
    config fails without touching the endpoint.
    """
    for role in ROLE_DEFAULTS:
        if not cfg.models.get(role):
            raise ConfigError(
                f"model role {role!r} is missing from the role table of "
                f"config {config_path}")
    builder = make_client or partial(_default_client, timeout_sec=llm_timeout_sec)
    clients: Dict[str, Any] = {}

    def client_for(model_name: str) -> Any:
        if model_name not in clients:
            clients[model_name] = builder(model_name)
        return clients[model_name]

    def handle_for(role: str) -> Any:
        # Resolved BEFORE the client is built: a config whose budgets are
        # unusable fails without touching the endpoint. The helper's
        # ConfigError is core's (a ValueError subclass); re-raise it as the
        # infra ConfigError the CLI prints as a clean stop, naming the
        # config file like every other config failure here.
        try:
            budget = cfg.max_output_tokens_for(role)
        except ValueError as exc:
            raise ConfigError(
                f"invalid config {config_path}: {exc}") from exc
        model_name = cfg.models[role]
        return make_handle(
            role, model_name, client_for(model_name),
            # Per-model extra chat-call parameters from the frozen config:
            # a fresh dict per handle, empty for models the config does not
            # list (never a shared mutable default).
            extra_body=dict(cfg.model_options.get(model_name, {})),
            max_tokens=budget,
            infra_retries=infra_retries,
            infra_backoff_seconds=infra_backoff_seconds,
        )

    return {role: handle_for(role) for role in ROLE_DEFAULTS}


def _check_run_settings(*, workers: Any, llm_infra_retries: Any,
                        llm_infra_backoff_seconds: Any, llm_timeout_sec: Any,
                        k1_stagger: Any,
                        allow_infra_shortfall: Any, until_stage: Any) -> None:
    """Reject bad run settings as ConfigError before anything runs.

    Each value would otherwise only be rejected deep inside K1 or
    ModelHandle (a ValueError, i.e. a CLI traceback) after stage 0 had
    already frozen the run -- or, for ``workers``, not at all.
    """
    def is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    problems = []
    if not is_int(workers) or workers < 1:
        problems.append(f"workers must be an int >= 1, got {workers!r}")
    if not is_int(llm_infra_retries) or llm_infra_retries < 0:
        problems.append("llm_infra_retries must be an int >= 0, got "
                        f"{llm_infra_retries!r}")
    backoff = llm_infra_backoff_seconds
    if (isinstance(backoff, bool) or not isinstance(backoff, (int, float))
            or not math.isfinite(backoff) or backoff < 0):
        problems.append("llm_infra_backoff_seconds must be a finite number "
                        f">= 0, got {backoff!r}")
    timeout = llm_timeout_sec
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        problems.append("llm_timeout_sec must be a finite number > 0, "
                        f"got {timeout!r}")
    stagger = k1_stagger
    if (isinstance(stagger, bool) or not isinstance(stagger, (int, float))
            or not math.isfinite(stagger) or stagger < 0):
        problems.append("k1_stagger must be a finite number >= 0, "
                        f"got {stagger!r}")
    if not isinstance(allow_infra_shortfall, bool):
        problems.append("allow_infra_shortfall must be a bool, got "
                        f"{allow_infra_shortfall!r}")
    if (until_stage is not None
            and (not is_int(until_stage) or not 0 <= until_stage <= 6)):
        problems.append(f"until_stage must be an int in [0, 6] or None, "
                        f"got {until_stage!r}")
    if problems:
        raise ConfigError("invalid run setting(s): " + "; ".join(problems))


def _task_objects(cfg: Any, ids: Sequence[str]) -> Sequence[Any]:
    """Rebuild Task objects from a frozen id list via D1 (blocks.tasks).

    Stage 6 takes Task objects; the pipeline holds the frozen id lists, so
    the objects come from ONE deterministic plugin load of exactly those ids
    -- the same route stage 1 uses internally. A listed id the plugin does
    not know is the plugin's ConfigError to raise.
    """
    by_id = {task.task_id: task
             for task in tasks_block.load_tasks(cfg.benchmark, sorted(ids))}
    try:
        return [by_id[task_id] for task_id in ids]
    except KeyError as exc:
        raise ConfigError(
            f"benchmark {cfg.benchmark!r} does not know task id {exc} "
            "named by the frozen split") from None


def run_pipeline(
    *,
    config_path: Path,
    run_dir: Path,
    workers: int = 1,
    make_client: Optional[Callable[[str], Any]] = None,
    on_stage: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    llm_infra_retries: int = 3,
    llm_infra_backoff_seconds: float = 5.0,
    llm_timeout_sec: float = 300.0,
    allow_infra_shortfall: bool = False,
    k1_stagger: float = 0.0,
    until_stage: Optional[int] = None,
) -> Dict[str, Any]:
    """Run stages 0-6 over ``run_dir``; return the LAST EXECUTED stage's
    summary.

    ``make_client(model_name) -> client`` overrides the default
    ``_default_client`` (``OpenAICompatClient.from_env()``; tests inject
    FakeClients here). Either way ONE client is built per unique model name
    in the role table.
    ``on_stage(name, summary)`` fires after each stage completes, right after
    its ledger line is written -- the CLI uses it to print progress.

    ``until_stage`` stops the pipeline after that stage (inclusive, 0-6);
    ``None`` runs all stages and keeps today's contract of returning stage
    6's summary. It is operational, like ``workers``: not frozen into the
    manifest, and a resume may change it freely. A stopped run simply
    re-runs from stage 0 next time, and each stage skips its finished units
    (the existing resume model), so continuing means re-running with a higher
    ``until_stage`` (or without one) at zero extra cost for finished work.

    ``llm_infra_retries`` / ``llm_infra_backoff_seconds`` go onto every
    ModelHandle: a model request that fails on infrastructure is resent up to
    that many times with exponential backoff (capped in llm.call) before the
    unit counts as infra-failed. Like ``workers`` they are operational: they
    change how hard the run tries, not what it measures, so they live outside
    ExperimentConfig and the frozen manifest and may differ between a run
    and its resume.

    ``llm_timeout_sec`` is the per-request client timeout the default client
    builder bakes into its clients (an injected ``make_client`` owns its own
    timeout). It is operational for the same reason: whether an endpoint
    hung for 300 seconds says nothing about the experiment, so the value is
    not frozen and a resume may change it. The default (300) fits
    reasoning-effort=max calls that legitimately take minutes.

    ``allow_infra_shortfall`` is NOT operational: accepting holes changes
    what is measured (F, hence the bank). It lets stages 1 and 6 proceed over
    units that exhausted their infra retries instead of raising
    InfrastructureError, and each stage records the accepted unit ids on
    disk as a durable per-run decision that every later call honours with or
    without the flag (see the stage modules); the holes stay counted as
    infra, the stage summary records ``infra_shortfall_accepted: true``, its
    ledger line carries the same flag at top level, and a warning goes to
    stderr. The flag itself is not frozen: it only decides whether NEW holes
    are accepted.

    ``k1_stagger`` is the per-unit random startup jitter in seconds passed
    to every K1 call (default 0): it spreads the ssh connections of parallel
    unit dispatches, and it is operational like ``workers`` -- not frozen
    into the manifest, so a resume may change it.

    The run lock is taken for the whole pipeline; a stale lock is taken over
    with its warning printed to stderr (RunLock names the dead holder). The
    returned dict is the summary of the last stage that actually ran (always
    stage 0's or later: stage 0 executes unconditionally).
    """
    _check_run_settings(
        workers=workers, llm_infra_retries=llm_infra_retries,
        llm_infra_backoff_seconds=llm_infra_backoff_seconds,
        llm_timeout_sec=llm_timeout_sec,
        k1_stagger=k1_stagger,
        allow_infra_shortfall=allow_infra_shortfall,
        until_stage=until_stage)
    config_path = Path(config_path)
    run_dir = Path(run_dir)
    try:
        cfg = load_config(config_path)
    except (ValueError, OSError) as exc:
        # A config that is unreadable or fails its own validation is a
        # ConfigError for the pipeline's callers (the CLI prints it as a
        # clear message instead of a traceback).
        raise ConfigError(
            f"invalid config {config_path}: {exc}") from exc

    store = Store(run_dir)
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            # Stale takeover is tolerated, never silent (AGENTS.md section 6).
            print(f"warning: {warning}", file=sys.stderr)
        usage = UsageRecorder(usage_path(run_dir))
        handles = _build_handles(
            cfg, config_path, make_client,
            infra_retries=llm_infra_retries,
            infra_backoff_seconds=llm_infra_backoff_seconds,
            llm_timeout_sec=llm_timeout_sec)
        ctx = StageContext(
            run_dir=run_dir,
            store=store,
            config=cfg,
            usage=usage,
            handles=handles,
            workdir_root=run_dir / "work",
            # The operational settings, typed on the frozen context (the
            # stages read ctx.run_settings.<field> -- no more getattr
            # chains pasted into every stage file).
            run_settings=RunSettings(
                workers=workers,
                k1_stagger=k1_stagger,
                allow_infra_shortfall=allow_infra_shortfall),
        )

        def record(name: str, summary: Dict[str, Any]) -> Dict[str, Any]:
            row: Dict[str, Any] = {"stage": name,
                                   "summary": _json_safe(summary)}
            if summary.get("infra_shortfall_accepted"):
                # Never silent: the ledger line itself (not only the nested
                # summary) and stderr say the stage ran with holes.
                row["infra_shortfall_accepted"] = True
                print(f"warning: {name} accepted an infra shortfall "
                      "(--allow-infra-shortfall); missing units are listed "
                      "under infra_missing in its summary", file=sys.stderr)
            append_jsonl(ledger_path(run_dir), row)
            if on_stage is not None:
                on_stage(name, summary)
            return summary

        # The last stage index this call may execute (inclusive); stage 0
        # always runs, so the return value below is always a real summary.
        last = 6 if until_stage is None else until_stage

        # Stage 0 freezes the run's identity and derives the frozen train/test
        # split as task-id lists (full mode: both are the whole task set).
        last_summary = record("stage0_freeze", stage0_freeze.run(ctx))
        train_ids = last_summary["train"]
        test_ids = last_summary["test"]

        # Stage 1 runs both bare arms over the distillation pool (the train
        # id list) and computes the failed-task set F as prefixed task ids.
        if last >= 1:
            s1 = record("stage1_baseline", stage1_baseline.run(
                ctx, train_tasks=train_ids, test_tasks=test_ids))
            last_summary = s1

        if last >= 2:
            last_summary = record("stage2_segmentation", stage2_segmentation.run(
                ctx, failed_task_ids=s1["F"]))
        if last >= 3:
            last_summary = record("stage3_generation", stage3_generation.run(ctx))
        if last >= 4:
            last_summary = record("stage4_acceptance", stage4_acceptance.run(ctx))
        if last >= 5:
            last_summary = record("stage5_curation", stage5_curation.run(ctx))
        # Stage 6 takes the test set as Task objects (it runs them), rebuilt
        # from the frozen id list via the same D1 load stage 1 uses.
        if last >= 6:
            last_summary = record("stage6_test", stage6_test.run(
                ctx, test_tasks=_task_objects(cfg, test_ids)))
        return last_summary
    finally:
        lock.release()
