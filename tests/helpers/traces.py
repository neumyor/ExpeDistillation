"""A run directory holding real baseline answer sheets for provenance tests.

Writers now record exact ``TraceRef`` locations and refuse a pointer that
does not resolve, so a test that lands a skill needs the sheets its source
trajectory ran. This builds them once, the same way the baseline phase does
(sheet id ``<unit>::<trajectory id>``, one sheet per attempt).
"""

from pathlib import Path
from typing import Iterable, Optional

from expedistill.composites import answer_sheets, phase_report
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
)
from expedistill.core.models import Step

__all__ = ["baseline_sheet", "write_baseline_sheets"]


def baseline_sheet(task_id: str, role: str, attempt: int, *,
                   n_steps: int = 6, benchmark: str = "tb",
                   phase_label: str = "p01:baseline",
                   correct: Optional[bool] = None) -> AnswerSheet:
    """One baseline answer sheet for ``traj/<task>/<role>/<attempt>``."""
    trajectory = f"traj/{task_id}/{role}/{attempt:02d}"
    unit = make_unit_id(phase_label=phase_label, benchmark=benchmark,
                        task_ref=task_id, role=role, skills_injected=None)
    sheet_id = f"{unit}::{trajectory}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=unit, phase_label=phase_label,
        task_ref=task_id, role=role, arm=role, model_role=role,
        model_name="m", sample_index=attempt,
        steps=[Step(action=f"{role}{attempt}-a{i}", observation="obs")
               for i in range(n_steps)],
        artifacts={"trajectory_id": trajectory}, raw_correctness=correct)
    return AnswerSheet(
        sheet_id=sheet_id, phase_label=phase_label,
        coverage=CoverageRecord(unit_id=unit, attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))


def write_baseline_sheets(run_dir: Path,
                          sheets: Iterable[AnswerSheet],
                          phase_label: str = "p01:baseline") -> None:
    """Write ``sheets`` into the run's ``phases/<phase>`` directory."""
    from expedistill.composites.phase_bank import phase_bank_dir
    sheets = list(sheets)
    dest = phase_bank_dir(run_dir, phase_label)
    answer_sheets.write_answer_sheets(
        sheets, dest / "answer_sheets", render_markdown=False)
    answer_sheets.write_coverage(
        [sheet.coverage for sheet in sheets], dest / "coverage.jsonl")
    units = {}
    for sheet in sheets:
        execution = sheet.execution
        units.setdefault(execution.unit_id, UnitSpec(
            unit_id=execution.unit_id, phase_label=phase_label,
            task_ref=execution.task_ref, role=execution.role,
            arm=execution.arm, expected_attempts=0,
            injected_skills=None))
    phase_report.write_unit_specs(list(units.values()), dest)
