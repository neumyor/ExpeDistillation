#!/usr/bin/env python3
"""Integrity audit of a finished evidence-driven run (baseline / learn /
practice / test sequence). Read-only: it never writes into the run directory.

Usage:
    .venv/bin/python scripts/audit/audit_run.py <run dir> [--out report.json]

Checks (design spec section 7, "run 跑完后的完整性检查", items 1-6):

1. ``pipeline_log.jsonl`` covers every label of the frozen sequence;
2. every execute phase's ``phase_summary.json`` is complete with empty
   ``infra_missing``; accepted holes are listed (WARN); every answer sheet
   with an execution is graded success / failure / invalid; no coverage
   record is ``missing`` (``terminal`` ones are listed as WARN);
3. per learn phase: findings + terminal diagnosis failures (``ModelOutputError``
   rows of ``failed_units.jsonl``) == the weak executor's failed sheets of its
   input phase;
4. per learn phase: evidence rows + ``Unlocated`` rows == findings; every gap
   type and every card with trigger or content evidence has a receipt in
   ``outcomes.jsonl``;
5. per learn phase: ``learn_summary.json`` carries ``bank`` and
   ``bank_summary.json`` lists the same members as ``skill_bank.jsonl``;
6. every test phase's two arms cover the frozen test list.

Extras: stored trajectory files agree with their ids and phases; the
failed-units ledger by phase and error class; executor replies cut at the
output limit (WARN). When the run's benchmark is ``tb``: every skills-mode
trajectory uploaded its read suffix; trials graded invalid per phase; and a
weak-executor trial whose Harbor config (``<remote_root>/configs/<run_id>.yaml``,
checked only when readable on this host) sets ``reasoning_effort`` fails.

Prints one ``PASS`` / ``FAIL`` / ``WARN`` / ``INFO`` line per check; exits 1
on any FAIL, 0 otherwise. ``--out`` writes the same list as JSON to a file
outside the run directory.
"""

import argparse
import collections
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"
VERDICTS = ("success", "failure", "invalid")
EXECUTE_ROLES = ("baseline", "practice", "test")
TRAJECTORY_STORE = "stage1_baseline/trajectories"
MODEL_OUTPUT_ERROR = "ModelOutputError"
UNLOCATED = "Unlocated"


class Report:
    def __init__(self) -> None:
        self.items: List[Dict[str, str]] = []

    def add(self, status: str, check: str, detail: str) -> None:
        self.items.append({"status": status, "check": check,
                           "detail": detail})
        print(f"{status} {check}: {detail}", flush=True)

    def expect(self, ok: bool, check: str, detail: str) -> None:
        self.add(PASS if ok else FAIL, check, detail)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if item["status"] == FAIL)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Rows of a JSONL file; [] when absent (failure ledgers appear only on
    the first failure)."""
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{number} is not a JSON object")
            rows.append(row)
    return rows


def phase_dir_name(label: str) -> str:
    return label.replace(":", "_")


def _flatten(value: Any) -> List[str]:
    """Hole lists come as a list (baseline, practice) or a dict of lists per
    arm (test)."""
    if not value:
        return []
    if isinstance(value, dict):
        return [f"{arm}:{item}" for arm, items in sorted(value.items())
                for item in (items or [])]
    return [str(item) for item in value]


class Run:
    """The parts of a run directory every check reads."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        self.manifest = read_json(run_dir / "manifest.json")
        config = self.manifest.get("config") or {}
        self.benchmark = self.manifest.get("benchmark") or config.get("benchmark")
        sequence = config.get("sequence")
        phases = config.get("phases")
        if not isinstance(sequence, list) or not sequence \
                or not isinstance(phases, dict):
            raise ValueError("manifest.json has no frozen config.sequence "
                             "and config.phases")
        self.phases: List[Dict[str, Any]] = []
        for index, name in enumerate(sequence):
            spec = phases.get(name) or {}
            label = f"p{index + 1:02d}:{name}"
            self.phases.append({
                "label": label, "name": name, "spec": spec,
                "role": spec.get("role"), "kind": spec.get("kind"),
                "dir": run_dir / "phases" / phase_dir_name(label)})
        self.failures = read_jsonl(run_dir / "failed_units.jsonl")
        self._sheets: Dict[str, List[Dict[str, Any]]] = {}

    def sheets(self, label: str) -> List[Dict[str, Any]]:
        if label not in self._sheets:
            directory = self.dir / "phases" / phase_dir_name(label) \
                / "answer_sheets"
            self._sheets[label] = [read_json(path) for path in
                                   sorted(directory.glob("*.json"))]
        return self._sheets[label]

    def failure_ids(self, stage: str, error_class: str) -> Set[str]:
        return {str(row.get("unit_id")) for row in self.failures
                if row.get("stage") == stage
                and row.get("error_class") == error_class}

    def execute_phases(self) -> List[Dict[str, Any]]:
        return [phase for phase in self.phases
                if phase["role"] in EXECUTE_ROLES]

    def learn_phases(self) -> List[Dict[str, Any]]:
        return [phase for phase in self.phases if phase["role"] == "learn"]


def verdict_of(sheet: Dict[str, Any]) -> Optional[str]:
    grading = sheet.get("grading")
    return grading.get("verdict") if isinstance(grading, dict) else None


def is_weak_failure(sheet: Dict[str, Any]) -> bool:
    execution = sheet.get("execution")
    return (isinstance(execution, dict)
            and execution.get("model_role") == "weak"
            and verdict_of(sheet) == "failure")


# ---------------------------------------------------------------------------
# Checks 1-6
# ---------------------------------------------------------------------------

def check_ledger(run: Run, report: Report) -> None:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in read_jsonl(run.dir / "pipeline_log.jsonl"):
        if isinstance(row.get("stage"), str) and isinstance(row.get("summary"), dict):
            latest[row["stage"]] = row["summary"]
    expected = ["p00:freeze"] + [phase["label"] for phase in run.phases]
    missing = [label for label in expected if label not in latest]
    report.expect(not missing, "1 ledger",
                  f"missing phases {missing}" if missing
                  else f"all {len(expected)} labels completed")


def check_execute_phase(run: Run, phase: Dict[str, Any], report: Report) -> None:
    label = phase["label"]
    summary_path = phase["dir"] / "phase_summary.json"
    if not summary_path.exists():
        report.add(FAIL, f"2 {label} summary", "phase_summary.json is missing")
        return
    summary = read_json(summary_path)
    missing = _flatten(summary.get("infra_missing"))
    complete = summary.get("complete") is True
    report.expect(complete and not missing, f"2 {label} summary",
                  f"complete={summary.get('complete')} infra_missing="
                  f"{len(missing)}" + (f" {missing[:10]}" if missing else ""))
    accepted = _flatten(summary.get("infra_accepted"))
    if accepted:
        report.add(WARN, f"2 {label} accepted holes",
                   f"{len(accepted)}: {accepted}")

    sheets = run.sheets(label)
    verdicts = collections.Counter(verdict_of(sheet) for sheet in sheets
                                   if sheet.get("execution"))
    ungraded = sum(count for verdict, count in verdicts.items()
                   if verdict not in VERDICTS)
    foreign = sum(1 for sheet in sheets if sheet.get("phase_label") != label)
    report.expect(bool(sheets) and not ungraded and not foreign,
                  f"2 {label} sheets",
                  f"{len(sheets)} sheets, verdicts "
                  f"{dict(sorted((str(k), v) for k, v in verdicts.items()))}, "
                  f"ungraded {ungraded}, other phase label {foreign}")

    coverage = read_jsonl(phase["dir"] / "coverage.jsonl")
    statuses = collections.Counter(str(row.get("status")) for row in coverage)
    report.expect(bool(coverage) and not statuses.get("missing"),
                  f"2 {label} coverage", f"{len(coverage)} records "
                  f"{dict(sorted(statuses.items()))}")
    if statuses.get("terminal"):
        report.add(WARN, f"2 {label} coverage terminal",
                   f"{statuses['terminal']} trial(s) are accepted holes")


def _input_label(run: Run, phase: Dict[str, Any],
                 summary: Dict[str, Any]) -> Optional[str]:
    if isinstance(summary.get("input_phase"), str):
        return summary["input_phase"]
    earlier = [other for other in run.phases[:run.phases.index(phase)]
               if other["role"] in ("baseline", "practice")]
    return earlier[-1]["label"] if earlier else None


def check_learn_phase(run: Run, phase: Dict[str, Any], report: Report) -> None:
    label = phase["label"]
    directory = phase["dir"]
    summary_path = directory / "learn_summary.json"
    if not summary_path.exists():
        report.add(FAIL, f"3 {label}", "learn_summary.json is missing")
        return
    summary = read_json(summary_path)

    source = _input_label(run, phase, summary)
    weak_failed = {sheet["sheet_id"] for sheet in run.sheets(source)
                   if is_weak_failure(sheet)} if source else set()
    findings = {row["sheet_id"] for row in read_jsonl(directory / "findings.jsonl")}
    diag_failed = run.failure_ids(label, MODEL_OUTPUT_ERROR) & weak_failed
    stray = findings - weak_failed
    report.expect(
        findings | diag_failed == weak_failed and not (findings & diag_failed)
        and not stray, f"3 {label} diagnoses",
        f"input {source}: weak failures {len(weak_failed)}, findings "
        f"{len(findings)}, diagnosis failures {len(diag_failed)}, findings "
        f"outside the input {len(stray)}")

    evidence = read_jsonl(directory / "evidence.jsonl")
    located = {row["sheet_id"] for row in evidence}
    unlocated = run.failure_ids(label, UNLOCATED) & findings
    report.expect(
        located | unlocated == findings and not (located & unlocated)
        and len(located) == len(evidence), f"4 {label} evidence",
        f"evidence {len(evidence)}, unlocated {len(unlocated)}, findings "
        f"{len(findings)}")

    outcomes = [row for row in read_jsonl(directory / "outcomes.jsonl")
                if row.get("phase_label", label) == label]
    targets = {row.get("target_id") for row in outcomes} \
        | {row.get("skill_id") for row in outcomes}
    wanted = set()
    for row in evidence:
        category = row.get("category")
        if category == "gap":
            wanted.add(row.get("type_id"))
        elif category in ("trigger", "content"):
            wanted.add(row.get("skill_id"))
    without = sorted(str(target) for target in wanted - targets)
    report.expect(not without, f"4 {label} receipts",
                  f"{len(wanted)} targets with evidence, without receipt "
                  f"{without}")

    bank = summary.get("bank")
    members = sorted((bank or {}).get("members") or [])
    rows = sorted(row.get("skill_id") for row in
                  read_jsonl(directory / "skill_bank.jsonl"))
    bank_summary_path = directory / "bank_summary.json"
    listed = sorted(read_json(bank_summary_path).get("members") or []) \
        if bank_summary_path.exists() else None
    report.expect(isinstance(bank, dict) and listed == rows and members == rows,
                  f"5 {label} bank",
                  f"bank key {'present' if isinstance(bank, dict) else 'MISSING'}, "
                  f"skill_bank.jsonl {len(rows)} rows, bank_summary members "
                  f"{'MISSING' if listed is None else len(listed)}")


def check_test_phase(run: Run, phase: Dict[str, Any], report: Report) -> None:
    label = phase["label"]
    summary_path = phase["dir"] / "phase_summary.json"
    if not summary_path.exists():
        return  # already a FAIL under check 2
    summary = read_json(summary_path)
    n_test = len((run.manifest.get("split") or {}).get("test") or [])
    arms = {arm: (summary.get(arm) or {}).get("tasks")
            for arm in ("with_bank", "control")}
    report.expect(n_test > 0 and all(tasks == n_test for tasks in arms.values()),
                  f"6 {label} arms", f"frozen test list {n_test}, arms {arms}")


# ---------------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------------

def trajectory_files(run: Run) -> Iterable[Path]:
    yield from sorted((run.dir / TRAJECTORY_STORE).glob("*.json"))
    for store in sorted((run.dir / "phases").glob("*/*/store")):
        yield from sorted((store / TRAJECTORY_STORE).glob("*.json"))


def _tag_label(trajectory_id: str) -> Optional[str]:
    if not trajectory_id.startswith("traj/"):
        return None
    tag = trajectory_id[len("traj/"):].partition("/")[0]
    number, sep, name = tag.partition("_")
    return f"{number}:{name}" if sep else None


def load_trajectories(run: Run, report: Report) -> List[Dict[str, Any]]:
    known = {phase["label"] for phase in run.execute_phases()}
    trajectories, bad = [], []
    by_phase: collections.Counter = collections.Counter()
    for path in trajectory_files(run):
        record = read_json(path)
        trajectory_id = str(record.get("trajectory_id"))
        label = _tag_label(trajectory_id)
        if urllib.parse.unquote(path.stem) != trajectory_id or label not in known:
            bad.append(str(path.relative_to(run.dir)))
        by_phase[label] += 1
        trajectories.append(record)
    report.expect(not bad, "extra trajectories",
                  f"{len(trajectories)} stored, by phase "
                  f"{dict(sorted((str(k), v) for k, v in by_phase.items()))}, "
                  f"id/file/phase mismatches {bad[:10]}")
    return trajectories


def report_failures(run: Run, report: Report) -> None:
    counts = collections.Counter((str(row.get("stage")), str(row.get("error_class")))
                                 for row in run.failures)
    report.add(INFO, "extra failed_units",
               f"{len(run.failures)} rows "
               + (", ".join(f"{stage} {name} x{n}" for (stage, name), n
                            in sorted(counts.items())) or "(none)"))


def report_executor_cutoffs(run: Run, report: Report) -> None:
    reasons: collections.Counter = collections.Counter()
    for row in read_jsonl(run.dir / "llm_requests" / "usage.jsonl"):
        if str(row.get("role", "")).startswith("executor"):
            reasons[str((row.get("meta") or {}).get("finish_reason"))] += 1
    report.add(WARN if reasons.get("length") else INFO,
               "extra executor finish reasons", str(dict(sorted(reasons.items()))))


def check_tb(run: Run, trajectories: List[Dict[str, Any]],
             report: Report) -> None:
    skills = [t for t in trajectories
              if (t.get("meta") or {}).get("eval_mode") == "skills"]
    no_suffix = [t.get("trajectory_id") for t in skills
                 if (t.get("meta") or {}).get("read_suffix_uploaded") is not True]
    report.expect(not no_suffix, "tb read suffix",
                  f"{len(skills)} skills-mode trajectories, without uploaded "
                  f"read suffix {len(no_suffix)} {no_suffix[:10]}")

    for phase in run.execute_phases():
        sheets = run.sheets(phase["label"])
        invalid = sum(1 for sheet in sheets if verdict_of(sheet) == "invalid")
        report.add(INFO, f"tb {phase['label']} invalid",
                   f"{invalid} of {len(sheets)} trials graded invalid")

    weak = [t for t in trajectories if t.get("model_role") == "weak"]
    readable, effort = 0, []
    for trajectory in weak:
        meta = trajectory.get("meta") or {}
        root, run_id = meta.get("remote_root"), meta.get("run_id")
        if not root or not run_id:
            continue
        try:
            text = (Path(root) / "configs" / f"{run_id}.yaml").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        readable += 1
        if any("reasoning_effort" in line and not line.lstrip().startswith("#")
               for line in text.splitlines()):
            effort.append(trajectory.get("trajectory_id"))
    if not readable:
        report.add(INFO, "tb weak reasoning_effort",
                   f"not checked: no Harbor config of the {len(weak)} weak "
                   "trials is readable on this host")
    else:
        report.expect(not effort, "tb weak reasoning_effort",
                      f"{readable} of {len(weak)} weak-trial Harbor configs "
                      f"read, with reasoning_effort {len(effort)} {effort[:10]}")


# ---------------------------------------------------------------------------

def _guarded(report: Report, name: str, check, *args) -> Any:
    """Run one check; an unreadable or malformed record is that check's FAIL,
    never the end of the audit."""
    try:
        return check(*args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        report.add(FAIL, name, f"could not check: {type(exc).__name__}: {exc}")
        return None


def audit(run_dir: Path) -> Report:
    report = Report()
    try:
        run = Run(run_dir)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        report.add(FAIL, "manifest", f"{type(exc).__name__}: {exc}")
        return report
    report.add(INFO, "run", f"benchmark {run.benchmark}, phases "
               + ", ".join(f"{p['label']} ({p['role']})" for p in run.phases))
    _guarded(report, "1 ledger", check_ledger, run, report)
    for phase in run.execute_phases():
        _guarded(report, f"2 {phase['label']}", check_execute_phase,
                 run, phase, report)
    for phase in run.learn_phases():
        _guarded(report, f"3-5 {phase['label']}", check_learn_phase,
                 run, phase, report)
    for phase in run.execute_phases():
        if phase["role"] == "test":
            _guarded(report, f"6 {phase['label']}", check_test_phase,
                     run, phase, report)
    trajectories = _guarded(report, "extra trajectories", load_trajectories,
                            run, report) or []
    _guarded(report, "extra failed_units", report_failures, run, report)
    _guarded(report, "extra executor finish reasons",
             report_executor_cutoffs, run, report)
    if run.benchmark == "tb":
        _guarded(report, "tb", check_tb, run, trajectories, report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path,
                        help="write the report as JSON here (outside the run)")
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        parser.error(f"not a directory: {run_dir}")
    out = args.out.resolve() if args.out else None
    if out is not None and (out == run_dir or run_dir in out.parents):
        parser.error("--out must not be inside the audited run directory")

    report = audit(run_dir)
    verdict = "AUDIT FAILED" if report.failed else "AUDIT PASSED"
    print(f"{verdict}: {report.failed} FAIL of {len(report.items)} lines")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"run_dir": str(run_dir),
                                   "passed": not report.failed,
                                   "items": report.items},
                                  indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
