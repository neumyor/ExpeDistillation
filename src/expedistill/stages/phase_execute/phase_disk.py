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

from expedistill.composites import decision
from expedistill.composites import phase_bank
from expedistill.composites import phase_report
from expedistill.core import vocabulary
from expedistill.core.config import PhaseInstance
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import read_json, read_jsonl, write_jsonl
from expedistill.stages.context import StageContext

__all__ = [
    "GENERATION_FAILURES_NAME",
    "PHASE_RECORD_NAME",
    "REDISPATCH_NAME",
    "REUSE_INDEX_NAME",
    "SUMMARY_NAME",
    "UNITS_NAME",
    "_earlier_pass_unit_ids",
    "_earlier_pass_units",
    "_write_carried_units",
    "_write_reuse_index",
]

#: The execute-phase layout inside ``<run>/phases/<label>/`` (the
#: package docstring). The learn-phase artifacts beside them are
#: phase_bank's.
PHASE_RECORD_NAME = "phase.json"
UNITS_NAME = "units.jsonl"
REUSE_INDEX_NAME = "reuse_index.jsonl"
REDISPATCH_NAME = "redispatch.json"
SUMMARY_NAME = "phase_summary.json"
GENERATION_FAILURES_NAME = "generation_failures.jsonl"

#: exam phase directory's name: ``pNN_<name>`` (phase_bank's colon mapping).
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


def _bank_before_previous(ctx: StageContext,
                          phase: PhaseInstance) -> List[Skill]:
    """The bank artifact BEFORE the previous learn phase's artifact.

    The exam selector compares the latest learn artifact with the one
    before it to find newly created and version-increased skills.  With no
    earlier artifact (the first learn phase produced the only one), the
    pre-learn run-level bank stands in; a from-scratch run has none, which
    makes every member of the latest artifact "new".
    """
    phases_dir = Path(ctx.run_dir) / phase_bank.PHASES_DIR
    if not phases_dir.exists():
        return [Skill.from_dict(row) for row in ctx.store.bank()]
    found = []
    for entry in phases_dir.iterdir():
        match = _PHASE_DIR_RE.fullmatch(entry.name)
        if match is None or int(match.group(1)) - 1 >= phase.index:
            continue
        if (entry / phase_bank.BANK_NAME).exists():
            found.append((int(match.group(1)),
                          f"p{int(match.group(1)):02d}:{match.group(2)}"))
    if len(found) < 2:
        return [Skill.from_dict(row) for row in ctx.store.bank()]
    label = sorted(found)[-2][1]
    return [Skill.from_dict(row)
            for row in phase_bank.load_phase_bank(ctx.run_dir, label)]


def _write_units(ctx: StageContext, phase: PhaseInstance,
                 units: Sequence[ExecutionUnitRow]) -> None:
    """Persist the resolved unit list (one resolution per phase; the
    rewrite never grows a second copy of a row). Atomic -- temp file +
    rename (B3's pattern), so a crash mid-write leaves the previous file
    intact instead of a truncated one."""
    write_jsonl(_phase_dir(ctx, phase) / UNITS_NAME,
                [asdict(unit) for unit in units])


def _write_reuse_index(ctx: StageContext, phase: PhaseInstance,
                       rows: Sequence[Tuple[str, str, str]]) -> None:
    """Persist the execution-side rule-1 reuse index.

    This is deliberately NOT a unit report card: it is a small
    ``{unit_id, status, cause}`` cache written by the execute driver so a
    later phase can answer "is this unchanged unit's result reusable?"
    without re-reading the trial sheets.  Acceptance itself only produces
    trial-level answer sheets.
    """
    write_jsonl(_phase_dir(ctx, phase) / REUSE_INDEX_NAME,
                [{"unit_id": unit_id, "status": status, "cause": cause}
                 for unit_id, status, cause in rows])


def _write_carried_units(ctx: StageContext, phase: PhaseInstance,
                         reused: Dict[str, tuple]) -> None:
    """Persist the reused units' previous summaries under the new ids.

    Rule 1 deliberately does not re-measure the unit, but the next learn
    phase still has to decide it (a failed revision must be retried).
    The summary travels in ``carried_units.jsonl``; the trial sheets stay
    exactly this phase's measurements.
    """
    carried = []
    for new_id, (status, cause, summary) in sorted(reused.items()):
        if (summary.status, summary.cause) != (status, cause):
            raise CodeBugError(
                f"reuse row {new_id!r} says {(status, cause)!r} but the "
                f"source summary says "
                f"{(summary.status, summary.cause)!r}: the reuse index "
                "and the carried summary disagree")
        artifacts = dict(summary.artifacts or {})
        # Keep the ORIGINAL measured unit, not a chain of carries.
        artifacts["reused_from"] = artifacts.get(
            "reused_from", summary.unit_id)
        carried.append(replace(
            summary, unit_id=new_id, phase_label=phase.label,
            sheet_ids=[], artifacts=artifacts))
    phase_dir = _phase_dir(ctx, phase)
    if carried:
        phase_report.write_carried_units(carried, phase_dir)
    else:
        # No reused unit: leave no empty artifact behind. A resumed pass
        # that now carries nothing must remove an earlier pass's file.
        stale = phase_dir / phase_report.CARRIED_UNITS_NAME
        if stale.exists():
            stale.unlink()


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


def _reusable_units(ctx: StageContext, phase: PhaseInstance,
                    new_units: Sequence[ExecutionUnitRow]
                    ) -> Dict[str, tuple]:
    """Rule 1's reuse map: new unit id -> (status, cause, summary).

    A new unit reuses an earlier execute unit's result when task, role
    and the rendered injection bytes match (:func:`decision.reusable_units`).
    The status/cause come from the earlier phase's
    ``reuse_index.jsonl``; the full summary (counts included) comes from
    its measured report or its own carried units, so a later learn phase
    can still decide the reused unit. A ``zero_execution`` verdict is not
    a measurement and is never reused. Most recent phase wins.
    """
    reused: Dict[str, tuple] = {}
    for record in _earlier_phases(ctx, phase):
        if record.get("role") == "test":
            # The final test never uses rule 1, and its trial sheets must
            # never seed a later execute phase's reuse map.
            continue
        directory = phase_bank.phase_bank_dir(ctx.run_dir,
                                              record["label"])
        units_path = directory / UNITS_NAME
        index_path = directory / REUSE_INDEX_NAME
        if not units_path.exists() or not index_path.exists():
            continue
        matches = decision.reusable_units(
            new_units, [ExecutionUnitRow(**row)
                        for row in read_jsonl(units_path)])
        if not matches:
            continue
        stored = {row["unit_id"]: (row.get("status", ""),
                                   row.get("cause", ""))
                  for row in read_jsonl(index_path)}
        summaries = {unit.unit_id: unit
                     for unit in phase_report.read_phase_units(directory)}
        for new_id, previous_id in matches.items():
            if new_id in reused:
                continue  # a more recent phase already answered this unit
            verdict = stored.get(previous_id)
            if verdict is None:
                raise CodeBugError(
                    f"phase {record['label']!r} has a unit {previous_id!r} "
                    f"but no reuse row for it: {index_path} is "
                    "internally inconsistent and a human must look at "
                    "this run")
            status, cause = verdict
            if (status == vocabulary.STATUS_NOT_TESTABLE
                    and cause == vocabulary.CAUSE_ZERO_EXECUTION):
                continue
            summary = summaries.get(previous_id)
            if summary is None:
                raise CodeBugError(
                    f"phase {record['label']!r} has a reuse row for "
                    f"{previous_id!r} but no summary to carry: the "
                    "reused unit would be invisible to the next learn "
                    "phase")
            if (summary.status, summary.cause) != (status, cause):
                raise CodeBugError(
                    f"phase {record['label']!r} reuse row {previous_id!r} "
                    f"says {(status, cause)!r} but its summary says "
                    f"{(summary.status, summary.cause)!r}: the phase "
                    "directory disagrees with itself")
            reused[new_id] = (status, cause, summary)
    return reused


