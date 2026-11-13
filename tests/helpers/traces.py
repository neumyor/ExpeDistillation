"""Answer sheets of real executions for provenance and learn-step tests.

Writers record exact ``TraceRef`` locations and refuse a pointer that does
not resolve, so a test that lands a skill needs the sheets its evidence
points at. :func:`execution_sheet` builds one the way the baseline and
practice phases do (sheet id ``<unit>::<trajectory id>``, one sheet per
attempt, the trajectory id and, for a practice trial, the delivered skill
folder in the artifacts); :func:`write_sheets` writes sheets into a phase
directory.
"""

from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
)
from expedistill.core.ids import make_trajectory_id
from expedistill.core.models import Step
from expedistill.infra.store import object_filename, write_json

__all__ = ["execution_sheet", "write_sheets"]


def execution_sheet(task_id: str, role: str, attempt: int, *,
                    actions: Optional[Sequence[str]] = None,
                    n_steps: int = 6, benchmark: str = "tb",
                    phase_label: str = "p01:baseline",
                    correct: Optional[bool] = False,
                    skill_folder: Optional[Dict[str, Any]] = None
                    ) -> AnswerSheet:
    """One graded answer sheet of ``task_id``'s ``attempt`` by ``role``;
    its steps are ``actions`` (default ``n_steps`` generic ones)."""
    trajectory = make_trajectory_id(phase_label, task_id, role, attempt)
    unit = make_unit_id(phase_label=phase_label, benchmark=benchmark,
                        task_ref=task_id, role=role, skills_injected=None)
    sheet_id = f"{unit}::{trajectory}"
    if actions is None:
        actions = [f"{role}{attempt}-a{i}" for i in range(n_steps)]
    artifacts: Dict[str, Any] = {"trajectory_id": trajectory}
    if skill_folder is not None:
        artifacts["skill_folder"] = skill_folder
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=unit, phase_label=phase_label,
        task_ref=task_id, role=role, arm=role, model_role=role,
        model_name="m", sample_index=attempt,
        steps=[Step(action=action, observation=f"after {action}")
               for action in actions],
        artifacts=artifacts, raw_correctness=correct)
    verdict = "success" if correct else "failure"
    return AnswerSheet(
        sheet_id=sheet_id, phase_label=phase_label,
        coverage=CoverageRecord(unit_id=unit, attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict=verdict, basis="b", rule_name="r"))


def write_sheets(run_dir: Path, sheets: Iterable[AnswerSheet],
                 phase_label: str = "p01:baseline") -> None:
    """Write ``sheets`` into the run's ``phases/<phase>/answer_sheets``."""
    directory = phase_bank_dir(run_dir, phase_label) / "answer_sheets"
    for sheet in sheets:
        write_json(directory / object_filename(sheet.sheet_id),
                   sheet.to_dict(), indent=2)
