"""A stand-in for swebench-eval's ``scripts/run_unit.py`` (the bridge).

The SWE-bench plugin tests install it as ``<root>/scripts/run_unit.py`` with
``<root>/.venv/bin/python`` pointing at the test interpreter. It speaks the
bridge's contract (docs/decisions/swebench_plugin_20261118.md) without a
sandbox or a model, steered by environment variables:

- ``FAKE_BRIDGE_LOG``: a JSON-lines file; every call appends its argv, the
  texts of ``--skill-block-file`` and ``--skill-rule-file`` and the files
  under ``--skill-dir``;
- ``FAKE_BRIDGE_CASE``: the recorded unit directory ``run`` copies to
  ``<out>/<instance>/rep0/`` (default: the ``resolved`` fixture);
- ``FAKE_BRIDGE_MODE``: ``final`` (default), ``infra`` (exit 0 with status
  infra), ``config`` (exit 2), ``crash`` (exit 1) or ``killed`` (the
  process kills itself with SIGKILL).

``describe`` refuses (exit 2) any instance id starting with ``unknown``.
Standard library only: it runs outside the package's environment.
"""

import argparse
import json
import os
import shutil
import signal
import sys
from pathlib import Path

FIXTURES = Path(os.environ.get("FAKE_BRIDGE_FIXTURES", "."))


def _log(args, extra):
    path = os.environ.get("FAKE_BRIDGE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(argv=sys.argv[1:], **extra)) + "\n")


def _describe(args):
    _log(args, {})
    unknown = [i for i in args.instance if i.startswith("unknown")]
    if unknown:
        print(f"instances not in split_v1.json: {unknown}", file=sys.stderr)
        return 2
    print("describing", len(args.instance), "instances")
    print(json.dumps([{"instance_id": i, "repo": i.split("__")[0],
                       "problem_statement": f"Problem statement of {i}."}
                      for i in args.instance]))
    return 0


def _run(args):
    block = (Path(args.skill_block_file).read_text(encoding="utf-8")
             if args.skill_block_file else None)
    rule = (Path(args.skill_rule_file).read_text(encoding="utf-8")
            if args.skill_rule_file else None)
    files = (sorted(str(p.relative_to(args.skill_dir))
                    for p in Path(args.skill_dir).rglob("*") if p.is_file())
             if args.skill_dir else None)
    out_existed = Path(args.out).exists()
    _log(args, {"block": block, "rule": rule, "skill_files": files,
                "out_existed": out_existed})
    mode = os.environ.get("FAKE_BRIDGE_MODE", "final")
    print("fake bridge: starting", args.instance, file=sys.stderr)
    if mode == "config":
        print(f"unknown model {args.model!r}", file=sys.stderr)
        return 2
    if mode == "killed":
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == "crash":
        print("Traceback (most recent call last):\nRuntimeError: boom",
              file=sys.stderr)
        return 1
    unit_dir = Path(args.out) / args.instance / "rep0"
    case = Path(os.environ.get("FAKE_BRIDGE_CASE") or FIXTURES / "resolved")
    shutil.copytree(case, unit_dir)
    unit = json.loads((unit_dir / "unit.json").read_text(encoding="utf-8"))
    payload = {key: unit.get(key) for key in (
        "resolved", "exit_status", "score_status", "n_steps",
        "prompt_tokens", "completion_tokens", "agent_wall_sec", "error")}
    payload.update(status="final", unit_dir=str(unit_dir))
    if mode == "infra":
        payload.update(status="infra", resolved=None,
                       score_status="infra_error", error="sandbox lost")
    print("agent finished")
    print(json.dumps(payload))
    return 0


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    describe = commands.add_parser("describe")
    describe.add_argument("--instance", action="append", required=True)
    run = commands.add_parser("run")
    run.add_argument("--instance", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--max-input-tokens", type=int, required=True)
    run.add_argument("--skill-block-file")
    run.add_argument("--skill-rule-file")
    run.add_argument("--skill-dir")
    args = parser.parse_args()
    return _describe(args) if args.command == "describe" else _run(args)


if __name__ == "__main__":
    sys.exit(main())
