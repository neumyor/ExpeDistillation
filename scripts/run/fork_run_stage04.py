"""Fork a run directory at stage 5: new config, copied stage-0..4 products.

Usage:
    set -a; . ./.env; set +a
    .venv/bin/python scripts/run/fork_run_stage04.py \
        --old-run runs/tb21_full_20261011_2242 \
        --new-run runs/tb21_full_v2_<time> \
        --config configs/experiments/tb21_full_v2.yaml

What it does (the 2026-10-12 budget decision, docs/decisions/
bank_token_budget_20261012.md):
1. Runs the REAL stage 0 via the CLI (--until-stage 0) to freeze the new
   config's manifest (the new bank_token_budget; every other field must
   match the old manifest -- verified below).
2. Copies the stage-0..4 products from the old run, keyed by the same
   deterministic IDs (zero model calls):
   - stage1_baseline/trajectories/          (534 files)
   - stage2_segments/subtasks/               (682) + segmentation_audit.jsonl
   - stage3_generation/skills/ + exams/      (stage-5 merged skills and
     their marked sources INCLUDED: the new stage 5 continues from the
     old run's post-merge member set -- reverted/skipped merges are
     discarded and inert)
   - stage4_acceptance/attempts/ + leak_verdicts.jsonl + rewrite_failures.jsonl
   - llm_requests/usage.jsonl               (accounting continuity)
   NOT copied: stage5_curation (fresh v3 log), skill_bank.jsonl (none),
   pipeline_log.jsonl (fresh ledger), run.lock, failed_units.jsonl,
   llm_requests/*.json dumps (old run dir keeps them as the archive).
3. Verifies: every kind's file count matches the old run; the new
   manifest differs from the old ONLY in config.bank_token_budget
   (asserted); the copied trajectories' model names match the new config.
4. Prints the verification and the follow-up command:
   `--until-stage 4` must complete with ZERO model calls (usage line
   count unchanged) before stage 5 is allowed to run.

Idempotent: files already present in the new run are skipped.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PRODUCTS = [
    "stage1_baseline/trajectories",
    "stage2_segments/subtasks",
    "stage2_segments/segmentation_audit.jsonl",
    "stage3_generation/skills",
    "stage3_generation/exams",
    "stage3_generation/generation_failures.jsonl",
    "stage4_acceptance/attempts",
    "stage4_acceptance/leak_verdicts.jsonl",
    "stage4_acceptance/rewrite_failures.jsonl",
    "llm_requests/usage.jsonl",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-run", required=True, type=Path)
    ap.add_argument("--new-run", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args()
    old, new, cfg = args.old_run, args.new_run, args.config
    assert old != new

    # 1. freeze the new manifest with the real CLI
    cmd = [".venv/bin/python", "-m", "expedistill.cli", "run",
           "--config", str(cfg), "--run-dir", str(new),
           "--until-stage", "0", "--workers", "1"]
    print("stage 0 freeze:", " ".join(cmd))
    if subprocess.call(cmd) != 0:
        print("error: stage 0 freeze failed", file=sys.stderr)
        return 1

    # the new manifest may differ from the old ONLY in bank_token_budget
    old_m = json.loads((old / "manifest.json").read_text())
    new_m = json.loads((new / "manifest.json").read_text())

    def diffs(a, b, prefix=""):
        out = []
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                out.extend(diffs(a.get(k), b.get(k), f"{prefix}.{k}"))
        elif a != b:
            out.append(prefix)
        return out

    d = diffs(old_m, new_m)
    allowed = {".config.bank_token_budget"}
    unexpected = [x for x in d if x not in allowed]
    if unexpected:
        print(f"error: manifests differ beyond bank_token_budget: "
              f"{unexpected}", file=sys.stderr)
        return 1
    if d:
        print("manifest differs only in:", d)

    # 2. copy products
    for rel in PRODUCTS:
        src, dst = old / rel, new / rel
        if not src.exists():
            print(f"skip (absent in old run): {rel}")
            continue
        # Stage 0's Store() pre-creates the empty directory skeleton, so
        # dst.exists() is NOT evidence of a prior copy (that trap lost the
        # first fork attempt). Always copy; re-copying identical files is
        # harmless, which is the idempotency guarantee here.
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # 3. verify counts
    print("\n=== verification ===")
    for rel in [r for r in PRODUCTS if (old / r).is_dir()]:
        n_old = sum(1 for _ in (old / rel).glob("**/*.json") if _.is_file())
        n_new = sum(1 for _ in (new / rel).glob("**/*.json") if _.is_file())
        mark = "OK" if n_old == n_new else "MISMATCH"
        print(f"{mark}: {rel}: old {n_old} / new {n_new}")
    usage = new / "llm_requests/usage.jsonl"
    n = sum(1 for _ in usage.open()) if usage.exists() else 0
    print(f"usage.jsonl copied: {n} lines")

    print(f"\nnext: `expedistill run --config {cfg} --run-dir {new} "
          f"--until-stage 4 --workers 1` must complete with ZERO new "
          f"usage lines (verify: line count stays {n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
