#!/usr/bin/env python3
"""Seed a TB21 run directory's baseline (D-phase) units from the campaign
archive.

The TB21 full campaign does not need live rollouts for its baseline: the
tb21 bare-rollout campaign already produced, per task and arm, five ranked
valid trials on node29 (/data2/niuyiming/e1_final_archive/{qwen,dpsk}/<task>/).
The pre-registered seeding rule maps archive rank 1..3 to attempt 0..2 of
each arm (strong = dpsk / deepseek-v4-flash-0731--ali, weak = qwen / qwen3.6-flash-
distill), so a run configured with n_attempts 3/3 gets its whole baseline
unit set (89 tasks x 2 arms x 3 attempts = 534 units) from the archive and
K1 skips every unit -- zero model calls, zero remote rollouts. Under the
phase world the baseline is the sequence's first execute phase (D), whose
bare arms keep the run-level store and the deterministic ids, so the
seeding rule and the store layout are unchanged.

Steps (each idempotent, so an interrupted seeding is re-run as-is):

1. The freeze step through the REAL CLI (``expedistill run --until-phase
   -1``): loads the task set, freezes the manifest, dispatches no phase.
   On a re-run the freeze is the resume check (a changed config is refused;
   that is the point).
2. Download the needed ``agent/trajectory.json`` files (rank 1..3 of both
   arms) into the cache (default ``tmp/tb_seed_cache/``, mirroring the
   archive layout) with ONE tar-over-ssh stream -- only files not already
   cached. Trials whose trajectory the archive does not hold (the
   campaign's KEEP_MODEL tmux-setup deaths: empty agent dir, no interaction
   ever happened) are probed once and recorded in ``absent_units.json`` in
   the cache; they seed with zero steps, like the live plugin's own
   missing-trajectory path (the verdict stands on result.json).
3. Rebuild steps with ``benchmarks.tb.trajectory.reconstruct`` /
   ``reconstruct_skip_counts`` and store each Trajectory under the
   pipeline's ID rule (``traj/tb:<task>/<role>/<attempt:02d>``) with
   model_name / skills_injected exactly what K1's stored-unit check
   compares. Units already in the store are verified, not rebuilt.
4. Print a summary table: per-arm seeded/skipped counts, per-task status
   mixes, the baseline aggregates implied by the stored statuses (avg@3 /
   pass@3 / F -- compare with docs/tb21_f3_baseline_20261011.md), total
   steps, and this run's download volume.

Status mapping (campaign rewards are binary, fixed from the audit): reward
1.0 -> success; 0.0 -> failure; a missing reward is the audit's KEEP_MODEL
(failed by the model's own doing: tmux keystroke errors, AgentTimeout) ->
failure. Any other missing-reward case aborts the seeding.

The remote is READ-ONLY: this script only ever reads from node29 (a tar
stream and two ls/python probes); it never writes or uploads anything there.

Usage (from the repo root; .env is loaded automatically if present):
    .venv/bin/python scripts/run/seed_tb_stage1.py \
        --run-dir runs/tb21_full_20261011_2200 \
        [--config configs/experiments/tb21_full.yaml] \
        [--tasks-file FILE]      # one task name per line, # comments; default all
        [--cache-dir tmp/tb_seed_cache]

Inputs : configs/experiments/<cfg>.yaml, configs/seed/manifest_{qwen,dpsk}.tsv,
         configs/seed/audit_20261011.json, node29 e1_final_archive (read-only).
Output : the frozen run dir with all baseline trajectories stored (+ stdout
         summary). Exit 0 on success; a non-zero exit leaves a resumable
         partial seeding.
"""

import argparse
import base64
import csv
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.benchmarks.tb.plugin import _trial_timing  # noqa: E402
from expedistill.benchmarks.tb.remote import RemoteLink  # noqa: E402
from expedistill.benchmarks.tb.trajectory import (  # noqa: E402
    reconstruct,
    reconstruct_skip_counts,
)
from expedistill.core.ids import make_task_id, make_trajectory_id  # noqa: E402
from expedistill.core.models import Step, Trajectory  # noqa: E402
from expedistill.infra.store import Store  # noqa: E402

#: The campaign archive on node29 (read-only for this script).
ARCHIVE_ROOT = "/data2/niuyiming/e1_final_archive"

#: Pipeline role -> (archive arm dir, campaign model name, manifest file).
#: The pre-registered rule of this seeder: strong arm = the dpsk archive,
#: weak arm = the qwen archive. The frozen config's model table is checked
#: against the campaign model names below; a different experiment needs a
#: new rule, not a flag.
ARMS = {
    "strong": ("dpsk", "deepseek-v4-flash-0731--ali", "manifest_dpsk.tsv"),
    "weak": ("qwen", "qwen3.6-flash-distill", "manifest_qwen.tsv"),
}

#: Archive ranks mapped to attempts: rank 1..3 -> attempt 0..2.
SEED_ATTEMPTS = 3

DEFAULT_CONFIG = "configs/experiments/tb21_full.yaml"
DEFAULT_CACHE = "tmp/tb_seed_cache"
DEFAULT_MANIFEST_DIR = "configs/seed"
AUDIT_NAME = "audit_20261011.json"

_SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

_TAR_CHUNK = 64 * 1024


def load_env(env_file: Path) -> None:
    """Fill os.environ from the repo's .env (existing values win).

    The run/ scripts own environment loading (AGENTS.md section 5): the
    method code reads env vars only, it never loads .env itself.
    """
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def load_manifest(path: Path) -> dict:
    """{task: {rank(int): row}} from one manifest TSV."""
    with open(path, encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    by_task: dict = {}
    for row in rows:
        by_task.setdefault(row["task"], {})[int(row["rank"])] = row
    return by_task


def load_keep_model(audit_path: Path) -> set:
    """original_path set of the audit's KEEP_MODEL trials (any model)."""
    with open(audit_path, encoding="utf-8") as stream:
        audit = json.load(stream)
    return {trial["trial_dir"] for trial in audit["trials"]
            if trial["status"] == "KEEP_MODEL"}


def read_tasks_file(path: Path) -> list:
    """Task names, one per line, '#' comments and blanks allowed."""
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    if not names:
        raise SystemExit(f"tasks file {path} names no task")
    return names


def run_stage0(cli: Path, config: Path, run_dir: Path) -> None:
    """The freeze step via the real CLI; its freeze is the resume check.

    ``--until-phase -1`` stops right after the freeze (W3-B's freeze-only
    entry): the manifest and task table land on disk and no phase
    dispatches, which is what leaves room for the seeding below before
    the baseline phase runs.
    """
    argv = [str(cli), "run", "--config", str(config),
            "--run-dir", str(run_dir), "--until-phase", "-1"]
    print("$ " + " ".join(argv))
    completed = subprocess.run(argv)
    if completed.returncode != 0:
        raise SystemExit(f"stage 0 exited {completed.returncode}; "
                         "fix the config/run dir and re-run this script")


def ssh_argv(host: str, key: str, command: str) -> list:
    return ["ssh", *_SSH_OPTIONS, "-i", key, host, command]


def download_missing(link: RemoteLink, wanted: dict) -> tuple:
    """Stream the not-yet-cached archive files into the cache with one tar.

    ``wanted`` maps archive-relative path -> local cache path. Files already
    on disk are skipped by the caller; this function tar-queries only the
    missing ones and atomically writes each extracted member (temp file +
    os.replace), so an interrupted stream can never leave a truncated file
    that looks cached. Returns (n_downloaded, n_bytes, still_missing).
    """
    missing = {rel: local for rel, local in wanted.items()
               if not local.exists()}
    if not missing:
        return 0, 0, []
    command = f"cd {ARCHIVE_ROOT} && tar -cf - --ignore-failed-read -T -"
    argv = ssh_argv(link.host, link.key_path, command)
    print(f"downloading {len(missing)} file(s) from "
          f"{link.host}:{ARCHIVE_ROOT} (single tar stream)")
    started = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None and proc.stdout is not None

    def feed() -> None:
        try:
            proc.stdin.write(("\n".join(sorted(missing)) + "\n").encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass  # remote tar died early; surfaced via its exit below

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()

    n_files = 0
    n_bytes = 0
    arrived: set = set()
    stream = tarfile.open(fileobj=proc.stdout, mode="r|")
    for member in stream:
        if not member.isfile():
            continue
        rel = member.name
        if rel.startswith("./"):
            rel = rel[2:]
        local = missing.get(rel)
        if local is None:
            continue  # not one of ours (never happens; defensive)
        source = stream.extractfile(member)
        if source is None:
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        temp = local.with_name(local.name + ".tmp")
        with open(temp, "wb") as target:
            while True:
                chunk = source.read(_TAR_CHUNK)
                if not chunk:
                    break
                target.write(chunk)
                n_bytes += len(chunk)
        os.replace(temp, local)
        arrived.add(rel)
        n_files += 1
    stream.close()
    proc.stdout.close()
    stderr = (proc.stderr.read().decode("utf-8", "replace")
              if proc.stderr else "")
    rc = proc.wait()
    feeder.join()
    elapsed = time.monotonic() - started
    print(f"tar stream: {n_files} file(s), {n_bytes / 1e6:.1f} MB in "
          f"{elapsed:.1f}s (rc={rc})")
    if rc != 0:
        # --ignore-failed-read covers genuinely absent files; anything else
        # on stderr is worth showing (the caller resolves the rest).
        condensed = " ".join(stderr.split())[-500:]
        print(f"note: tar stderr: {condensed}" if condensed
              else "note: tar exited non-zero with no stderr")
    return n_files, n_bytes, sorted(set(missing) - arrived)


def probe_absent(link: RemoteLink, relatives: list) -> dict:
    """Confirm which remote paths truly do not exist (one ssh round trip).

    Returns {relative_path: True} for the absent ones. A file that exists
    but was missed by the stream (transport hiccup) is NOT marked absent --
    the next run downloads it again.
    """
    script = ("while IFS= read -r p; do "
              "if [ -f \"$p\" ]; then printf 'OK %s\\n' \"$p\"; "
              "else printf 'ABSENT %s\\n' \"$p\"; fi; done")
    argv = ssh_argv(link.host, link.key_path,
                    f"cd {ARCHIVE_ROOT} && {script}")
    completed = subprocess.run(argv, input="\n".join(relatives) + "\n",
                              capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(f"absence probe failed (rc="
                         f"{completed.returncode}): {completed.stderr[-300:]}")
    absent: dict = {}
    for line in completed.stdout.splitlines():
        state, _, rel = line.partition(" ")
        if state == "ABSENT":
            absent[rel] = True
        elif state != "OK":
            raise SystemExit(f"absence probe returned {line!r}")
    return absent


def fetch_timings(link: RemoteLink, trials: list) -> dict:
    """started_at/finished_at per trial from result.json (one ssh round trip).

    The remote code is delivered base64-decoded through an env var (the
    trial list goes over stdin). Mirrors the live plugin's meta discipline:
    timing is the trial's own from result.json, never the fetch time
    (benchmarks/tb/plugin.py _trial_timing).
    """
    code = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    trial = line.rstrip('\\n')\n"
        "    try:\n"
        "        result = json.load(open(trial + '/result.json'))\n"
        "    except (OSError, ValueError):\n"
        "        print(trial + '\\t\\t')\n"
        "        continue\n"
        "    print(trial + '\\t' + str(result.get('started_at') or '')\n"
        "          + '\\t' + str(result.get('finished_at') or ''))\n"
    )
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    command = (f"cd {ARCHIVE_ROOT} && SEEDCODE=$(printf %s {encoded} "
               "| base64 -d) && python3 -c \"$SEEDCODE\"")
    argv = ssh_argv(link.host, link.key_path, command)
    completed = subprocess.run(argv, input="\n".join(sorted(trials)) + "\n",
                              capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(f"timing fetch failed (rc="
                         f"{completed.returncode}): {completed.stderr[-300:]}")
    timings: dict = {}
    for line in completed.stdout.splitlines():
        trial, _, rest = line.partition("\t")
        started, _, finished = rest.partition("\t")
        timings[trial] = (started or None, finished or None)
    if len(timings) != len(set(trials)):
        raise SystemExit(f"timing fetch returned {len(timings)} of "
                         f"{len(set(trials))} trials")
    return timings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True, type=Path,
                        help="run directory (frozen by stage 0 here)")
    parser.add_argument("--config", type=Path,
                        default=REPO / DEFAULT_CONFIG,
                        help=f"experiment config (default {DEFAULT_CONFIG})")
    parser.add_argument("--tasks-file", type=Path, default=None,
                        help="seed only these task names (one per line, "
                             "'#' comments); default: all tasks of the "
                             "frozen config")
    parser.add_argument("--cache-dir", type=Path, default=REPO / DEFAULT_CACHE,
                        help=f"download cache (default {DEFAULT_CACHE})")
    parser.add_argument("--manifest-dir", type=Path,
                        default=REPO / DEFAULT_MANIFEST_DIR,
                        help="local manifest TSVs + audit JSON "
                             f"(default {DEFAULT_MANIFEST_DIR})")
    args = parser.parse_args()

    load_env(REPO / ".env")
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # --- (a) stage 0 via the real CLI: freeze the manifest -----------------
    cli = REPO / ".venv" / "bin" / "expedistill"
    if not cli.exists():
        raise SystemExit(f"CLI not found at {cli}")
    run_stage0(cli, args.config.resolve(), run_dir)

    with open(run_dir / "manifest.json", encoding="utf-8") as stream:
        manifest = json.load(stream)
    frozen = manifest["config"]
    for role, (_, model_name, _) in ARMS.items():
        if frozen["models"][f"executor_{role}"] != model_name:
            raise SystemExit(
                f"frozen config maps executor_{role} to "
                f"{frozen['models'][f'executor_{role}']!r}, but this seeder "
                f"is pre-registered for the tb21 campaign "
                f"({model_name!r}); use a different --config")
    train = list(manifest["split"]["train"])
    attempts = {"strong": int(frozen["n_attempts_strong"]),
                "weak": int(frozen["n_attempts_weak"])}
    for role, n in attempts.items():
        if n > SEED_ATTEMPTS:
            raise SystemExit(
                f"n_attempts_{role}={n} exceeds the pre-registered seeding "
                f"rule (archive rank 1..{SEED_ATTEMPTS} -> attempt 0.."
                f"{SEED_ATTEMPTS - 1}); reduce it or extend the rule")

    seed_tasks = train
    if args.tasks_file is not None:
        wanted = read_tasks_file(args.tasks_file.resolve())
        unknown = sorted(set(wanted) - set(train))
        if unknown:
            raise SystemExit(f"tasks file names tasks outside the frozen "
                             f"run: {unknown}")
        seed_tasks = [task for task in train if task in set(wanted)]
    unseeded = [task for task in train if task not in set(seed_tasks)]
    if unseeded:
        print(f"warning: {len(unseeded)} frozen task(s) NOT seeded "
              f"(--tasks-file): {unseeded}")
        print("warning: the baseline phase would EXECUTE those remotely; "
              "seed them before running the pipeline")

    # --- campaign manifests: rank -> row; KEEP_MODEL reward check ----------
    manifests = {role: load_manifest(args.manifest_dir / arm[2])
                 for role, arm in ARMS.items()}
    keep_model = load_keep_model(args.manifest_dir / AUDIT_NAME)
    for role in ARMS:
        missing_rows = sorted(set(seed_tasks) - set(manifests[role]))
        if missing_rows:
            raise SystemExit(f"{role} manifest covers no rows for: "
                             f"{missing_rows}")
    for role, task, rank in ((role, task, rank)
                             for role in ARMS
                             for task in sorted(seed_tasks)
                             for rank in range(1, attempts[role] + 1)):
        row = manifests[role][task][rank]
        if row["reward"] == "None" and row["original_path"] not in keep_model:
            raise SystemExit(
                f"{role}/{task} rank {rank}: manifest reward is missing "
                "but the audit does not mark the trial KEEP_MODEL; "
                "refusing to guess its status")

    link = RemoteLink()  # host/key from .env; the archive is read-only

    # --- expected units ------------------------------------------------------
    store = Store(run_dir)
    units = []  # (role, task, attempt, rank, manifest row, trial, rel_traj)
    for role, (arm_dir, _, _) in ARMS.items():
        for task in sorted(seed_tasks):
            for attempt in range(attempts[role]):
                rank = attempt + 1
                row = manifests[role][task][rank]
                trial = f"{arm_dir}/{task}/{rank:02d}_{row['trial_dir']}"
                rel_traj = f"{trial}/agent/trajectory.json"
                units.append((role, task, attempt, rank, row, trial, rel_traj))

    absent_cache = cache_dir / "absent_units.json"
    absent_recorded: dict = {}
    if absent_cache.exists():
        absent_recorded = json.loads(absent_cache.read_text(encoding="utf-8"))

    already_stored = 0
    wanted: dict = {}
    for role, task, attempt, rank, row, trial, rel_traj in units:
        if store.exists("trajectories",
                        make_trajectory_id(make_task_id("tb", task),
                                           role, attempt)):
            already_stored += 1
            continue
        if rel_traj in absent_recorded:
            continue
        wanted[rel_traj] = cache_dir / rel_traj
    print(f"units: {len(units)} expected, {already_stored} already stored, "
          f"{len(wanted)} to download, "
          f"{sum(1 for u in units if u[6] in absent_recorded)} archive-absent")

    # --- (b) download the missing trajectories into the cache ---------------
    n_files = n_bytes = 0
    if wanted:
        n_files, n_bytes, still_missing = download_missing(link, wanted)
        if still_missing:
            confirmed = probe_absent(link, still_missing)
            if confirmed:
                absent_recorded.update(confirmed)
                absent_cache.write_text(
                    json.dumps(absent_recorded, indent=2, sort_keys=True),
                    encoding="utf-8")
                for rel in confirmed:
                    print(f"archive has no {rel} (recorded; seeds 0 steps)")
            unresolved = sorted(set(still_missing) - set(confirmed))
            if unresolved:
                raise SystemExit(
                    f"transport problem: {unresolved} neither downloaded "
                    "nor confirmed absent; re-run this script")

    # --- timings from result.json for the units we still must build ---------
    trials_needed = sorted({trial
                            for role, task, attempt, rank, row, trial, rel
                            in units
                            if not store.exists(
                                "trajectories",
                                make_trajectory_id(make_task_id("tb", task),
                                                   role, attempt))})
    timings: dict = {}
    if trials_needed:
        print(f"fetching trial timings from result.json "
              f"({len(trials_needed)} trial(s))")
        timings = fetch_timings(link, trials_needed)

    # --- (c) reconstruct + store every missing unit -------------------------
    seeded: dict = {role: 0 for role in ARMS}
    seeded_absent = 0
    total_steps = 0
    dropped = {"skipped_parse_failures": 0, "skipped_handoffs": 0,
               "skipped_other": 0}
    for role, task, attempt, rank, row, trial, rel_traj in units:
        trajectory_id = make_trajectory_id(make_task_id("tb", task),
                                           role, attempt)
        if store.exists("trajectories", trajectory_id):
            stored = store.load("trajectories", trajectory_id, Trajectory)
            # Same guard K1 applies: a stored unit is "done" only for the
            # inputs it was produced under.
            problems = []
            if stored.model_role != role:
                problems.append(f"model_role {stored.model_role!r}")
            if stored.model_name != ARMS[role][1]:
                problems.append(f"model_name {stored.model_name!r}")
            if stored.skills_injected is not None:
                problems.append("skills_injected is not None")
            if problems:
                raise SystemExit(
                    f"{trajectory_id}: stored under different inputs "
                    f"({', '.join(problems)}); use a new run directory")
            continue
        meta = {
            "tb_task": task,
            "reward": (None if row["reward"] == "None"
                       else float(row["reward"])),
            "campaign_rank": rank,
            "campaign_trial_dir": row["trial_dir"],
            "campaign_source_kind": row["source"],
            "campaign_origin_run": row["origin_run"],
            "campaign_trial": f"{ARCHIVE_ROOT}/{trial}",
            "campaign_source": "e1_final_archive (tb21 campaign, 2026-10-11)",
            "note": "seeded from the campaign archive; steps rebuilt via "
                    "benchmarks.tb.trajectory.reconstruct",
        }
        steps: list = []
        if rel_traj in absent_recorded:
            seeded_absent += 1
            meta["trajectory_error"] = (
                "agent/trajectory.json absent in the archive (empty agent "
                "dir: the trial died at tmux setup before any interaction)")
        else:
            local = cache_dir / rel_traj
            if not local.exists():
                raise SystemExit(
                    f"{trial}: trajectory neither cached nor recorded "
                    "absent; re-run this script")
            with open(local, encoding="utf-8") as stream:
                traj_json = json.load(stream)
            steps = reconstruct(traj_json)[0]
            counts = reconstruct_skip_counts(traj_json)
            meta.update({key: counts[key] for key in dropped})
            for key in dropped:
                dropped[key] += counts[key]
        started, finished = timings.get(trial, (None, None))
        meta["timing"] = _trial_timing(
            {"started_at": started, "finished_at": finished})
        trajectory = Trajectory(
            trajectory_id=trajectory_id,
            task_id=make_task_id("tb", task),
            benchmark="tb",
            model_role=role,
            model_name=ARMS[role][1],
            attempt_index=attempt,
            skills_injected=None,
            steps=[Step(**step) for step in steps],
            correctness=row["reward"] == "1.0",
            meta=meta,
        )
        store.save(trajectory, "trajectories")
        seeded[role] += 1
        total_steps += len(steps)

    # --- (d) summary over what the STORE now holds ---------------------------
    print()
    print(f"run dir: {run_dir}")
    print(f"stored now: strong={seeded['strong']} weak={seeded['weak']} "
          f"(absent-trajectory units: {seeded_absent}); "
          f"already stored before: {already_stored}")
    print(f"total interaction steps stored: {total_steps} "
          f"(dropped by reconstruct this run: {dropped})")
    print(f"downloaded this run: {n_files} file(s), {n_bytes / 1e6:.1f} MB")

    statuses: dict = {role: {} for role in ARMS}
    stored_steps = 0
    complete = True
    for role, task, attempt, rank, row, trial, rel_traj in units:
        trajectory_id = make_trajectory_id(make_task_id("tb", task),
                                           role, attempt)
        if not store.exists("trajectories", trajectory_id):
            complete = False
            continue
        stored = store.load("trajectories", trajectory_id, Trajectory)
        stored_steps += len(stored.steps)
        marks = statuses[role].setdefault(task, ["-"] * attempts[role])
        marks[attempt] = "S" if stored.correctness is True else "F"
    if complete:
        print(f"all {len(units)} expected units are in the store "
              f"({stored_steps} interaction steps)")
    else:
        print("warning: store still misses units; aggregate below is "
              "partial (re-run this script to finish)")
        return 1

    def aggregate(role: str) -> dict:
        n = len(seed_tasks)
        per_task = {task: statuses[role][task].count("S")
                    for task in sorted(seed_tasks)}
        avg = sum(per_task.values()) / (attempts[role] * n)
        passed = sum(1 for count in per_task.values() if count >= 1)
        failed = sorted(task for task, count in per_task.items() if count == 0)
        return {"avg": avg, "passed": passed, "failed": failed, "n": n}

    weak = aggregate("weak")
    strong = aggregate("strong")
    print(f"implied baseline aggregates over {weak['n']} task(s):")
    print(f"  weak   avg@{attempts['weak']}={weak['avg']:.6f} "
          f"pass@{attempts['weak']}={weak['passed']}/{weak['n']} "
          f"|F|={len(weak['failed'])}")
    print(f"  strong avg@{attempts['strong']}={strong['avg']:.6f} "
          f"pass@{attempts['strong']}={strong['passed']}/{strong['n']} "
          f"(F tasks with a strong success: "
          f"{len([t for t in weak['failed'] if 'S' in statuses['strong'].get(t, [])])})")
    print("compare with docs/tb21_f3_baseline_20261011.md before the "
          "baseline phase")
    print("per-task status mixes (attempt order, S=success F=failure):")
    for task in sorted(seed_tasks):
        print(f"  {task:<42} "
              f"weak={''.join(statuses['weak'][task])} "
              f"strong={''.join(statuses['strong'][task])}")
    print("next: expedistill run --config "
          f"{args.config} --run-dir {run_dir} --until-phase 0 --workers 8 "
          "(freeze + the baseline phase, zero executions: every unit is "
          "already stored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
