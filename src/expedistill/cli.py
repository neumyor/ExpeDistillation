"""Layer 7: the command-line entry point (the only method entry, AGENTS.md 5).

Three subcommands:

* ``run`` -- one experiment run: load the config, take the run directory, and
  print one line per stage as it completes (progress comes from the
  pipeline's ``on_stage`` callback, i.e. from stages that really finished,
  not from a progress guess). ``--until-stage N`` stops after stage N for
  smoke tests and pilots; the run stays resumable (see the flag's help).
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
  re-running stages to check them would burn model calls). A resumed run
  appends a fresh line per stage, so the LAST line per stage wins. Usage is
  printed twice: the overall/by-role totals, then one line per (role, prompt)
  with the pre-registered token statistics of
  ``infra.usage.UsageStats.by_role_prompt`` (medians over ok rows, totals
  over all rows, unknown never coerced to 0). Exit 1
  when the ledger shows the run is incomplete (a stage never completed).
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
import json
import shlex
import sys
from pathlib import Path
from typing import Dict, List, Optional

from expedistill.stages.assemble_reuse import LEVELS, assemble_reuse
from expedistill.infra.manifest import FrozenProtocolChanged
from expedistill.core.config import ConfigError as CoreConfigError
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.infra.runlock import RunLockedError
from expedistill.infra.store import read_jsonl
from expedistill.infra.usage import UsageRecorder, UsageStats
from expedistill.stages.pipeline import (
    STAGE_NAMES,
    ledger_path,
    run_pipeline,
    usage_path,
)

__all__ = ["EXIT_INFRA_RESUMABLE", "main"]

#: Exit code of a run stopped by infra exhaustion: resumable as-is.
EXIT_INFRA_RESUMABLE = 3

#: Cap for the one-line stage summary printed by ``run``; the full summary is
#: in the ledger, the console line is a progress signal.
_LINE_LIMIT = 240


def _stage_line(name: str, summary: Dict[str, object]) -> str:
    """One compact line per stage: name plus its summary as sorted JSON."""
    try:
        payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr(summary)
    if len(payload) > _LINE_LIMIT:
        payload = payload[:_LINE_LIMIT - 3] + "..."
    return f"{name}: {payload}"


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

    run_pipeline(
        config_path=args.config,
        run_dir=args.run_dir,
        workers=args.workers,
        on_stage=on_stage,
        llm_infra_retries=args.llm_infra_retries,
        llm_infra_backoff_seconds=args.llm_infra_backoff,
        llm_timeout_sec=args.llm_timeout,
        allow_infra_shortfall=args.allow_infra_shortfall,
        k1_stagger=args.k1_stagger,
        until_stage=args.until_stage,
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
    if args.until_stage is not None:
        # Kept so re-running the hint stops at the same stage again; a run
        # stopped early continues by re-running with a higher --until-stage.
        argv += ["--until-stage", str(args.until_stage)]
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


def _cmd_summary(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    ledger = ledger_path(run_dir)
    try:
        lines = read_jsonl(ledger)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {ledger}: {exc}", file=sys.stderr)
        return 1

    # A resumed run appends a new line per stage; the last line per stage is
    # that stage's current state.
    latest: Dict[str, Dict[str, object]] = {}
    for row in lines:
        name = row.get("stage")
        summary = row.get("summary")
        if isinstance(name, str) and isinstance(summary, dict):
            latest[name] = summary

    for name in STAGE_NAMES:
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

    missing = [name for name in STAGE_NAMES if name not in latest]
    if missing:
        print(
            f"error: run looks incomplete, missing stages: {', '.join(missing)}",
            file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expedistill",
        description="ExpeDistillation: distill strong-model trajectories into "
                    "a skill bank for a weak model.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one experiment (stages 0-6)")
    run.add_argument("--config", required=True, type=Path,
                     help="experiment config YAML (configs/experiments/)")
    run.add_argument("--run-dir", required=True, type=Path,
                     help="run directory (created; holds manifest, stages, ledger)")
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
    run.add_argument("--until-stage", type=int, default=None, metavar="N",
                     help="run only stages 0 through N, inclusive (default: "
                          "all stages). Operational, not frozen: a stopped "
                          "run simply re-runs from stage 0 next time and each "
                          "stage skips its finished units, so continue by "
                          "re-running with a higher --until-stage (or "
                          "without the flag)")
    run.add_argument("--allow-infra-shortfall", action="store_true",
                     help="let stages 1 and 6 (and stage 4 in live_run "
                          "mode, per round) proceed over execution units "
                          "that exhausted their infra retries (counted as "
                          "infra, flagged in the summaries) instead of "
                          "stopping for a resume; the accepted units are "
                          "recorded in the run directory and stay accepted "
                          "(never re-executed) on every later resume")
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
