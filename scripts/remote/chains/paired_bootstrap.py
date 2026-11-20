#!/usr/bin/env python3
"""Paired task-level bootstrap of avg@3 differences between ALFWorld test runs.

usage: paired.py RUNS_DIR   (reads every alfworld_paper_*_j40 run's last test phase)
Per task the score is the mean over its 3 attempts; the difference of two runs
is bootstrapped over the 134 tasks (10000 resamples, seed 0).
"""
import glob
import json
import os
import random
import sys

runs_dir = sys.argv[1]


def load(name):
    ps = sorted(glob.glob(f"{runs_dir}/alfworld_paper_{name}_j40/phases/p0*_test"))
    base = ps[-1] + "/with_bank/store/stage1_baseline/trajectories/*.json"
    per = {}
    for f in glob.glob(base):
        d = json.load(open(f))
        per.setdefault(d["task_id"], []).append(1.0 if d["correctness"] is True else 0.0)
    return {t: sum(v) / len(v) for t, v in per.items()}, {t: len(v) for t, v in per.items()}


def diff(a, b, rng):
    tasks = sorted(set(a) & set(b))
    d = [a[t] - b[t] for t in tasks]
    mean = sum(d) / len(d)
    boots = []
    for _ in range(10000):
        s = [d[rng.randrange(len(d))] for _ in d]
        boots.append(sum(s) / len(s))
    boots.sort()
    return mean, boots[250], boots[9749], len(tasks)


names = ["bare_q", "ref", "e1_no_skills", "e2_written_once", "e3_body_only", "e4_trigger_only",
         "e5_prefix_authoring", "e6_no_profile", "e7_k1", "e8_ours_nopb", "e8_written_once_nopb",
         "e8_body_only_nopb", "e8_trigger_only_nopb", "e8_prefix_authoring_nopb",
         "e8_no_profile_nopb", "e8_k1_nopb"]
data = {n: load(n) for n in names}
bad = {n: set(c.values()) for n, (_, c) in data.items() if set(c.values()) != {3}}
print("attempt counts not all 3:", bad or "none")
rng = random.Random(0)
pairs = [("ref", "bare_q"), ("e1_no_skills", "bare_q"), ("ref", "e1_no_skills"),
         ("ref", "e2_written_once"), ("ref", "e3_body_only"), ("ref", "e4_trigger_only"),
         ("e3_body_only", "e2_written_once"), ("e4_trigger_only", "e2_written_once"),
         ("e5_prefix_authoring", "e2_written_once"), ("ref", "e6_no_profile"),
         ("ref", "e7_k1"), ("e8_ours_nopb", "bare_q"), ("e8_ours_nopb", "ref"),
         ("e8_written_once_nopb", "bare_q"), ("e8_body_only_nopb", "bare_q"),
         ("e8_trigger_only_nopb", "bare_q"), ("e8_prefix_authoring_nopb", "bare_q"),
         ("e8_no_profile_nopb", "bare_q"), ("e8_k1_nopb", "bare_q")]
for a, b in pairs:
    m, lo, hi, n = diff(data[a][0], data[b][0], rng)
    print(f"{a:28s} - {b:22s} {100*m:+6.2f}pp  95% CI [{100*lo:+6.2f}, {100*hi:+6.2f}]  n={n}")
