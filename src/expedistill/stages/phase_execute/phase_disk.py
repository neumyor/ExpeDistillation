"""What is on disk: the phase directory, earlier phases, the bank.

Every helper here derives a fact from the run directory alone (the
phase's own directory, the earlier phases' records, the previous learn
phase's bank artifact) or persists the phase's units / report. They are
shared by more than one role, which is why they live in their own
module: the roles (baseline / exam / live / final_test) and the engine
consume them, none owns them.
"""

from dataclasses import asdict, replace
from pathlib import Path
import re
from typing import Any, Dict, List, Sequence, Tuple

from expedistill.composites import decision, verdicts
from expedistill.composites import phase_bank
from expedistill.core.config import PhaseInstance
from expedistill.core.contracts import (
    AcceptanceReportRow,
    ExecutionUnitRow,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import read_json, read_jsonl, write_jsonl
from expedistill.stages.context import StageContext

__all__ = [
    "GENERATION_FAILURES_NAME",
    "PHASE_RECORD_NAME",
    "REDISPATCH_NAME",
    "REPORT_NAME",
    "SUMMARY_NAME",
    "UNITS_NAME",
    "_earlier_pass_unit_ids",
    "_earlier_pass_units",
]

#: The execute-phase layout inside ``<run>/phases/<label>/`` (the
#: package docstring). The learn-phase artifacts beside them are
#: phase_bank's.
PHASE_RECORD_NAME = "phase.json"
UNITS_NAME = "units.jsonl"
REPORT_NAME = "acceptance_report.jsonl"
REDISPATCH_NAME = "redispatch.json"
SUMMARY_NAME = "phase_summary.json"
GENERATION_FAILURES_NAME = "generation_failures.jsonl"

#: A phase directory's name: ``pNN_<name>`` (phase_bank's colon mapping).
_PHASE_DIR_RE = re.compile(r"p([0-9]+)_(.+)")


def _phase_dir(ctx: StageContext, phase: PhaseInstance,
               arm: str = "") -> Path:
    """The phase's directory (phase_bank's validated label mapping)."""
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    return directory / arm if arm else directory


def _arm_dir(ctx: StageContext, phase: PhaseInstance, arm: str) -> Path:
    """An arm's directory (store + ledgers), created on first use."""
    directory = _phase_dir(ctx, phase, arm)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _earlier_phases(ctx: StageContext, phase: PhaseInstance
                    ) -> List[Dict[str, Any]]:
    """The earlier phases' ``phase.json`` records, newest first.
    Derived from the phases directory alone: which phases ran is a disk
    fact, and PhaseInstance names only this phase (the frozen hand-off
    contract carries no sequence)."""
    phases_dir = Path(ctx.run_dir) / phase_bank.PHASES_DIR
    if not phases_dir.exists():
        return []
    records: List[Tuple[int, Dict[str, Any]]] = []
    for entry in phases_dir.iterdir():
        record_path = entry / PHASE_RECORD_NAME
        if not record_path.exists():
            continue
        try:
            record = read_json(record_path)
        except (OSError, ValueError) as exc:
            raise CodeBugError(
                f"unreadable phase record {record_path}: {exc}") from None
        if not isinstance(record.get("index"), int) \
                or record["index"] >= phase.index:
            continue
        records.append((record["index"], record))
    return [record for _, record in sorted(records, key=lambda p: -p[0])]


def _bank_skills(ctx: StageContext, phase: PhaseInstance) -> List[Skill]:
    """The bank delivery receives: the previous learn phase's artifact
    (R7's cross-phase load), else the run-level frozen bank (before the
    first learn phase there is no artifact to load). The artifact's
    PRESENCE is the signal -- a learn phase ends with one by contract,
    whatever else it writes beside it."""
    phases_dir = Path(ctx.run_dir) / phase_bank.PHASES_DIR
    if phases_dir.exists():
        found = []
        for entry in phases_dir.iterdir():
            match = _PHASE_DIR_RE.fullmatch(entry.name)
            if match is None or int(match.group(1)) - 1 >= phase.index:
                continue
            if (entry / phase_bank.BANK_NAME).exists():
                found.append((int(match.group(1)),
                               f"p{int(match.group(1)):02d}:{match.group(2)}"))
        if found:
            label = max(found)[1]
            return [Skill.from_dict(row)
                    for row in phase_bank.load_phase_bank(ctx.run_dir,
                                                          label)]
    return [Skill.from_dict(row) for row in ctx.store.bank()]


def _write_units(ctx: StageContext, phase: PhaseInstance,
                 units: Sequence[ExecutionUnitRow]) -> None:
    """Persist the resolved unit list (one resolution per phase; the
    rewrite never grows a second copy of a row). Atomic -- temp file +
    rename (B3's pattern), so a crash mid-write leaves the previous file
    intact instead of a truncated one."""
    write_jsonl(_phase_dir(ctx, phase) / UNITS_NAME,
                [asdict(unit) for unit in units])


def _write_report(ctx: StageContext, phase: PhaseInstance,
                  rows: Sequence[AcceptanceReportRow]) -> None:
    """Persist the phase's acceptance report (one JSON row per unit),
    atomically (temp file + rename, B3's pattern)."""
    write_jsonl(_phase_dir(ctx, phase) / REPORT_NAME,
                [asdict(row) for row in rows])


def _earlier_pass_unit_ids(ctx: StageContext, phase: PhaseInstance
                           ) -> set:
    """The unit ids THIS phase instance resolved in an earlier pass.

    The replay marker of the phase's own resume rules (the exam role's
    judged-answer reuse): ``units.jsonl`` is rewritten at the start of
    every pass, so its pre-overwrite content is the only durable record
    that this phase already planned these units -- and since a unit id
    carries the phase label, no other phase can have written them. The
    caller must read this BEFORE :func:`_write_units` replaces the file.

    The marker scopes a LOOKUP, never skips work: a marked unit whose
    measurement is not on disk yet (the earlier pass crashed before it)
    runs exactly like an unmarked one.
    """
    return {row.unit_id for row in _earlier_pass_units(ctx, phase)}


def _earlier_pass_units(ctx: StageContext, phase: PhaseInstance
                        ) -> List[ExecutionUnitRow]:
    """The FULL unit rows an earlier pass of this phase resolved.

    The live role's universe (the store's pending groups) SHRINKS as its
    own earlier pass commits verdicts, so a re-entered live phase would
    re-plan fewer units than the pass that crashed -- and rewriting
    ``units.jsonl`` with only the new plans would erase the earlier
    pass's rows from the phase's record (and desync the summary from
    the report, which the audit's units-report check then flags). The
    re-entered pass therefore MERGES the earlier rows back in: the
    phase's unit list is every unit ANY of its passes resolved, and the
    resume lands on the uninterrupted run's bytes.
    """
    path = _phase_dir(ctx, phase) / UNITS_NAME
    if not path.exists():
        return []
    return [ExecutionUnitRow(**row) for row in read_jsonl(path)]


def _reused_rows(ctx: StageContext, phase: PhaseInstance,
                 new_units: Sequence[ExecutionUnitRow]
                 ) -> Dict[str, AcceptanceReportRow]:
    """Rule 1's reuse map: for each new unit that matches an earlier
    execute phase's unit (same task, same role, same rendered block --
    :func:`decision.reuse_key`), the previous row it reuses, keyed by
    the new unit id. Most recent phase wins; a ``zero_execution`` row is
    not an acceptance result and is never reused (its work is still
    owed -- the earlier phase resumes it, the later one does not
    inherit a nothing)."""
    reused: Dict[str, AcceptanceReportRow] = {}
    for record in _earlier_phases(ctx, phase):
        directory = phase_bank.phase_bank_dir(ctx.run_dir,
                                              record["label"])
        units_path = directory / UNITS_NAME
        report_path = directory / REPORT_NAME
        if not units_path.exists() or not report_path.exists():
            continue
        matches = decision.reusable_units(
            new_units, [ExecutionUnitRow(**row)
                        for row in read_jsonl(units_path)])
        if not matches:
            continue
        rows = {row.unit_id: row for row in (
            AcceptanceReportRow(**entry)
            for entry in read_jsonl(report_path))}
        for new_id, previous_id in matches.items():
            if new_id in reused:
                continue  # a more recent phase already answered this unit
            row = rows.get(previous_id)
            if row is None:
                raise CodeBugError(
                    f"phase {record['label']!r} has a unit {previous_id!r} "
                    "but no report row for it: the phase directory is "
                    "internally inconsistent and a human must look at "
                    "this run")
            if (row.status == verdicts.STATUS_NOT_TESTABLE
                    and row.cause == verdicts.CAUSE_ZERO_EXECUTION):
                continue
            reused[new_id] = row
    return reused


def _rebased_row(row: AcceptanceReportRow, *, unit_id: str,
                 phase_label: str) -> AcceptanceReportRow:
    """A reused unit's row under its new unit id (rule 1: the verdicts
    carry over unchanged; only the phase identity is new)."""
    return replace(row, unit_id=unit_id, phase_label=phase_label)
