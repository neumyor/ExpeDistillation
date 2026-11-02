"""A finished run directory with a test phase, built by hand.

The paper-table readers need only the frozen manifest and the test phase's
trial-level artifacts, so the helper writes exactly those through the real
writers (answer sheets, coverage, unit specs) and computes
``phase_summary.json`` the way ``stages/phase_execute/final_test.py`` does:
unit summaries -> task metrics -> ``summarize_metrics`` -> ``final_test_delta``.
"""

from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from expedistill.composites import (
    answer_sheets as sheet_io,
    decision,
    phase_bank,
    phase_report,
    unit_ids,
    unit_summary,
)
from expedistill.core import ids
from expedistill.core.answer_sheet import (
    CoverageRecord,
    ExecutionRecord,
    UnitSpec,
)
from expedistill.core.config import phase_label
from expedistill.infra.store import write_json

__all__ = ["VERDICTS", "write_test_run"]

#: One trial of a hand-built arm: a graded success / failure, a run that
#: came back without a scorer verdict (``invalid``), or a trial that is
#: absent (``missing``: never stored, ``hole``: an accepted infra hole).
VERDICTS = ("success", "failure", "invalid", "missing", "hole")

_RAW_CORRECTNESS = {"success": True, "failure": False, "invalid": None}


def _execution(unit_id, task_ref, arm, index, verdict, *, role, label,
               model):
    return ExecutionRecord(
        sheet_id=f"{unit_id}::{task_ref}/{arm}/{index:02d}",
        unit_id=unit_id, phase_label=label, task_ref=task_ref, role=role,
        arm=arm, model_role=role, model_name=model, sample_index=index,
        raw_correctness=_RAW_CORRECTNESS[verdict])


def write_test_run(
        root: Path, *, with_bank: Mapping[str, Sequence[str]],
        control: Mapping[str, Sequence[str]], benchmark: str = "alfworld",
        role: str = "weak", model: str = "weak-model", mode: str = "split",
        sequence: Sequence[str] = ("baseline", "learn", "test"),
        test_phases: Sequence[str] = ("test",), data_phase: str = "test",
        external_control: bool = False,
        control_source: Optional[str] = None, name: str = "run") -> Path:
    """Write one run as ``root/<name>`` and return its directory.

    ``with_bank`` and ``control`` map a raw task id to that task's trials
    (each one of :data:`VERDICTS`, attempt index = list position).
    ``external_control`` builds the control arm the way an external
    campaign control is stored: reward-valued external trials graded by the
    ``external_reward`` rule (only ``success`` / ``failure`` are possible).
    ``data_phase`` names which of ``test_phases`` carries the measurement.
    """
    run_dir = Path(root) / name
    label = phase_label(list(sequence).index(data_phase), data_phase)
    phase_dir = phase_bank.phase_bank_dir(run_dir, label)
    roles = {phase: ("test" if phase in test_phases else
                     "baseline" if phase == "baseline" else "learn")
             for phase in sequence}
    write_json(run_dir / "manifest.json", {
        "benchmark": benchmark,
        "mode": mode,
        "config": {
            "models": {"executor_weak": "weak-model",
                       "executor_strong": "strong-model"},
            "sequence": list(sequence),
            "phases": {phase: {"role": roles[phase],
                               "delivery": {"name": "whole_bank"}}
                       for phase in dict.fromkeys(sequence)},
        },
    }, indent=2)

    arms = {"with_bank": with_bank, "control": control}
    specs: List[UnitSpec] = []
    executions: List[ExecutionRecord] = []
    coverage: List[CoverageRecord] = []
    for arm, tasks in arms.items():
        for raw_id in sorted(tasks):
            trials = list(tasks[raw_id])
            task_ref = ids.make_task_id(benchmark, raw_id)
            injected = None if arm == "control" else [
                {"skill_id": "s1", "version": 1}]
            unit_id = unit_ids.make_unit_id(
                phase_label=label, benchmark=benchmark, task_ref=task_ref,
                role=role, skills_injected=injected)
            specs.append(UnitSpec(
                unit_id=unit_id, phase_label=label, task_ref=task_ref,
                role=role, arm=arm, expected_attempts=len(trials),
                injected_skills=injected))
            for index, verdict in enumerate(trials):
                if verdict not in VERDICTS:
                    raise ValueError(f"unknown trial verdict {verdict!r}")
                if verdict in ("missing", "hole"):
                    coverage.append(CoverageRecord(
                        unit_id=unit_id, attempt_index=index, present=False,
                        status="missing",
                        cause=("missing" if verdict == "missing"
                               else "accepted_hole")))
                    continue
                coverage.append(CoverageRecord(
                    unit_id=unit_id, attempt_index=index, present=True,
                    status="present"))
                if arm == "control" and external_control:
                    executions.append(sheet_io.execution_from_external(
                        task_ref=task_ref, unit_id=unit_id,
                        phase_label=label, role=role,
                        reward=1.0 if verdict == "success" else 0.0,
                        sample_index=index, source="campaign.json",
                        sha256="0" * 64))
                else:
                    executions.append(_execution(
                        unit_id, task_ref, arm, index, verdict, role=role,
                        label=label, model=model))

    sheets = sheet_io.assemble_answer_sheets(
        executions, coverage, phase_label=label)
    sheet_io.grade_answer_sheets(
        [sheet for sheet in sheets
         if sheet.execution is not None and sheet.execution.source != "external"],
        "scorer")
    sheet_io.grade_answer_sheets(
        [sheet for sheet in sheets
         if sheet.execution is not None and sheet.execution.source == "external"],
        "external_reward")
    sheet_io.write_answer_sheets(
        sheets, phase_dir / phase_report.ANSWER_SHEETS_DIR,
        render_markdown=False)
    sheet_io.write_coverage(coverage, phase_dir / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(specs, phase_dir)

    report = phase_report.build_phase_report(
        sheets, specs, phase_kind="test", learning_usable=False)
    metrics: Dict[str, dict] = {}
    for arm in arms:
        units = [unit for unit in report.units if unit.arm == arm]
        metrics[arm] = unit_summary.summarize_metrics(
            unit_summary.task_metrics(units, 0), 0)
    write_json(phase_dir / "phase_summary.json", {
        "role": "test",
        "mode": mode,
        "control_source": control_source or (
            "external_campaign" if external_control else "fresh_bare"),
        "with_bank": metrics["with_bank"],
        "control": metrics["control"],
        "delta": decision.final_test_delta(
            metrics["with_bank"], metrics["control"]),
        "infra_missing": {"with_bank": [], "control": []},
        "infra_accepted": {"with_bank": [], "control": []},
        "complete": True,
    }, indent=2)
    return run_dir
