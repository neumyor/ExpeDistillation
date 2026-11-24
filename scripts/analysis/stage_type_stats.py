#!/usr/bin/env python
"""Stage-type statistics over finished runs (read-only; paper experiment E11).

For every ``--run LABEL=RUN_DIR`` it reports

(a) stage types that occur in more than one training task: a stage type is
    a row of the run's ``subtask_types.jsonl``; its segments are the rows of
    ``segment_types.jsonl`` that name it; the type "occurs in more than one
    training task" when its segments come from at least two distinct
    ``task_id`` values (all segments are cut from training-task
    trajectories: baseline and practice). Counted over all types of the
    final type table, and over the types that own an active skill in the
    final bank.
(b) mid-task skills: the share of active skills of the final bank (the last
    learn phase's ``skill_bank.jsonl``) whose earliest source segment starts
    after step 0. This is exactly ``segmentation_metrics.skill_anchor``
    (minimum ``step_range[0]`` over ``source_subtasks``); a skill counts as
    mid-task when that minimum is > 0, i.e. no source segment starts at the
    task start. A looser variant (any source segment starts at step > 0) and
    the share of all segments starting at step > 0 are reported beside it.
(c) mid-task opening: the share of test attempts (with-bank arm of the test
    phase) that read a skill's SKILL.md after their first environment
    action. Attempts are read from the test phase's answer sheets; the
    command lines of every step are scanned in order with
    ``composites.skill_use.skill_read_events`` (a ``cat`` of a manifest
    SKILL.md or a ``ReadSkill[...]`` action), the first line that is not a
    skill read is the first environment action, and the attempt is a
    mid-task opening when a skill read follows it (later in the same step
    counts). For benchmarks that use ``ReadSkill`` the program's own
    ``segmentation_metrics.read_facts`` is computed as a cross-check.

Nothing is written inside a run directory; the table goes to stdout and,
optionally, to ``--out-json`` / ``--out-md`` (refused inside a run).

Run with the deployed interpreter, isolated:  .venv/bin/python -I \
scripts/analysis/stage_type_stats.py --run K=5=<run_dir> --run K=1=<run_dir>
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.composites import phase_bank
from expedistill.composites.skill_use import skill_read_events
from expedistill.infra.store import read_json, read_jsonl
from expedistill.stages import segmentation_metrics as seg

_ACTIVE = "active"


def _pct(num: int, den: int) -> Optional[float]:
    return round(100.0 * num / den, 2) if den else None


def _load_segments(run: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted((run / "stage2_segments" / "subtasks").glob("*.json")):
        rec = read_json(path)
        out[rec["subtask_id"]] = rec
    return out


def _types_block(run: Path, final_bank: List[Dict[str, Any]],
                 segments: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    types = read_jsonl(run / "stage2_segments" / "subtask_types.jsonl")
    assign = read_jsonl(run / "stage2_segments" / "segment_types.jsonl")
    by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    missing = 0
    for row in assign:
        seg_rec = segments.get(row["subtask_id"])
        if seg_rec is None:
            missing += 1
            continue
        by_type[row["type_id"]].append(seg_rec)
    active_types = {s["subtask_type"] for s in final_bank
                    if s.get("status") == _ACTIVE and s.get("subtask_type")}

    def stat(type_ids: Sequence[str]) -> Dict[str, Any]:
        multi = sum(1 for t in type_ids
                    if len({r["task_id"] for r in by_type.get(t, [])}) > 1)
        empty = sum(1 for t in type_ids if not by_type.get(t))
        return {"n_types": len(type_ids), "multi_task": multi,
                "share_multi_task": _pct(multi, len(type_ids)),
                "types_without_segments": empty}

    all_ids = [t["type_id"] for t in types]
    p02_ids = [t for t in all_ids if any(
        r["phase_label"] == "p02:learn" for r in by_type.get(t, []))]
    tasks_per_type = sorted(len({r["task_id"] for r in by_type.get(t, [])})
                            for t in all_ids)
    return {
        "all_types": stat(all_ids),
        "types_with_active_skill": stat(sorted(active_types)),
        "segments_total": len(assign),
        "segments_unresolved": missing,
        "distinct_training_tasks": len({r["task_id"] for r in segments.values()}),
        "tasks_per_type_sorted": tasks_per_type,
        "segments_per_type_sorted": sorted(len(by_type.get(t, []))
                                           for t in all_ids),
    }


def _skills_block(final_bank: List[Dict[str, Any]], run: Path,
                  segments: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    active = [s for s in final_bank if s.get("status") == _ACTIVE]
    min_starts: List[Optional[int]] = []
    any_mid = 0
    known = 0
    per_skill = []
    for s in active:
        starts = [int(segments[i]["step_range"][0])
                  for i in s.get("source_subtasks") or [] if i in segments]
        if not starts:
            min_starts.append(None)
            per_skill.append({"skill_id": s["skill_id"], "starts": []})
            continue
        known += 1
        min_starts.append(min(starts))
        any_mid += any(x > 0 for x in starts)
        per_skill.append({"skill_id": s["skill_id"], "starts": sorted(starts),
                          "tasks": len({segments[i]["task_id"]
                                        for i in s["source_subtasks"]
                                        if i in segments})})
    mid = sum(1 for m in min_starts if m is not None and m > 0)
    seg_starts = [int(r["step_range"][0]) for r in segments.values()]
    return {
        "n_skills_active": len(active),
        "n_skills_discarded": len(final_bank) - len(active),
        "n_known": known,
        "mid_task_skills": mid,
        "share_mid_task_skills": _pct(mid, known),
        "skills_any_source_mid_task": any_mid,
        "share_any_source_mid_task": _pct(any_mid, known),
        "min_start_per_skill": min_starts,
        "per_skill": per_skill,
        "segments_total": len(seg_starts),
        "segments_start_gt0": sum(1 for x in seg_starts if x > 0),
        "share_segments_start_gt0": _pct(
            sum(1 for x in seg_starts if x > 0), len(seg_starts)),
    }


def _skill_dirs(phase_dir: Path) -> List[str]:
    folder = phase_dir / "skill_folder"
    return sorted(f"{p.parent.name}/SKILL.md"
                  for p in folder.glob("*/SKILL.md"))


def _lines(steps: Sequence[Dict[str, Any]]) -> List[Tuple[int, str]]:
    out = []
    for index, step in enumerate(steps):
        for line in str(step.get("action", "")).split("\n"):
            if line.strip():
                out.append((index, line))
    return out


def _is_read(line: str, manifest: Dict[str, Any]) -> bool:
    stub = SimpleNamespace(steps=[SimpleNamespace(action=line)])
    return bool(skill_read_events(stub, manifest))


def _read_facts(steps: Sequence[Dict[str, Any]],
                manifest: Dict[str, Any]) -> Dict[str, Any]:
    first_env_line = None
    mid = False
    reads = 0
    first_read_step = None
    read_steps = set()
    env_steps = set()
    for pos, (index, line) in enumerate(_lines(steps)):
        if _is_read(line, manifest):
            reads += 1
            read_steps.add(index)
            if first_read_step is None:
                first_read_step = index
            if first_env_line is not None:
                mid = True
        else:
            env_steps.add(index)
            if first_env_line is None:
                first_env_line = pos
    # step-level variant: the first step holding no skill read is the first
    # environment step; a read in a later step is a mid-task opening.
    first_env_step = min((i for i in range(len(steps))
                          if i not in read_steps), default=None)
    mid_step = (first_env_step is not None
                and any(i > first_env_step for i in read_steps))
    return {"opened": reads > 0, "reads": reads, "mid_task_open": mid,
            "mid_task_open_step_level": mid_step,
            "first_read_step": first_read_step,
            "opened_after_step0": (first_read_step or 0) > 0}


def _opening_block(run: Path, test_label: str) -> Dict[str, Any]:
    phase_dir = phase_bank.phase_bank_dir(run, test_label)
    names = _skill_dirs(phase_dir)
    manifest = {"files": [{"skill_id": n, "filename": n} for n in names]}
    summary = read_json(phase_dir / "phase_summary.json")
    logged = summary.get("skill_files_read") or {}
    n = opened = mid = valid = mid_valid = opened_valid = mid_step = 0
    after0 = 0
    first_steps: List[int] = []
    code_n = code_open = code_mid = 0
    agree = disagree = 0
    for path in sorted((phase_dir / "answer_sheets").glob("*.json")):
        sheet = read_json(path)
        execution = sheet.get("execution") or {}
        if execution.get("arm") != "with_bank":
            continue
        steps = list(execution.get("steps") or [])
        facts = _read_facts(steps, manifest)
        verdict = (sheet.get("grading") or {}).get("verdict")
        n += 1
        opened += facts["opened"]
        mid += facts["mid_task_open"]
        mid_step += facts["mid_task_open_step_level"]
        after0 += facts["opened_after_step0"] and facts["opened"]
        if facts["first_read_step"] is not None:
            first_steps.append(facts["first_read_step"])
        if verdict in ("success", "failure"):
            valid += 1
            opened_valid += facts["opened"]
            mid_valid += facts["mid_task_open"]
        code = seg.read_facts(steps)
        code_n += 1
        code_open += code["opened"]
        code_mid += code["mid_task_open"]
        # cross-check against the run's own skill_files_read log
        tid = "traj/{}/{}/{}/{:02d}".format(
            test_label.replace(":", "_"), execution.get("task_ref"),
            execution.get("role"), int(execution.get("sample_index", 0)))
        if tid in logged:
            ours = {s for s in logged[tid]}
            got = set()
            stubs = SimpleNamespace(steps=[SimpleNamespace(action=l)
                                           for _, l in _lines(steps)])
            for ev in skill_read_events(stubs, manifest):
                got.add(ev["skill_id"])
            norm_logged = {x.replace("type/", "type-").replace("/skill", "-skill")
                           + "/SKILL.md" for x in ours}
            if norm_logged == got:
                agree += 1
            else:
                disagree += 1
    first_steps.sort()
    return {
        "test_phase": test_label,
        "skills_in_test_folder": len(names),
        "attempts": n,
        "attempts_graded_valid": valid,
        "opened": opened,
        "share_opened": _pct(opened, n),
        "mid_task_open": mid,
        "share_mid_task_open": _pct(mid, n),
        "mid_task_open_step_level": mid_step,
        "share_mid_task_open_step_level": _pct(mid_step, n),
        "share_mid_task_open_graded_valid": _pct(mid_valid, valid),
        "share_opened_graded_valid": _pct(opened_valid, valid),
        "opened_first_read_after_step0": after0,
        "share_mid_among_opened": _pct(mid, opened),
        "first_read_step_median": (first_steps[len(first_steps) // 2]
                                   if first_steps else None),
        "program_read_facts_ReadSkill_only": {
            "trials": code_n, "opened": code_open, "mid_task_open": code_mid,
            "share_mid_task_open": _pct(code_mid, code_n)},
        "crosscheck_vs_phase_summary_skill_files_read": {
            "agree": agree, "disagree": disagree,
            "logged_trajectories": len(logged)},
        "test_avg_at_3": summary["with_bank"]["avg_at_n"],
        "test_pass_at_3": summary["with_bank"]["pass_at_n"],
        "test_complete": summary.get("complete"),
        "bank_reference": summary.get("bank_reference"),
    }


def _final_bank(run: Path) -> Tuple[List[Dict[str, Any]], str]:
    labels = phase_bank.bank_phase_labels(run)
    path = phase_bank.phase_bank_path(run, labels[-1])
    return read_jsonl(path), labels[-1]


def _test_label(run: Path) -> str:
    found = sorted(path.name for path in (run / "phases").glob("p*_test"))
    if len(found) != 1:
        raise SystemExit(f"{run}: expected one test phase, found {found}")
    return found[0].replace("_", ":", 1)


def run_one(label: str, run_dir: Path) -> Dict[str, Any]:
    run = run_dir.resolve()
    bank, bank_label = _final_bank(run)
    segments = _load_segments(run)
    test_label = _test_label(run)
    return {
        "label": label, "run_dir": str(run), "final_bank_phase": bank_label,
        "stage_types": _types_block(run, bank, segments),
        "skills": _skills_block(bank, run, segments),
        "opening": _opening_block(run, test_label),
    }


def render(results: Sequence[Dict[str, Any]]) -> str:
    cols = [r["label"] for r in results]
    lines = ["| metric | " + " | ".join(cols) + " |",
             "|---|" + "|".join("---" for _ in cols) + "|"]

    def row(name: str, fn) -> None:
        lines.append(f"| {name} | " + " | ".join(str(fn(r)) for r in results)
                     + " |")

    def frac(a: int, b: int, p) -> str:
        return f"{a}/{b} ({p}%)"

    row("stage types (all, final table)", lambda r: r["stage_types"]["all_types"]["n_types"])
    row("types in >1 training task (all)", lambda r: frac(
        r["stage_types"]["all_types"]["multi_task"],
        r["stage_types"]["all_types"]["n_types"],
        r["stage_types"]["all_types"]["share_multi_task"]))
    row("types with an active skill", lambda r: r["stage_types"]["types_with_active_skill"]["n_types"])
    row("... of which in >1 training task", lambda r: frac(
        r["stage_types"]["types_with_active_skill"]["multi_task"],
        r["stage_types"]["types_with_active_skill"]["n_types"],
        r["stage_types"]["types_with_active_skill"]["share_multi_task"]))
    row("segments / distinct training tasks", lambda r: "{} / {}".format(
        r["stage_types"]["segments_total"], r["stage_types"]["distinct_training_tasks"]))
    row("skills (active)", lambda r: r["skills"]["n_skills_active"])
    row("mid-task skills (min source start > 0)", lambda r: frac(
        r["skills"]["mid_task_skills"], r["skills"]["n_known"],
        r["skills"]["share_mid_task_skills"]))
    row("skills with any source start > 0", lambda r: frac(
        r["skills"]["skills_any_source_mid_task"], r["skills"]["n_known"],
        r["skills"]["share_any_source_mid_task"]))
    row("segments starting at step > 0", lambda r: frac(
        r["skills"]["segments_start_gt0"], r["skills"]["segments_total"],
        r["skills"]["share_segments_start_gt0"]))
    row("test attempts (with bank)", lambda r: r["opening"]["attempts"])
    row("attempts opening any skill", lambda r: frac(
        r["opening"]["opened"], r["opening"]["attempts"], r["opening"]["share_opened"]))
    row("mid-task opening", lambda r: frac(
        r["opening"]["mid_task_open"], r["opening"]["attempts"],
        r["opening"]["share_mid_task_open"]))
    row("mid-task opening (step-level variant)", lambda r: frac(
        r["opening"]["mid_task_open_step_level"], r["opening"]["attempts"],
        r["opening"]["share_mid_task_open_step_level"]))
    row("mid-task opening (ReadSkill-only program def.)", lambda r: frac(
        r["opening"]["program_read_facts_ReadSkill_only"]["mid_task_open"],
        r["opening"]["program_read_facts_ReadSkill_only"]["trials"],
        r["opening"]["program_read_facts_ReadSkill_only"]["share_mid_task_open"]))
    row("test avg@3 / pass@3 (run summary)", lambda r: "{:.2f} / {:.2f}".format(
        100 * r["opening"]["test_avg_at_3"], 100 * r["opening"]["test_pass_at_3"]))
    row("read-log cross-check agree/disagree", lambda r: "{}/{}".format(
        r["opening"]["crosscheck_vs_phase_summary_skill_files_read"]["agree"],
        r["opening"]["crosscheck_vs_phase_summary_skill_files_read"]["disagree"]))
    return "\n".join(lines)


def _parse(spec: str) -> Tuple[str, Path]:
    label, sep, path = spec.rpartition("=")
    if not sep or not label or not path:
        raise SystemExit(f"--run expects LABEL=RUN_DIR, got {spec!r}")
    return label, Path(path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", action="append", required=True,
                        metavar="LABEL=RUN_DIR")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    args = parser.parse_args(argv)
    parsed = [_parse(s) for s in args.run]
    for _, run_dir in parsed:
        for out in (args.out_json, args.out_md):
            if out is not None and run_dir.resolve() in Path(out).resolve().parents:
                raise SystemExit(f"{out} is inside run {run_dir}")
    results = [run_one(label, run_dir) for label, run_dir in parsed]
    table = render(results)
    print(table)
    if args.out_md:
        Path(args.out_md).write_text(table + "\n", encoding="utf-8")
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(results, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
