#!/usr/bin/env python3
"""Compute the TB21 full-campaign stage-1 baseline (F@3 numbers) offline.

Reads the tb21 campaign archive manifests (local copies under configs/seed/)
and writes docs/tb21_f3_baseline_20261011.md. No model calls, no remote
access, no run directory needed: the whole point is to pre-compute, from the
pre-registered seeding rule ("attempt 0..2 = archive rank 1..3 of each arm"),
exactly the numbers a seeded stage-1 run must reproduce, so the pipeline
output can be checked against an independent computation (outer AGENTS.md
section 1: register the metric before looking at the pipeline's numbers).

Integrity gate BEFORE any number is computed: every manifest row is joined
against the campaign audit (configs/seed/audit_20261011.json). reward present
must be a VALID trial with the same reward; reward "None" must be a KEEP_MODEL
trial (the campaign's "model's own doing" failures -- tmux keystroke errors,
AgentTimeoutError -- which count as failure, i.e. reward 0). Any mismatch
aborts: numbers over an unverified join would not be a baseline.

The numbers are computed TWICE, independently:
1. hand-rolled counting over the manifest rows (exact Fractions);
2. through the pipeline's own D3/D4 blocks (blocks.metrics) over Trajectory
   objects built exactly as the seeding script builds them
   (scripts/run/seed_tb_stage1.py).
Both must agree, or the script aborts (统计实现自查).

Usage (from the repo root):
    .venv/bin/python scripts/analysis/tb21_f3_baseline.py \
        [--manifest-qwen PATH] [--manifest-dpsk PATH] [--audit PATH] \
        [--doc PATH] [--check-only]

Inputs:  configs/seed/manifest_qwen.tsv, configs/seed/manifest_dpsk.tsv,
         configs/seed/audit_20261011.json (defaults).
Output:  docs/tb21_f3_baseline_20261011.md (default) and a stdout table.
Exit 0 on success, 1 on any integrity failure.
"""

import argparse
import csv
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.blocks import metrics as metrics_block  # noqa: E402
from expedistill.blocks.rules import get_task_select  # noqa: E402
from expedistill.core.ids import make_task_id  # noqa: E402
from expedistill.core.models import Trajectory  # noqa: E402

#: archive model dir -> manifest file; both arms of the campaign.
ARMS = (("weak", "qwen", "manifest_qwen.tsv"),
        ("strong", "dpsk", "manifest_dpsk.tsv"))

DEFAULT_AUDIT = "configs/seed/audit_20261011.json"
DEFAULT_DOC = "docs/tb21_f3_baseline_20261011.md"

SEED_ATTEMPTS = 3  # pre-registered: archive rank 1..3 -> attempt 0..2

_DOC_HEADER = """日期：{date}
一句话：TB21 全量 campaign 的 stage-1 基线（F@3 口径）——按预注册的"前三个 rank"播种规则，
从 campaign 归档 manifest 离线算出 F 集、双臂 avg@3 / pass@3，作为播种后 stage 1 的对照值。
对应 log.md 条目：2026-10-11（TB21 全量实验：stage-1 归档播种与 F@3 基线对照）。

计算脚本：`scripts/analysis/tb21_f3_baseline.py`（本文件由它生成，重跑可复现）。
输入：`configs/seed/manifest_qwen.tsv`、`configs/seed/manifest_dpsk.tsv`（89 题 × 5 rank，
来自基准主机上 `EXPEDISTILL_TB_ARCHIVE_ROOT` 的 的收官归档 manifest 本地副本）与
`configs/seed/audit_20261011.json`（campaign 审计）。
"""


def load_manifest(path: Path) -> list:
    with open(path, encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def load_audit(path: Path) -> dict:
    import json
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def verify_join(manifests: dict, audit: dict) -> None:
    """Every manifest row must match its audit trial; abort otherwise."""
    # audit model shorthand -> archive dir name (the manifest side's key)
    audit_model = {"q": "qwen", "d": "dpsk"}
    by_path = {(audit_model[t["model"]], t["trial_dir"]): t
               for t in audit["trials"]}
    problems = []
    for _, archive_dir, _ in ARMS:
        for row in manifests[archive_dir]:
            trial = by_path.get((archive_dir, row["original_path"]))
            if trial is None:
                problems.append(f"{archive_dir} {row['original_path']}: "
                                "not in the audit")
                continue
            if row["task"] != trial["task"]:
                problems.append(f"{row['original_path']}: task "
                                f"{row['task']} != audit {trial['task']}")
            if row["reward"] == "None":
                if trial["status"] != "KEEP_MODEL":
                    problems.append(
                        f"{archive_dir} {row['task']} rank {row['rank']}: "
                        f"reward None but audit status {trial['status']}")
            else:
                reward = float(row["reward"])
                if trial["status"] != "VALID" or trial["reward"] != reward:
                    problems.append(
                        f"{archive_dir} {row['task']} rank {row['rank']}: "
                        f"reward {row['reward']} but audit "
                        f"{trial['status']}/{trial['reward']}")
    if problems:
        raise SystemExit("manifest/audit join failed:\n  "
                         + "\n  ".join(problems))


def successes_first3(rows_by_task: dict) -> dict:
    """Per task: how many of ranks 1..3 succeeded (KEEP_MODEL counts as 0)."""
    per_task = {}
    for task, by_rank in rows_by_task.items():
        per_task[task] = sum(
            1 for rank in range(1, SEED_ATTEMPTS + 1)
            if by_rank[rank]["reward"] == "1.0")
    return per_task


def as_trajectories(rows_by_task: dict, role: str, model_name: str) -> list:
    """Trajectory objects exactly as the seeding script stores them."""
    trajectories = []
    for task in sorted(rows_by_task):
        task_id = make_task_id("tb", task)
        for attempt, rank in enumerate(range(1, SEED_ATTEMPTS + 1)):
            row = rows_by_task[task][rank]
            trajectories.append(Trajectory(
                trajectory_id=f"traj/{task_id}/{role}/{attempt:02d}",
                task_id=task_id,
                benchmark="tb",
                model_role=role,
                model_name=model_name,
                attempt_index=attempt,
                skills_injected=None,
                steps=[],
                correctness=row["reward"] == "1.0",
            ))
    return trajectories


def pipeline_stats(trajectories: list, n_attempts: int) -> dict:
    """The stage-1 arm summary, recomputed through the D3/D4 blocks."""
    expected = {traj.task_id: n_attempts for traj in trajectories}
    task_metrics = metrics_block.compute_task_metrics(trajectories,
                                                      expected=expected)
    summary = metrics_block.summarize(task_metrics, n_attempts)
    failed = metrics_block.failed_tasks(
        task_metrics, get_task_select("all_valid_failed"),
        n_attempts=n_attempts)
    return {"metrics": task_metrics, "summary": summary, "F": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest-qwen", type=Path,
                        default=REPO / "configs/seed/manifest_qwen.tsv")
    parser.add_argument("--manifest-dpsk", type=Path,
                        default=REPO / "configs/seed/manifest_dpsk.tsv")
    parser.add_argument("--audit", type=Path, default=REPO / DEFAULT_AUDIT)
    parser.add_argument("--doc", type=Path, default=REPO / DEFAULT_DOC)
    parser.add_argument("--check-only", action="store_true",
                        help="verify and print, do not write the doc")
    parser.add_argument("--force", action="store_true",
                        help="overwrite the doc even if it exists (the doc "
                             "also carries hand-appended sections such as "
                             "the run comparison; without this flag an "
                             "existing file is never clobbered)")
    args = parser.parse_args()

    manifests = {"qwen": load_manifest(args.manifest_qwen),
                 "dpsk": load_manifest(args.manifest_dpsk)}
    audit = load_audit(args.audit)

    # Integrity gate: no numbers before the join is proven.
    verify_join(manifests, audit)

    rows_by = {}
    for _, archive_dir, _ in ARMS:
        by_task = {}
        for row in manifests[archive_dir]:
            by_task.setdefault(row["task"], {})[int(row["rank"])] = row
        rows_by[archive_dir] = by_task
    tasks = sorted(rows_by["qwen"])
    if tasks != sorted(rows_by["dpsk"]):
        raise SystemExit("manifest task sets differ between arms")
    for archive_dir, by_task in rows_by.items():
        for task in tasks:
            if sorted(by_task[task]) != list(range(1, 6)):
                raise SystemExit(f"{archive_dir}/{task}: ranks 1..5 incomplete")

    # (1) hand-rolled, exact fractions.
    per_task = {"qwen": successes_first3(rows_by["qwen"]),
                "dpsk": successes_first3(rows_by["dpsk"])}
    counts = {arm: Counter(per.values()) for arm, per in per_task.items()}

    def avg3(arm: str) -> Fraction:
        return sum((Fraction(per_task[arm][t], SEED_ATTEMPTS)
                    for t in tasks), Fraction(0)) / len(tasks)

    def pass3(arm: str) -> Fraction:
        return Fraction(sum(1 for t in tasks if per_task[arm][t] >= 1),
                        len(tasks))

    F = sorted(t for t in tasks if per_task["qwen"][t] == 0)
    F_strong_success = [t for t in F if per_task["dpsk"][t] >= 1]

    weak_avg, weak_pass = avg3("qwen"), pass3("qwen")
    strong_avg, strong_pass = avg3("dpsk"), pass3("dpsk")

    # (2) through the pipeline's own blocks, over the same manifest rows.
    pip = {
        "weak": pipeline_stats(as_trajectories(rows_by["qwen"], "weak",
                                              "qwen3.6-flash-distill"),
                               SEED_ATTEMPTS),
        "strong": pipeline_stats(as_trajectories(rows_by["dpsk"], "strong",
                                                "DEEPSEEK_up5zdj"),
                                 SEED_ATTEMPTS),
    }
    pip_weak, pip_strong = pip["weak"]["summary"], pip["strong"]["summary"]
    pip_F = pip["weak"]["F"]

    def close(fraction: Fraction, value) -> bool:
        return abs(float(fraction) - value) <= 1e-12

    checks = [
        ("weak avg@3", close(weak_avg, pip_weak["avg_at_n"])),
        ("weak avg@3 conservative",
         close(weak_avg, pip_weak["avg_at_n_conservative"])),
        ("weak pass@3", close(weak_pass, pip_weak["pass_at_n"])),
        ("strong avg@3", close(strong_avg, pip_strong["avg_at_n"])),
        ("strong avg@3 conservative",
         close(strong_avg, pip_strong["avg_at_n_conservative"])),
        ("strong pass@3", close(strong_pass, pip_strong["pass_at_n"])),
        ("F set", pip_F == [make_task_id("tb", t) for t in F]),
        ("no infra", not pip_weak["infra_tasks"]
         and not pip_strong["infra_tasks"]
         and pip_weak["aux"] is None and pip_strong["aux"] is None),
        ("tasks", pip_weak["tasks"] == len(tasks)
         and pip_strong["tasks"] == len(tasks)),
    ]
    failed = [name for name, ok in checks if not ok]
    if failed:
        print("CROSS-CHECK FAILED: " + ", ".join(failed), file=sys.stderr)
        for arm in ("weak", "strong"):
            print(arm, pip[arm]["summary"], file=sys.stderr)
        return 1

    # ---- report -----------------------------------------------------------
    table = [
        "| 口径 | 值 |",
        "|---|---|",
        f"| 任务数 | {len(tasks)} |",
        f"| F（弱臂 rank1-3 全失败的任务数） | {len(F)} |",
        f"| 弱臂 avg@3 | {weak_avg} = {float(weak_avg):.6f} |",
        f"| 弱臂 pass@3 | {weak_pass} = {float(weak_pass):.6f} "
        f"({weak_pass.numerator}/{weak_pass.denominator}) |",
        f"| 强臂 avg@3 | {strong_avg} = {float(strong_avg):.6f} |",
        f"| 强臂 pass@3 | {strong_pass} = {float(strong_pass):.6f} "
        f"({strong_pass.numerator}/{strong_pass.denominator}) |",
        f"| F 中强臂 rank1-3 至少一次成功的任务数 | {len(F_strong_success)} |",
    ]
    print("\n".join(table))
    print(f"\nper-task weak success counts (rank1-3): "
          f"{dict(sorted(counts['qwen'].items()))}")
    print(f"per-task strong success counts (rank1-3): "
          f"{dict(sorted(counts['dpsk'].items()))}")
    print(f"\nF tasks ({len(F)}):")
    for task in F:
        mark = "强臂rank1-3有成功" if task in F_strong_success else "强臂rank1-3全失败"
        print(f"  {task}  [{mark}]")

    if args.check_only:
        return 0
    if args.doc.exists() and not args.force:
        print(f"doc {args.doc} already exists; pass --force to regenerate "
              "the baseline section (hand-appended sections would be lost)")
        return 0

    doc = [
        _DOC_HEADER.format(date="2026-10-11"),
        "",
        "## F@3 基线表（预注册口径，写于任何播种 run 之前）",
        "",
        *table,
        "",
        "F 任务清单（*号 = 强臂 rank1-3 至少一次成功）：",
        "",
    ]
    starred = set(F_strong_success)
    for task in F:
        doc.append(f"- {'*' if task in starred else ' '} {task}")
    doc += [
        "",
        "## 方法说明",
        "",
        "口径在算任何 run 的数字**之前**预注册：播种规则取每个臂归档 rank 1、2、3 的 trial",
        f"作为 attempt 0、1、2（共 {len(tasks)} 题 × 2 臂 × 3 次采样 = {len(tasks) * 6} 个单元）。",
        "campaign 的 reward 是二值的：1.0 记 success，0.0 记 failure；reward 缺失的行是审计里",
        "KEEP_MODEL 的 trial（模型自身原因失败：tmux 按键失败、AgentTimeoutError 等），一律记",
        "failure（计 0）。每个单元都是有效采样（VALID 或 KEEP_MODEL），没有 infra_error，",
        "所以条件 avg@3 与保守 avg@3 在此数据上恒等，pass@3 分母为全部 "
        f"{len(tasks)} 题。",
        "avg@3 = 各题三次采样成功率先按题平均再跨题平均；pass@3 = 三次中至少一次成功的题占比。",
        "F 的定义是 D4 具名规则 `all_valid_failed`：该题弱臂三次采样全部有效且全部失败。",
        "脚本先用精确分数独立手算一遍，再用仓库自己的 D3/D4 积木"
        "（`blocks.metrics.compute_task_metrics` / `summarize` / `failed_tasks`）",
        "在同一批 manifest 行上复算一遍，两套结果逐项一致后才落盘本文件。",
        "manifest 与审计的连接经过完整性校验：445+445 行全部与审计逐行对上",
        "（reward 在则必为 VALID 且数值相同；reward 缺失则必为 KEEP_MODEL），",
        "任何一行对不上脚本即中止。该表是 `runs/tb21_full_*` 播种 run 的 stage-1 对照值：",
        "播种后的 stage 1 summary 中 strong/weak 两臂的 avg@3、pass@3 与 F 必须与本表逐项一致。",
    ]
    args.doc.parent.mkdir(parents=True, exist_ok=True)
    args.doc.write_text("\n".join(doc) + "\n", encoding="utf-8")
    print(f"\nwrote {args.doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
