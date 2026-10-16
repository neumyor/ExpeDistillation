"""Layer 8: the command-line entry point (the only method entry, AGENTS.md 5)
and, since R9 deleted stages/pipeline.py, the sequence loop itself.

Three subcommands:

* ``run`` -- one experiment run: load the config, take the run directory,
  freeze, then run the config's phase SEQUENCE in order -- one
  ``phase_execute.run`` / ``phase_learn.run`` call per sequence entry --
  and print one line per phase as it completes (progress comes from the
  loop's ``on_stage`` callback, i.e. from phases that really finished,
  not from a progress guess). The ledger row's key stays ``"stage"``
  (consumer compatibility): its value is ``p00:freeze`` for the freeze
  step, then ``pNN:<local name>`` for sequence entry i (0-based), with
  ``NN = i + 1`` -- the numbering tests/golden/semantics.py froze.
  ``--until-phase N`` stops after sequence entry N (0-based) for smoke
  tests and pilots; ``-1`` stops right after the freeze step (the task
  table and the manifest on disk, no phase dispatched -- the pre-run
  seeders' entry point); the run stays resumable (see the flag's help).
  Expected stops print a clear message instead of a traceback:

  - FrozenProtocolChanged / RunLockedError / ConfigError /
    ProviderRefusalError exit 1 (a human must change something; repeating
    the same command cannot help);
  - ModelOutputError (a call whose output stayed malformed after its repair
    budget escaped a stage, e.g. K5's whole-bank G7/G9 calls) exits 1 too:
    finished units are saved and re-running the same command retries the
    call, but a human should look first -- a supervisor must not loop on it;
  - InfrastructureError exits EXIT_INFRA_RESUMABLE (3): a stage finished
    every other unit and some units exhausted their infra retries; every
    finished unit is on disk, so re-running the same command resumes and
    retries only those. The distinct code lets a supervisor tell "resume
    me" apart from "fix me".

  CodeBugError and any unknown exception are code bugs and keep their
  traceback.
* ``summary`` -- read a finished (or running) run's progress ledger and usage
  totals WITHOUT recomputing anything (the ledger is the source of truth;
  re-running phases to check them would burn model calls). A resumed run
  appends a fresh line per phase, so the LAST line per phase wins. The
  completeness checklist comes from the manifest's frozen sequence, never
  a code constant. Usage is printed twice: the overall/by-role totals,
  then one line per (role, prompt) with the pre-registered token
  statistics of ``infra.usage.UsageStats.by_role_prompt`` (medians over
  ok rows, totals over all rows, unknown never coerced to 0). Exit 1
  when the ledger shows the run is incomplete (a phase never completed).
* ``assemble-reuse`` -- assemble a fresh run directory from a finished
  source run and a reuse config (the TB frozen-split reuse experiment).
  ``--level`` picks the reuse level (revision 3 of the integration plan):
  ``bank`` (default) copies the source run's stage 1-5 train-lineage
  artifacts and the filtered bank (zero model calls downstream), while
  ``baseline`` copies ONLY the train-side stage-1 trajectories (plus the
  accepted stage-1 infra shortfall) and stages 2-6 run fresh under the
  current protocol. Both levels validate the identity (the ruling-4
  whitelist diff, respectively revision 3's pinned minimum), copy under
  the run lock, assert the negative list and write ASSEMBLED_FROM.md
  provenance. Prints the returned per-class counts and checksums as one
  JSON line. No model calls; its expected failures (ConfigError, a locked
  run directory, an identity mismatch) exit 1 with a clear message, the
  same contract as ``run``.
"""

import argparse
import importlib
import json
import math
import shlex
import sys
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from expedistill.stages.assemble_reuse import LEVELS, assemble_reuse
from expedistill.infra.manifest import FrozenProtocolChanged, load_or_none
from expedistill.core.config import (
    FREEZE_LABEL,
    ExecutePhaseSpec,
    PhaseInstance,
    ROLE_DEFAULTS,
    load_config,
    phase_label,
)
from expedistill.core.config import ConfigError as CoreConfigError
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.infra.runlock import RunLock, RunLockedError
from expedistill.infra.store import Store, append_jsonl, read_jsonl
from expedistill.infra.usage import UsageRecorder, UsageStats
from expedistill.llm.call import make_handle
from expedistill.llm import providers
from expedistill.stages import stage0_freeze
from expedistill.stages.context import RunSettings, StageContext

__all__ = ["EXIT_INFRA_RESUMABLE", "main"]

#: Exit code of a run stopped by infra exhaustion: resumable as-is.
EXIT_INFRA_RESUMABLE = 3

#: Cap for the one-line stage summary printed by ``run``; the full summary is
#: in the ledger, the console line is a progress signal.
_LINE_LIMIT = 240

#: The progress ledger, one appended line per completed step (freeze or
#: phase). The KEY stays ``"stage"`` for consumer compatibility (the
#: implementation contract, section 6); the VALUE is the pNN label.
LOG_NAME = "pipeline_log.jsonl"

#: B7 usage ledger (llm_requests/usage.jsonl, the same path the K-composite
#: tests construct; the module docstring of infra.usage.py pins it).
USAGE_PATH = ("llm_requests", "usage.jsonl")


def _stage_line(name: str, summary: Dict[str, object]) -> str:
    """One compact line per step: label plus its summary as sorted JSON."""
    try:
        payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr(summary)
    if len(payload) > _LINE_LIMIT:
        payload = payload[:_LINE_LIMIT - 3] + "..."
    return f"{name}: {payload}"


def usage_path(run_dir: Path) -> Path:
    """The B7 usage ledger path inside ``run_dir``."""
    return Path(run_dir).joinpath(*USAGE_PATH)


def ledger_path(run_dir: Path) -> Path:
    """The progress ledger path inside ``run_dir``."""
    return Path(run_dir) / LOG_NAME


def _json_safe(summary: Dict[str, Any]) -> Dict[str, Any]:
    """A ledger-writable copy of ``summary``.

    The ledger is a progress record, not an object store: a phase summary
    that carries a non-JSON value (an object a later phase deems useful
    to return) must never crash the run AFTER the phase's work was done
    and persisted. ``default=repr`` keeps everything JSON-native intact
    and renders anything else as its repr. A summary that needs full
    fidelity has its own summary file (every phase writes one); the
    ledger only needs to be readable.
    """
    return json.loads(json.dumps(summary, default=repr))


def _default_client(model_name: str,
                    timeout_sec: float = 300.0) -> Any:
    """The real-endpoint builder: one client per unique model, from the
    provider registry (``llm.providers``).

    The provider NAME comes from ``EXPEDISTILL_LLM_PROVIDER`` (default
    ``openai_compat``); the adapter gives the builder's client the
    ``make_client(model_name)`` shape, and building one per unique model
    keeps the documented one-client-per-model layout, so a later
    per-model endpoint needs no loop change.
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
    run stops while handles are being built, before the freeze step locks
    anything and before any client is built. Budget boundary: only
    requests through call_model are capped. TB's remote execution arm
    does NOT go through call_model (the sandbox runs the model behind the
    TBENCH_* env knobs), so executor output budgets do not apply to that
    arm -- real TB-side budgets live in those env knobs, not in this
    table.

    Every handle carries the same per-call infra retry settings (call_model
    resends a request that failed with InfrastructureError); they are run
    settings, not method parameters, so they never enter the manifest. The
    same goes for ``llm_timeout_sec``, the per-request client timeout the
    default builder bakes into its clients (an injected ``make_client``
    owns its own timeout).

    A role missing from the config's role table is a ConfigError naming
    the role AND the config file (the reader must know which experiment
    is broken, not just that one is). The check runs before any client is
    built so a bad config fails without touching the endpoint.
    """
    for role in ROLE_DEFAULTS:
        if not cfg.models.get(role):
            raise ConfigError(
                f"model role {role!r} is missing from the role table of "
                f"config {config_path}")
    builder = make_client or partial(_default_client,
                                     timeout_sec=llm_timeout_sec)
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
            # a fresh dict per handle, empty for models the config does
            # not list (never a shared mutable default).
            extra_body=dict(cfg.model_options.get(model_name, {})),
            max_tokens=budget,
            infra_retries=infra_retries,
            infra_backoff_seconds=infra_backoff_seconds,
        )

    return {role: handle_for(role) for role in ROLE_DEFAULTS}


def _check_run_settings(*, workers: Any, llm_infra_retries: Any,
                        llm_infra_backoff_seconds: Any, llm_timeout_sec: Any,
                        k1_stagger: Any,
                        allow_infra_shortfall: Any) -> None:
    """Reject bad run settings as ConfigError before anything runs.

    Each value would otherwise only be rejected deep inside K1 or
    ModelHandle (a ValueError, i.e. a CLI traceback) after the freeze
    step had already locked the run -- or, for ``workers``, not at all.
    ``until_phase`` is NOT checked here: its bound is the frozen
    sequence's length, which only exists after the config loads, so the
    loop checks it right after load_config (the implementation contract
    section 6, T3).
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
        problems.append("llm_timeout_sec must be an int or a float > 0, "
                        f"got {timeout!r}")
    stagger = k1_stagger
    if (isinstance(stagger, bool) or not isinstance(stagger, (int, float))
            or not math.isfinite(stagger) or stagger < 0):
        problems.append("k1_stagger must be a finite number >= 0, "
                        f"got {stagger!r}")
    if not isinstance(allow_infra_shortfall, bool):
        problems.append("allow_infra_shortfall must be a bool, "
                        f"got {allow_infra_shortfall!r}")
    if problems:
        raise ConfigError("invalid run setting(s): " + "; ".join(problems))


def _phase_driver(kind: str) -> Any:
    """The sequence entry's driver module: ``stages.phase_execute`` for an
    execute phase, ``stages.phase_learn`` for a learn phase.

    Imported per call (and so never at module import): a bad import must
    name the phase that wanted it, not break every CLI invocation.
    ``importlib.import_module`` (not a ``from`` import) so a sys.modules
    entry wins: that entry is the tests' fake-driver seam
    (tests/unit/test_w2c_run_loop.py), and once the real driver was
    imported elsewhere in the process a ``from expedistill.stages import
    phase_execute`` would return the bound package attribute around the
    seam.
    """
    return importlib.import_module(
        "expedistill.stages.phase_execute" if kind == "execute"
        else "expedistill.stages.phase_learn")


def run_experiment(
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
    until_phase: Optional[int] = None,
) -> Dict[str, Any]:
    """Freeze, then run the config's phase sequence over ``run_dir``;
    return the LAST EXECUTED step's summary.

    ``make_client(model_name) -> client`` overrides the default
    ``_default_client`` (``OpenAICompatClient.from_env()``; tests inject
    FakeClients here). Either way ONE client is built per unique model
    name in the role table.
    ``on_stage(label, summary)`` fires after each step completes, right
    after its ledger line is written -- the CLI uses it to print
    progress.

    ``until_phase`` stops the loop after that sequence entry
    (inclusive, 0-based); ``-1`` stops right after the freeze step (no
    phase dispatched); ``None`` runs the whole sequence. The freeze
    step always runs, so the return value is always a real summary. It
    is operational, like ``workers``: not frozen into the manifest, and
    a resume may change it freely. A stopped run simply replays the
    sequence from the start next time, and each phase skips its
    finished units (the existing resume model), so continuing means
    re-running with a higher ``until_phase`` (or without one) at zero
    extra cost for finished work.

    ``llm_infra_retries`` / ``llm_infra_backoff_seconds`` go onto every
    ModelHandle: a model request that fails on infrastructure is resent
    up to that many times with exponential backoff (capped in llm.call)
    before the unit counts as infra-failed. Like ``workers`` they are
    operational: they change how hard the run tries, not what it
    measures, so they live outside ExperimentConfig and the frozen
    manifest and may differ between a run and its resume.

    ``llm_timeout_sec`` is the per-request client timeout the default
    client builder bakes into its clients (an injected ``make_client``
    owns its own timeout). It is operational for the same reason.

    ``allow_infra_shortfall`` is NOT operational: accepting holes changes
    what is measured. It lets the execute phases proceed over units that
    exhausted their infra retries instead of raising
    InfrastructureError, and each phase records the accepted unit ids on
    disk as a durable per-run decision that every later call honours
    with or without the flag; the holes stay counted as infra, the phase
    summary records ``infra_shortfall_accepted: true``, its ledger line
    carries the same flag at top level, and a warning goes to stderr.
    The flag itself is not frozen: it only decides whether NEW holes are
    accepted.

    ``k1_stagger`` is the per-unit random startup jitter in seconds
    passed to every K1 call (default 0): it spreads the ssh connections
    of parallel unit dispatches, and it is operational like ``workers``.

    The run lock is taken for the whole loop; a stale lock is taken over
    with its warning printed to stderr (RunLock names the dead holder).
    """
    _check_run_settings(
        workers=workers, llm_infra_retries=llm_infra_retries,
        llm_infra_backoff_seconds=llm_infra_backoff_seconds,
        llm_timeout_sec=llm_timeout_sec,
        k1_stagger=k1_stagger,
        allow_infra_shortfall=allow_infra_shortfall)
    config_path = Path(config_path)
    run_dir = Path(run_dir)
    try:
        cfg = load_config(config_path)
    except (ValueError, OSError) as exc:
        # A config that is unreadable or fails its own validation is a
        # ConfigError for the CLI (printed as a clear message instead of
        # a traceback).
        raise ConfigError(
            f"invalid config {config_path}: {exc}") from exc
    # The bound is the frozen sequence's length, so this check can only
    # run after the config loads (the implementation contract, T3). -1
    # is the freeze-only stop (W3-B): the freeze step is p00 and the
    # sequence starts at p01, so "before the first phase" is -1 -- the
    # pre-run seeders need a manifest on disk before any phase dispatches.
    if until_phase is not None \
            and not -1 <= until_phase < len(cfg.sequence):
        raise ConfigError(
            f"until_phase must be -1 (run the freeze step only: the task "
            "table and manifest land on disk and no phase runs), an int "
            f"in [0, {len(cfg.sequence) - 1}] (the frozen sequence has "
            f"{len(cfg.sequence)} phases), or be omitted, got "
            f"{until_phase!r}")

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
            # phase drivers read ctx.run_settings.<field>).
            run_settings=RunSettings(
                workers=workers,
                k1_stagger=k1_stagger,
                allow_infra_shortfall=allow_infra_shortfall),
        )

        def record(name: str, summary: Dict[str, Any]) -> Dict[str, Any]:
            row: Dict[str, Any] = {"stage": name,
                                   "summary": _json_safe(summary)}
            if summary.get("infra_shortfall_accepted"):
                # Never silent: the ledger line itself (not only the
                # nested summary) and stderr say the phase ran with holes.
                row["infra_shortfall_accepted"] = True
                print(f"warning: {name} accepted an infra shortfall "
                      "(--allow-infra-shortfall); missing units are listed "
                      "under infra_missing in its summary", file=sys.stderr)
            append_jsonl(ledger_path(run_dir), row)
            if on_stage is not None:
                on_stage(name, summary)
            return summary

        # The freeze step always runs, so the return value below is
        # always a real summary.
        last_summary = record(FREEZE_LABEL, stage0_freeze.run(ctx))
        train_ids = last_summary["train"]
        test_ids = last_summary["test"]

        for index, name in enumerate(cfg.sequence):
            if until_phase is not None and index > until_phase:
                break
            spec = cfg.phases[name]
            label = phase_label(index, name)
            task_ids: tuple = ()
            if isinstance(spec, ExecutePhaseSpec):
                task_ids = tuple(train_ids if spec.tasks == "train"
                                else test_ids)
            phase = PhaseInstance(index=index, name=name, label=label,
                                  spec=spec, task_ids=task_ids)
            result = _phase_driver(spec.kind).run(ctx, phase)
            if result.label != label:
                raise ConfigError(
                    f"the {spec.kind} phase driver answered for "
                    f"{result.label!r} but was asked to run {label!r}")
            last_summary = record(label, result.summary)
        return last_summary
    finally:
        lock.release()


def _fmt_stat(value: object) -> str:
    """Compact field text for the usage table: unknown (None) is '-', never
    a fake 0; integral floats (statistics.median of an even count) print as
    integers, ratios stay bounded to 4 significant digits."""
    if value is None:
        return "-"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.4g}"
    return str(value)


def _usage_role_prompt_line(role: str, prompt: str,
                            stats: Dict[str, object]) -> str:
    """One bounded line per (role, prompt): the pre-registered metrics of
    infra.usage.UsageStats as key=value fields (dict fields as JSON)."""
    parts = [f"calls={stats['n_calls']}", f"ok={stats['n_ok']}",
             f"err={stats['n_error']}"]
    if stats["n_error"]:
        parts.append("errors=" + json.dumps(stats["errors_by_type"],
                                            ensure_ascii=False,
                                            sort_keys=True))
    parts += [
        f"in_med={_fmt_stat(stats['in_median'])}",
        f"in_max={_fmt_stat(stats['in_max'])}",
        f"in_tot={stats['total_in']}",
        f"out_med={_fmt_stat(stats['out_median'])}",
        f"out_max={_fmt_stat(stats['out_max'])}",
        f"out_tot={stats['total_out']}",
        f"cap_viol={stats['cap_violations']}",
        f"len_trunc={stats['length_truncations']}",
        f"est_in_med={_fmt_stat(stats['estimated_in_median'])}",
        f"in_over_est={_fmt_stat(stats['in_over_estimated_median'])}",
    ]
    if stats["unknown"]:
        parts.append("unknown=" + json.dumps(stats["unknown"],
                                             ensure_ascii=False,
                                             sort_keys=True))
    return f"{role} | {prompt} | " + " ".join(parts)


def _cmd_run(args: argparse.Namespace) -> int:
    def on_stage(name: str, summary: Dict[str, object]) -> None:
        print(_stage_line(name, summary))

    run_experiment(
        config_path=args.config,
        run_dir=args.run_dir,
        workers=args.workers,
        on_stage=on_stage,
        llm_infra_retries=args.llm_infra_retries,
        llm_infra_backoff_seconds=args.llm_infra_backoff,
        llm_timeout_sec=args.llm_timeout,
        allow_infra_shortfall=args.allow_infra_shortfall,
        k1_stagger=args.k1_stagger,
        until_phase=args.until_phase,
    )
    return 0


def _resume_command(args: argparse.Namespace) -> str:
    """The ``run`` command line that resumes this run (same arguments),
    shell-quoted so it can be pasted as-is."""
    argv = [
        "expedistill", "run",
        "--config", str(args.config),
        "--run-dir", str(args.run_dir),
        "--workers", str(args.workers),
        "--llm-infra-retries", str(args.llm_infra_retries),
        "--llm-infra-backoff", str(args.llm_infra_backoff),
        "--llm-timeout", str(args.llm_timeout),
        "--k1-stagger", str(args.k1_stagger),
    ]
    if args.until_phase is not None:
        # Kept so re-running the hint stops at the same phase again; a run
        # stopped early continues by re-running with a higher --until-phase.
        argv += ["--until-phase", str(args.until_phase)]
    if args.allow_infra_shortfall:
        argv.append("--allow-infra-shortfall")
    return " ".join(shlex.quote(part) for part in argv)


def _resume_hint(args: argparse.Namespace) -> str:
    """The resume command for ``run``; only ``run`` executes units, the
    fallback keeps the message sane if another subcommand ever raises."""
    return (_resume_command(args) if args.command == "run"
            else "the same command")


def _cmd_assemble_reuse(args: argparse.Namespace) -> int:
    # The default level IS the historical assembly, so the default call
    # keeps the historical three-argument shape; only an explicitly
    # requested non-default level changes the contract being invoked.
    if args.level == "bank":
        summary = assemble_reuse(
            source_run=args.source_run,
            config_path=args.config,
            run_dir=args.run_dir,
        )
    else:
        summary = assemble_reuse(
            source_run=args.source_run,
            config_path=args.config,
            run_dir=args.run_dir,
            level=args.level,
        )
    # One compact JSON line, the same style as the per-stage lines: the
    # console output is a progress record, ASSEMBLED_FROM.md at the run
    # root is the durable provenance record.
    print("assemble-reuse: " + json.dumps(
        summary, ensure_ascii=False, sort_keys=True))
    return 0


def _expected_labels(run_dir: Path) -> Optional[List[str]]:
    """The completeness checklist: the manifest's frozen sequence as pNN
    labels (``p00:freeze`` first). ``None`` when the run has no readable
    v4 manifest -- the caller reports that as its own error, because a
    code constant must never be the checklist (R9: the sequence is
    config, not code)."""
    manifest = load_or_none(run_dir)
    if manifest is None:
        return None
    sequence = (manifest.get("config") or {}).get("sequence")
    if not isinstance(sequence, list) or not sequence:
        return None
    return [FREEZE_LABEL] + [phase_label(index, name)
                             for index, name in enumerate(sequence)]


def _cmd_summary(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    expected = _expected_labels(run_dir)
    if expected is None:
        print(
            f"error: {run_dir} has no readable manifest with a frozen "
            "sequence; summary's completeness check reads the frozen "
            "sequence, never a code constant",
            file=sys.stderr)
        return 1
    ledger = ledger_path(run_dir)
    try:
        lines = read_jsonl(ledger)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {ledger}: {exc}", file=sys.stderr)
        return 1

    # A resumed run appends a new line per phase; the last line per phase
    # is that phase's current state.
    latest: Dict[str, Dict[str, object]] = {}
    for row in lines:
        name = row.get("stage")
        summary = row.get("summary")
        if isinstance(name, str) and isinstance(summary, dict):
            latest[name] = summary

    for name in expected:
        summary = latest.get(name)
        if summary is None:
            print(f"{name}: (not completed)")
        else:
            print(_stage_line(name, summary))

    totals = UsageRecorder(usage_path(run_dir)).totals()
    print("usage: " + json.dumps(totals, ensure_ascii=False, sort_keys=True))

    # Per-(role, prompt) token statistics (the token-budget observability
    # instrument): one bounded line per group; nothing extra when the ledger
    # is empty or absent.
    by_role_prompt = UsageStats(usage_path(run_dir)).by_role_prompt()
    if by_role_prompt:
        print("usage by (role, prompt):")
        for role, prompt in sorted(by_role_prompt):
            print("  " + _usage_role_prompt_line(
                role, prompt, by_role_prompt[(role, prompt)]))

    missing = [name for name in expected if name not in latest]
    if missing:
        print(
            f"error: run looks incomplete, missing phases: {', '.join(missing)}",
            file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expedistill",
        description="ExpeDistillation: distill strong-model trajectories into "
                    "a skill bank for a weak model.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one experiment (the phase sequence)")
    run.add_argument("--config", required=True, type=Path,
                     help="experiment config YAML (configs/experiments/)")
    run.add_argument("--run-dir", required=True, type=Path,
                     help="run directory (created; holds manifest, phases, "
                          "ledger)")
    run.add_argument("--workers", type=int, default=1,
                     help="parallel workers for execution units (default 1)")
    # Operational, like --workers: not frozen into the manifest, so a resume
    # may change them. (--allow-infra-shortfall below is the exception in
    # kind: what it accepts is recorded durably in the run directory.)
    run.add_argument("--llm-infra-retries", type=int, default=3,
                     help="per-request resends of a model call that failed on "
                          "infrastructure (default 3)")
    run.add_argument("--llm-infra-backoff", type=float, default=5.0,
                     help="base seconds of the exponential backoff between "
                          "those resends (default 5.0)")
    run.add_argument("--llm-timeout", type=float, default=300,
                     help="per-request client timeout in seconds for the LLM "
                          "endpoint (default 300); reasoning-effort=max calls "
                          "legitimately take minutes, so lower it only when "
                          "the endpoint is unstable and hung requests would "
                          "otherwise burn the full timeout each")
    run.add_argument("--k1-stagger", type=float, default=0.0,
                     help="per-unit random startup jitter in seconds "
                          "(default 0); spreads the ssh connections of "
                          "parallel unit dispatches")
    run.add_argument("--until-phase", type=int, default=None, metavar="N",
                     help="run the freeze step plus sequence entries 0 "
                          "through N, inclusive -- N is the sequence's "
                          "0-based index (default: the whole sequence); "
                          "N=-1 runs the freeze step ONLY: the task table "
                          "and manifest land on disk and no phase runs "
                          "(the pre-run seeders' entry point). "
                          "Operational, not frozen: a stopped run simply "
                          "replays from the first phase next time and each "
                          "phase skips its finished units, so continue by "
                          "re-running with a higher --until-phase (or "
                          "without the flag)")
    run.add_argument("--allow-infra-shortfall", action="store_true",
                     help="let the execute phases proceed over execution "
                          "units that exhausted their infra retries "
                          "(counted as infra, flagged in the summaries) "
                          "instead of stopping for a resume; the accepted "
                          "units are recorded in the run directory and "
                          "stay accepted (never re-executed) on every "
                          "later resume")
    run.set_defaults(func=_cmd_run)

    assemble = sub.add_parser(
        "assemble-reuse",
        help="assemble a fresh run from a finished source run's "
             "train-lineage artifacts (zero model calls)")
    assemble.add_argument("--source-run", required=True, type=Path,
                          help="the finished source run directory "
                               "(its manifest defines the protocol the "
                               "reuse config must match)")
    assemble.add_argument("--config", required=True, type=Path,
                          help="the reuse experiment config YAML "
                               "(configs/experiments/)")
    assemble.add_argument("--run-dir", required=True, type=Path,
                          help="fresh run directory to assemble (created; "
                               "must not contain a manifest -- the "
                               "pipeline freezes one on its first start)")
    assemble.add_argument("--level", choices=LEVELS, default="bank",
                          help="reuse level (revision 3): 'bank' (default) "
                               "copies the source run's stage 1-5 "
                               "train-lineage artifacts and the filtered "
                               "bank -- zero model calls downstream; "
                               "'baseline' copies ONLY the train-side "
                               "stage-1 trajectories (plus the accepted "
                               "stage-1 infra shortfall) and stages 2-6 "
                               "run fresh under the current protocol")
    assemble.set_defaults(func=_cmd_assemble_reuse)

    summary = sub.add_parser(
        "summary", help="print a run's stage summaries and usage totals")
    summary.add_argument("--run-dir", required=True, type=Path,
                         help="the run directory to read")
    summary.set_defaults(func=_cmd_summary)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FrozenProtocolChanged, RunLockedError, ConfigError,
            CoreConfigError) as exc:
        # CoreConfigError (core.config) is what the methods layer's
        # param contracts raise: a bad method parameter is a clean stop,
        # never a traceback. Anything else that leaks a bare ValueError
        # stays a traceback -- masking code bugs is worse than the noise.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ProviderRefusalError as exc:
        print(f"error: the model provider refused a request: {exc}\n"
              "stopped; repeating the same request cannot help -- check the "
              "model name, credentials, and the refused content",
              file=sys.stderr)
        return 1
    except ModelOutputError as exc:
        print(f"error: the model's output stayed malformed after its repair "
              f"budget: {exc}\n"
              "finished units are saved; inspect the logged responses, then "
              "re-run the same command to retry the failed call:\n"
              f"  {_resume_hint(args)}",
              file=sys.stderr)
        return 1
    except InfrastructureError as exc:
        print(f"error: {exc}\n"
              "finished units are saved; re-run the same command to resume "
              f"(only the failed units are retried):\n  {_resume_hint(args)}",
              file=sys.stderr)
        return EXIT_INFRA_RESUMABLE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
