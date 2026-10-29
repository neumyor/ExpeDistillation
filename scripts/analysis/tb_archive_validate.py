#!/usr/bin/env python3
"""Validate expedistill's TB data modules against the real campaign archive.

Runs four checks on data sampled from the benchmark host's campaign archive (see
scripts/analysis/tb_archive_sample.py for how the sample was fetched):

V1 trajectory reconstruction: expedistill.benchmarks.tb.trajectory.reconstruct
    on every sampled agent/trajectory.json; verifies each kept step's
    action/observation render byte-exactly from the raw record, that long
    observations survive verbatim, and that classify_result's status agrees
    with result.json / the campaign's audit verdict.
V2 pseudo-zero detection: classify_result vs the campaign's audit status for
    the sampled fake-zero / infra / keep-model trials.
V3 metrics cross-check: recompute avg@5 / pass@5 from the archive manifests
    with blocks.metrics and reconcile with the official numbers.
V4 view rendering: render reconstructed trajectories through the TB plugin's
    view rules; check truncation markers and token sizes.

Usage:
    PYTHONPATH=src python3 scripts/analysis/tb_archive_validate.py <tbval-root>
Input:  <tbval-root>/{manifest_qwen.tsv, manifest_dpsk.tsv, summary.json,
        audit_20261011.json, sample/...}
Output: report on stdout; exit 1 if any hard check fails.
"""

import json
import re
import sys
from pathlib import Path

from expedistill.benchmarks.tb.plugin import TBPlugin, classify_result
from expedistill.benchmarks.tb.trajectory import reconstruct, total_usage
from expedistill.blocks.metrics import compute_task_metrics, summarize
from expedistill.blocks.view import render_steps_view
from expedistill.core.models import Trajectory
from expedistill.inject.skills import estimate_tokens

FAILURES = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print(f"  FAIL {msg}")
    return cond


# Independent re-derivation of the rendering rules (NOT the module under test).
def raw_action(step):
    calls = step.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    out = []
    for call in calls:
        if not isinstance(call, dict):
            return None
        fn = call.get("function_name")
        if not isinstance(fn, str) or not fn:
            return None
        try:
            out.append(f"{fn}({json.dumps(call.get('arguments', {}), ensure_ascii=False, sort_keys=True)})")
        except (TypeError, ValueError):
            return None
    return "\n".join(out)


def raw_observation(step):
    obs = step.get("observation")
    if not isinstance(obs, dict):
        return None
    results = obs.get("results")
    if not isinstance(results, list) or not results:
        return None
    for r in results:
        if not isinstance(r, dict) or r.get("content") is None:
            return None
    return "\n".join(str(r["content"]) for r in results)


def load_audit(root):
    audit = json.loads((root / "audit_20261011.json").read_text())
    by_basename = {}
    for t in audit["trials"]:
        # archive trial dirs carry a "NN_" rank prefix before the original name
        by_basename[Path(t["trial_dir"]).name] = t
    return audit, by_basename


def read_verifier_log(trial: Path):
    p = trial / "verifier" / "test-stdout.txt"
    return p.read_text(errors="replace") if p.is_file() else None


def trial_report(trial: Path, by_basename):
    """One sampled trial -> dict of everything V1/V2 need."""
    traj = json.loads((trial / "agent" / "trajectory.json").read_text())
    result = json.loads((trial / "result.json").read_text())
    steps, skipped = reconstruct(traj)
    raw_steps = traj.get("steps") or []
    n_user = sum(1 for s in raw_steps
                 if isinstance(s, dict) and s.get("source") == "user")
    n_agent = sum(1 for s in raw_steps
                  if isinstance(s, dict) and s.get("source") == "agent")
    campaign = by_basename.get(trial.name) or by_basename.get(
        trial.name.split("_", 1)[1])
    return {
        "trial": trial, "traj": traj, "result": result, "steps": steps,
        "skipped": skipped, "raw_steps": raw_steps, "n_user": n_user,
        "n_agent": n_agent, "campaign": campaign,
        "status": classify_result(result, read_verifier_log(trial)),
    }


def v1(trials):
    print("\n== V1 trajectory reconstruction ==")
    for rep in trials:
        trial = rep["trial"]
        name = "/".join(trial.parts[-3:])
        steps, skipped, raw = rep["steps"], rep["skipped"], rep["raw_steps"]
        print(f"[{name}] raw steps={len(raw)} (agent={rep['n_agent']}, user={rep['n_user']}) "
              f"kept={len(steps)} skipped={skipped}")
        check(all(s["action"] and s["observation"] for s in steps),
              f"{name}: empty action/observation in a kept step")
        # kept count must equal the number of independently renderable pairs
        expected = [(raw_action(s), raw_observation(s)) for s in raw
                    if isinstance(s, dict)]
        expected_kept = [(a, o) for a, o in expected if a is not None and o is not None]
        check(len(steps) == len(expected_kept),
              f"{name}: kept {len(steps)} != independently renderable {len(expected_kept)}")
        # byte-exact rendering for every kept step
        for i, ((a, o), step) in enumerate(zip(expected_kept, steps)):
            check(step["action"] == a, f"{name}: step {i} action mismatch")
            check(step["observation"] == o, f"{name}: step {i} observation mismatch")
        # skipped accounting: raw steps that look like interactions but were dropped
        lookalike = [s for s, (a, o) in zip(
            [r for r in raw if isinstance(r, dict)], expected)
            if (a is None) != (o is None) or (a is None and o is None and
             (s.get("tool_calls") or s.get("observation")))]
        check(skipped == len(lookalike),
              f"{name}: skipped={skipped} != interaction-like dropped {len(lookalike)}")
        # long observations preserved verbatim: the longest raw content must
        # appear as a full step observation
        longest = max((o for _, o in expected_kept), key=len, default="")
        check(longest in [s["observation"] for s in steps],
              f"{name}: longest observation ({len(longest)} ch) not kept verbatim")
        print(f"  longest observation kept verbatim: {len(longest)} chars; "
              f"total_usage={total_usage(rep['traj'])}")
        # status consistency with result.json reward
        status, reward, info = rep["status"]
        raw_reward = ((rep["result"].get("verifier_result") or {})
                      .get("rewards", {}).get("reward"))
        check(reward is None or reward == float(raw_reward),
              f"{name}: classify reward {reward} != result.json {raw_reward}")
        if raw_reward is not None and reward is not None and reward > 0:
            check(status == "success", f"{name}: reward>0 but status {status}")
        camp = rep["campaign"]
        print(f"  classify={status} reward={reward} reason={info.get('verdict_reason')} "
              f"exception={info.get('exception_type')} campaign={camp['status'] if camp else '?'}")
    # the very long trajectory in detail
    long_names = [r for r in trials if "schemelike" in str(r["trial"])]
    if long_names:
        rep = long_names[0]
        obs_lens = [len(s["observation"]) for s in rep["steps"]]
        act_lens = [len(s["action"]) for s in rep["steps"]]
        print(f"[very long] schemelike: kept {len(rep['steps'])} steps, "
              f"obs len max={max(obs_lens)} median={sorted(obs_lens)[len(obs_lens)//2]}, "
              f"action len max={max(act_lens)}")


def v2(trials, root):
    print("\n== V2 pseudo-zero detection (classify_result vs campaign audit) ==")
    # sampled archive trials: campaign verdict VALID -> success/failure; KEEP_MODEL -> failure
    for rep in trials:
        camp = rep["campaign"]
        if camp is None:
            continue
        name = "/".join(rep["trial"].parts[-3:])
        status = rep["status"][0]
        if camp["status"] == "VALID":
            expected = "success" if (camp["reward"] or 0) >= 1 else "failure"
            check(status == expected,
                  f"{name}: classify {status} != campaign {camp['status']}->{expected}")
        elif camp["status"] == "KEEP_MODEL":
            if camp["reason"].startswith("agent改坏沙箱网络"):
                # Documented divergence: the campaign's human review kept this
                # pseudo zero as a model failure (the agent broke the sandbox's
                # own network); the pre-registered rule cannot see that from
                # result.json + the verifier log, so it reruns it as infra.
                check(status == "infra_error" and
                      rep["status"][2].get("verdict_reason") == "pseudo_zero",
                      f"{name}: network-override trial should be pseudo_zero, "
                      f"got {status}")
                print(f"[DIVERGENT-DOCUMENTED] {name}: campaign KEEP_MODEL "
                      f"(人工复核) vs classify infra_error/pseudo_zero -- our "
                      "rule reruns it, the campaign scored it 0")
            else:
                check(status == "failure",
                      f"{name}: classify {status} != campaign KEEP_MODEL->failure")
        else:
            continue
    # sampled out-of-archive trials (fake zeros / infra / the override)
    sample = root / "sample"
    audit = json.loads((root / "audit_20261011.json").read_text())
    by_dir = {t["trial_dir"].rstrip("/"): t for t in audit["trials"]}
    for result_file in sorted(sample.rglob("result.json")):
        trial = result_file.parent
        remote_dir = str(trial.relative_to(sample))
        if remote_dir.startswith("data2/"):
            remote_dir = "/" + remote_dir
        camp = by_dir.get(remote_dir)
        if camp is None:
            print(f"  (no audit row for {remote_dir}; skipping)")
            continue
        result = json.loads(result_file.read_text())
        log_path = trial / "verifier" / "test-stdout.txt"
        log = log_path.read_text(errors="replace") if log_path.is_file() else None
        status, reward, info = classify_result(result, log)
        collected = bool(re.search(r"collected [0-9]+ item", log or ""))
        expected = {"RERUN_FAKE0": "infra_error", "RERUN_INFRA": "infra_error",
                    "KEEP_MODEL": "failure", "VALID": None, "RUNNING": None}[camp["status"]]
        verdict = "OK" if status == expected else "DIVERGENT"
        print(f"[{verdict}] {trial.name}: campaign={camp['status']} ({camp['reason']}) "
              f"classify={status} reason={info.get('verdict_reason')} "
              f"reward={reward} pytest_collected={collected}")
        if camp["status"] != "KEEP_MODEL":
            check(status == expected,
                  f"{trial.name}: classify {status} != campaign {camp['status']}")
        else:
            # expected divergence ONLY for the human-reviewed network override
            check(camp["reason"].startswith("agent改坏沙箱网络") or status == "failure",
                  f"{trial.name}: unexpected KEEP_MODEL divergence")


def v3(root):
    print("\n== V3 metrics cross-check (blocks/metrics vs official) ==")
    summary = json.loads((root / "summary.json").read_text())
    official = {"qwen": (0.4697, 59, 89), "dpsk": (0.6315, 76, 89)}
    for label in ("qwen", "dpsk"):
        rows = [line.split("\t") for line in
                (root / f"manifest_{label}.tsv").read_text().splitlines()]
        header, body = rows[0], rows[1:]
        check(header == ["rank", "task", "source", "origin_run", "trial_dir",
                         "reward", "original_path"], f"{label}: manifest header {header}")
        print(f"[{label}] manifest rows={len(body)}")
        trajs, per_task_counts = [], {}
        for rank, task, source, origin, tdir, reward, orig in body:
            r = None if reward == "None" else float(reward)
            correctness = r is not None and r >= 1.0
            trajs.append(Trajectory(
                trajectory_id=f"{label}:{task}:{rank}", task_id=task,
                benchmark="tb", model_role="strong", model_name=label,
                attempt_index=int(rank) - 1, correctness=correctness))
            per_task_counts[task] = per_task_counts.get(task, 0) + 1
        check(len(trajs) == 445, f"{label}: {len(trajs)} trials in manifest, expected 445")
        check(len(per_task_counts) == 89, f"{label}: {len(per_task_counts)} tasks")
        check(set(per_task_counts.values()) == {5},
              f"{label}: per-task counts not all 5: "
              f"{sorted(set(per_task_counts.values()))}")
        expected = {t: 5 for t in per_task_counts}
        metrics = compute_task_metrics(trajs, expected)
        agg = summarize(metrics, 5)
        passed = sum(1 for m in metrics.values() if m["success"] >= 1)
        print(f"[{label}] tasks={agg['tasks']} avg@5={agg['avg_at_n']:.6f} "
              f"(conservative {agg['avg_at_n_conservative']:.6f}) pass={passed}/89 "
              f"= {agg['pass_at_n']:.4f}  infra_tasks={agg['infra_tasks']}")
        check(abs(agg["avg_at_n"] - summary[f"{label}_avg@5"]) < 1e-12,
              f"{label}: our avg@5 {agg['avg_at_n']} != archive summary "
              f"{summary[f'{label}_avg@5']}")
        check(passed == round(summary[f"{label}_pass@5"] * 89),
              f"{label}: our pass count {passed} != summary "
              f"{round(summary[f'{label}_pass@5'] * 89)}")
        off_avg, off_pass, denom = official[label]
        check(abs(agg["avg_at_n"] - off_avg) < 5e-5,
              f"{label}: our avg@5 {agg['avg_at_n']:.6f} vs official {off_avg}")
        check(passed == off_pass, f"{label}: our pass {passed} vs official {off_pass}")
    # rewards binary? campaign maps >=1 -> 1; we map >0 -> success. Only safe
    # if no reward lies in (0, 1).
    audit = json.loads((root / "audit_20261011.json").read_text())
    rewards = sorted({t["reward"] for t in audit["trials"] if t["reward"] is not None})
    print(f"audit reward values: {rewards}")
    check(all(r >= 1 or r == 0 for r in rewards),
          f"non-binary reward values present: {rewards}")


def v4(trials):
    print("\n== V4 view rendering ==")
    rules = TBPlugin().view_rules
    print(f"view rules: {rules}")
    sizes = []
    for rep in trials:
        steps = rep["steps"]
        name = "/".join(rep["trial"].parts[-3:])
        if not steps:
            print(f"[{name}] no steps (agent died before first interaction)")
            continue
        view = render_steps_view(steps, rules)
        n_marked = view.count("characters truncated")
        raw_chars = sum(len(s["action"]) + len(s["observation"]) for s in steps)
        tokens = estimate_tokens(view)
        sizes.append(tokens)
        # invariant: every truncation marker states the true omitted count
        bad = []
        for s in steps:
            for field in ("action", "observation"):
                text = s[field]
                if len(text) <= rules.max_obs_chars:
                    continue
                want = (f"{text[:rules.head_keep]}\n[... "
                        f"{len(text) - rules.head_keep - rules.tail_keep} "
                        f"characters truncated ...]\n"
                        + (text[len(text) - rules.tail_keep:] if rules.tail_keep else ""))
                if want not in view:
                    bad.append(field)
        check(not bad, f"{name}: truncation marker mismatch for {bad}")
        print(f"[{name}] steps={len(steps)} raw_chars={raw_chars} rendered_chars={len(view)} "
              f"truncated_fields={n_marked} est_tokens={tokens}")
    if sizes:
        sizes.sort()
        print(f"typical rendered view tokens: min={sizes[0]} median={sizes[len(sizes)//2]} "
              f"max={sizes[-1]} (n={len(sizes)})")


def main():
    root = Path(sys.argv[1]).expanduser()
    audit, by_basename = load_audit(root)
    sample = root / "sample"
    trials = []
    for traj_path in sorted(sample.rglob("agent/trajectory.json")):
        trials.append(trial_report(traj_path.parent.parent, by_basename))
    print(f"sampled trials with trajectories: {len(trials)}")
    v1(trials)
    v2(trials, root)
    v3(root)
    v4(trials)
    print("\n== summary ==")
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
