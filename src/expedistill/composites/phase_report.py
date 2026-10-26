"""The execute phase's trial-level report envelope (layer 5).

The acceptance module produces answer sheets; this module turns them into
the phase's derived unit summaries.  It is not a persisted report card and
it is not called by grading.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from expedistill.composites import unit_summary
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    PhaseReport,
    UnitSpec,
    UnitSummary,
)
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import read_json, read_jsonl, write_jsonl

__all__ = [
    "ANSWER_SHEETS_DIR",
    "CARRIED_UNITS_NAME",
    "COVERAGE_NAME",
    "SPECS_NAME",
    "build_phase_report",
    "read_carried_units",
    "read_phase_report",
    "read_phase_units",
    "require_learning_report",
    "write_carried_units",
    "write_unit_specs",
]

SPECS_NAME = "unit_specs.jsonl"
COVERAGE_NAME = "coverage.jsonl"
ANSWER_SHEETS_DIR = "answer_sheets"
#: Reused units' previous UnitSummary rows, carried under the current
#: phase's unit ids. Not a measurement of this phase: it keeps rule 1
#: from hiding a unit the next learn phase still has to decide.
CARRIED_UNITS_NAME = "carried_units.jsonl"


def build_phase_report(
        sheets: Sequence[AnswerSheet], specs: Sequence[UnitSpec], *,
        phase_kind: str, learning_usable: bool,
        summary: Optional[Dict[str, Any]] = None,
        external: Any = None) -> PhaseReport:
    """Derive unit summaries from trial sheets and build the envelope."""
    units = unit_summary.summarize_phase(sheets, specs)
    return PhaseReport(
        phase_kind=phase_kind,
        learning_usable=learning_usable,
        sheets=list(sheets),
        units=units,
        summary=dict(summary or {}),
        external=external,
    )


def require_learning_report(report: PhaseReport) -> PhaseReport:
    """Refuse a phase report that must not flow into decision (test)."""
    if not report.learning_usable:
        raise ValueError(
            f"phase report {report.phase_kind!r} is not learning-usable; "
            "final-test results never reach decision")
    return report


def write_unit_specs(specs: Sequence[UnitSpec], phase_dir: Path) -> None:
    """Persist the unit specs beside the answer sheets.

    The specs are the expected-unit universe the pure summarizer needs; they
    are not a report card and carry no counts.
    """
    write_jsonl(Path(phase_dir) / SPECS_NAME,
                [spec.to_dict() for spec in specs])


def write_carried_units(units: Sequence[UnitSummary],
                        phase_dir: Path) -> None:
    """Persist rule-1 reused units' previous summaries under the new ids.

    These are not this phase's measurements and never enter the trial
    sheets; they exist so the next learn phase can still decide a reused
    unit (rule 1 must not hide work that is still owed).
    """
    write_jsonl(Path(phase_dir) / CARRIED_UNITS_NAME,
                [unit.to_dict() for unit in units])


def read_carried_units(phase_dir: Path) -> List[UnitSummary]:
    """The reused units an execute phase carried forward, if any."""
    path = Path(phase_dir) / CARRIED_UNITS_NAME
    if not path.exists():
        return []
    return [UnitSummary.from_dict(row) for row in read_jsonl(path)]


def read_phase_units(phase_dir: Path) -> List[UnitSummary]:
    """Every unit summary a phase can hand to decision.

    The measured units come from the normal phase report; reused units
    come from ``carried_units.jsonl`` (they have no sheets of their own
    in this phase). A missing measured report with carried rows is an
    inconsistent directory and raises.
    """
    phase_dir = Path(phase_dir)
    carried = read_carried_units(phase_dir)
    report = read_phase_report(
        phase_dir, phase_kind="carried", learning_usable=True)
    if report is None:
        if carried:
            raise CodeBugError(
                f"phase directory {phase_dir} carries "
                f"{CARRIED_UNITS_NAME} but no trial-level report")
        return []
    return carried + list(report.units)


def read_phase_report(phase_dir: Path, *, phase_kind: str,
                      learning_usable: bool,
                      summary: Optional[Dict[str, Any]] = None
                      ) -> Optional[PhaseReport]:
    """Read a completed execute phase's trial-level artifacts.

    Returns None when the phase directory carries no trial-level
    artifacts; callers treat that as an interrupted or incompatible run,
    never as an older report shape to guess around.
    """
    phase_dir = Path(phase_dir)
    specs_path = phase_dir / SPECS_NAME
    coverage_path = phase_dir / COVERAGE_NAME
    sheets_dir = phase_dir / ANSWER_SHEETS_DIR
    if not (specs_path.exists() and coverage_path.exists()
            and sheets_dir.exists()):
        return None
    specs = [UnitSpec.from_dict(row) for row in read_jsonl(specs_path)]
    sheets: List[AnswerSheet] = []
    for path in sorted(sheets_dir.glob("*.json")):
        sheets.append(AnswerSheet.from_dict(read_json(path)))
    coverage = [CoverageRecord.from_dict(row)
                for row in read_jsonl(coverage_path)]
    sheet_keys = {(sheet.coverage.unit_id, sheet.coverage.attempt_index)
                  for sheet in sheets}
    missing = [key for key in
               {(record.unit_id, record.attempt_index)
                for record in coverage}
               if key not in sheet_keys]
    if missing:
        raise CodeBugError(
            "coverage records without a corresponding answer sheet: "
            f"{sorted(missing)[:10]}"
            f"{'...' if len(missing) > 10 else ''}")
    report = build_phase_report(
        sheets, specs, phase_kind=phase_kind,
        learning_usable=learning_usable, summary=summary)
    return report
