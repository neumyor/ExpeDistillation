"""TB smoke step 3: single-task LIVE rollout on node29 through the plugin.

Path: direct ``TBPlugin.run()`` calls (E1's per-unit entry point), one task,
one attempt, weak model qwen3.6-flash-distill. Four phases, each timed:

  1. bare      -- one full rollout; verifies trajectory reconstruction,
                  the pseudo-zero guard against the real verifier log, and
                  the usage ledger row (final_metrics totals).
  2. bare2     -- same run id again: must REUSE the finished result
                  (resumed="result"), no second submit, identical reward.
  3. skills*   -- fresh run id; the local process is killed mid-poll right
                  after launch (simulated crash), then the same run id is
                  called again: must REJOIN the live runner (resumed=
                  "alive") without resubmitting and finish the trial.
  4. evidence  -- the skills trial's trajectory step 0 must name
                  /opt/openclaw-skills/<task>/SKILL.md (the vendor agent
                  appends that to the instruction) and the trial dir must
                  hold skill_activation.json: the SKILL.md really reached
                  the sandbox (the handover's unverified link, its §6).

Usage:
    set -a; . ./.env; set +a
    .venv/bin/python scripts/smoke/smoke_tb_live.py [--task git-leak-recovery]

Options:
    --task   TB task name (default git-leak-recovery, ~3 min in the campaign)
    --model  MODEL_NAME (default qwen3.6-flash-distill)

Exit code 0 only if every phase passed its checks. Artifacts land under
runs/smoke_tb_live_<ts>/ (local) and $EXPEDISTILL_TB_ROOT (remote).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.benchmarks.base import Task  # noqa: E402
from expedistill.benchmarks.tb.plugin import TBPlugin  # noqa: E402
from expedistill.benchmarks.tb.remote import RemoteLink  # noqa: E402
from expedistill.infra.reliability import ConfigError  # noqa: E402
from expedistill.infra.usage import UsageRecorder  # noqa: E402
from expedistill.llm.call import ModelHandle  # noqa: E402

#: Two deliberately fake skill lines: content is irrelevant, this smoke
#: verifies the DELIVERY chain (upload -> sandbox /opt/openclaw-skills).
SKILLS_BLOCK = (
    "## Skill: verify before claiming\n"
    "- Always re-run the task's own test command before declaring done.\n"
    "## Skill: package mirrors\n"
    "- Prefer the configured pip index over the default one.\n"
    "- Never edit files outside the task directory.\n"
)


class _CountingLink:
    """Wraps the real link; counts submit (nohup) commands per phase."""

    def __init__(self, link):
        self._link = link
        self.submits = 0

    def run(self, cmd, timeout=None):
        if "nohup" in cmd:
            self.submits += 1
        return self._link.run(cmd, timeout=timeout)

    def get(self, remote, local, timeout=None):
        return self._link.get(remote, local, timeout=timeout)

    def put(self, local, remote, timeout=None):
        return self._link.put(local, remote, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._link, name)


class _CrashAfterFirstPoll:
    """Child-process link: hard-exits the process on the first PENDING poll,
    simulating a local crash mid-poll (the remote runner keeps going)."""

    def __init__(self, link):
        self._link = link
        self._crashed = False

    def run(self, cmd, timeout=None):
        out = self._link.run(cmd, timeout=timeout)
        if not self._crashed and "PENDING" in out[0]:
            self._crashed = True
            sys.stderr.write("[child] simulated crash mid-poll\n")
            sys.stderr.flush()
            os._exit(9)
        return out

    def __getattr__(self, name):
        return getattr(self._link, name)


def _task(name, token):
    return Task(task_id=name, instruction=name,
                meta={"task_name": name, "attempt_token": token})


def _run_once(plugin, task, handle, skills, workdir, label):
    start = time.time()
    outcome = plugin.run(task, handle, skills, workdir)
    elapsed = time.time() - start
    print(f"[{label}] status={outcome.status} "
          f"reward={outcome.meta.get('reward')} "
          f"resumed={outcome.meta.get('resumed')} "
          f"steps={len(outcome.steps)} "
          f"run_id={outcome.meta.get('run_id')} ({elapsed:.0f}s)")
    if outcome.meta.get("error"):
        print(f"[{label}] error: {outcome.meta['error']}")
    return outcome, elapsed


def _check(cond, label, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"    [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="git-leak-recovery")
    parser.add_argument("--model", default="qwen3.6-flash-distill")
    args = parser.parse_args()

    out_root = REPO / "runs" / f"smoke_tb_live_{time.strftime('%Y%m%d_%H%M%S')}"
    out_root.mkdir(parents=True, exist_ok=True)
    recorder = UsageRecorder(out_root / "usage.jsonl")
    handle = ModelHandle(name=args.model, role="smoke", client=None)
    link = _CountingLink(RemoteLink())
    results = []

    # ---- phase 1: bare live rollout ------------------------------------
    workdir = out_root / "bare"
    plugin = TBPlugin(link=link, usage_recorder=recorder)
    task = _task(args.task, "live1")
    outcome, _ = _run_once(plugin, task, handle, "", workdir, "bare")
    ok = _check(outcome.status in ("success", "failure"),
                "bare verdict is success/failure (not infra)",
                f"status={outcome.status}")
    ok &= _check(len(outcome.steps) > 0, "trajectory reconstructed to steps",
                 f"{len(outcome.steps)} steps, "
                 f"skipped={outcome.meta.get('skipped_steps')}")
    verifier = workdir / "verifier_test_stdout.txt"
    if outcome.status in ("success", "failure"):
        ok &= _check(verifier.exists() and "collected" in
                     verifier.read_text(encoding="utf-8", errors="replace"),
                     "verifier log shows pytest ran (no pseudo zero)")
    ok &= _check(link.submits == 1, "exactly one submit so far",
                 f"submits={link.submits}")
    results.append(("bare", ok))

    # ---- phase 2: same run id -> reuse the finished result --------------
    link.submits = 0
    outcome2, _ = _run_once(plugin, task, handle, "", workdir, "bare-resume")
    ok = _check(outcome2.meta.get("resumed") == "result",
                "second call resumed from the finished result",
                f"resumed={outcome2.meta.get('resumed')}")
    ok &= _check(outcome2.meta.get("reward") == outcome.meta.get("reward"),
                 "reward identical on reuse")
    ok &= _check(link.submits == 0, "no resubmit on reuse",
                 f"submits={link.submits}")
    ok &= _check(outcome2.meta.get("timing", {}).get("source")
                 == "result.json", "timing from result.json, not the fetch")
    results.append(("bare-resume", ok))

    # ---- phase 3: skills live rollout with a mid-poll crash -------------
    skills_workdir = out_root / "skills"
    skills_task = _task(args.task, "live2")
    child = os.fork()
    if child == 0:
        # Child: launch the skills trial, die at the first poll.
        crash_link = _CrashAfterFirstPoll(RemoteLink())
        TBPlugin(link=crash_link).run(skills_task, handle, SKILLS_BLOCK,
                                      skills_workdir)
        os._exit(0)  # unreachable: the link exits mid-poll
    _, status = os.waitpid(child, 0)
    print(f"[skills-crash] child exit status={status} "
          f"(expected 9 = simulated crash)")
    link.submits = 0
    outcome3, _ = _run_once(plugin, skills_task, handle, SKILLS_BLOCK,
                            skills_workdir, "skills")
    ok = _check(os.WEXITSTATUS(status) == 9, "child crashed mid-poll as "
                 "scripted", f"exit={os.WEXITSTATUS(status)}")
    ok &= _check(outcome3.meta.get("resumed") == "alive",
                 "rejoined the live runner without resubmitting",
                 f"resumed={outcome3.meta.get('resumed')}")
    ok &= _check(link.submits == 0, "no resubmit on rejoin",
                 f"submits={link.submits}")
    ok &= _check(outcome3.status in ("success", "failure"),
                 "skills verdict is success/failure",
                 f"status={outcome3.status}")
    ok &= _check(outcome3.meta.get("eval_mode") == "skills"
                 and outcome3.meta.get("skills_injected") is True,
                 "skills mode recorded in meta")
    results.append(("skills", ok))

    # ---- phase 4: skill delivery evidence in the remote trial dir -------
    real_link = RemoteLink()
    remote_root = real_link.root
    if remote_root.startswith("~"):
        remote_root = (real_link.run('printf %s "$HOME"')[0].strip()
                       + remote_root[1:])
    remote_root = remote_root.rstrip("/")
    out, err, rc = real_link.run(
        f"find {remote_root}/jobs/{outcome3.meta['run_id']} "
        f"-name skill_activation.json 2>/dev/null; "
        f"tr -d '\\n' < {remote_root}/skills/{outcome3.meta['run_id']}/"
        f"{args.task}/SKILL.md 2>/dev/null | head -c 80")
    activation = [ln for ln in out.splitlines() if ln.endswith(".json")]
    ok = _check(bool(activation), "trial dir holds skill_activation.json",
                activation[0] if activation else "missing")
    if activation:
        detail = real_link.run(f"cat {activation[0]}")[0].strip()
        ok &= _check("/opt/openclaw-skills" in detail,
                     "activation names the sandbox mount path", detail)
    traj_path = skills_workdir / "trajectory.json"
    delivered = False
    if traj_path.exists():
        steps0 = json.loads(traj_path.read_text(encoding="utf-8"))["steps"][0]
        expected = f"/opt/openclaw-skills/{args.task}/SKILL.md"
        delivered = expected in str(steps0.get("message", ""))
    ok &= _check(delivered, "trajectory step 0 tells the agent to read the "
                 "mounted SKILL.md")
    ok &= _check(SKILLS_BLOCK.splitlines()[0] in
                 real_link.run(f"cat {remote_root}/skills/"
                               f"{outcome3.meta['run_id']}/{args.task}/"
                               "SKILL.md")[0],
                 "remote SKILL.md is the skills block verbatim")
    results.append(("skills-evidence", ok))

    # ---- usage ledger ----------------------------------------------------
    totals = recorder.totals()["overall"]
    ok = _check(totals["request_count"] == 3,
                "three usage rows (bare, reuse, skills)", str(totals))
    ok &= _check((totals["in"] or 0) > 0 and (totals["out"] or 0) > 0,
                 "token totals recorded from final_metrics",
                 f"in={totals['in']} out={totals['out']}")
    results.append(("usage", ok))

    print("\n=== smoke summary ===")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"artifacts: {out_root}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigError as exc:
        print(f"config error: {exc}")
        raise SystemExit(2)
