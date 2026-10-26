"""Post-hoc usage facts for a live folder round (layer 5, pure).

The trial-level AnswerSheet's ExecutionRecord carries the terminal session
and the delivery's folder manifest; this module turns them into one row per
attempt (``build_usage_rows_from_sheets``).  The legacy trajectory-shaped
``build_usage_rows`` entry remains for old callers/tests.  The row keeps the
hard facts only -- which skills the model read, which stayed unread, whether
the initial prompt carried the skill list -- and the scorer's verdict.
Application and relevance are judged later, in the learning phase's evidence
driver; acceptance remains numeric.

The initial prompt's ``<available_skills>`` block, when the stored
trajectory still carries it, is compared with the manifest.  TB's local
reconstruction starts at the first agent command, so the block is often
absent; a recorded read then proves the model could reach the folder,
and a run with neither the block nor a read is reported as
``unverified`` rather than as a delivery failure.
"""

import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from expedistill.composites.skill_use import skill_read_events
from expedistill.core.answer_sheet import AnswerSheet, ExecutionRecord
from expedistill.inject.skill_text import DEFAULT_DESCRIPTION_MODE
from expedistill.infra.store import write_jsonl

__all__ = [
    "SKILL_USAGE_NAME",
    "build_usage_rows",
    "build_usage_rows_from_sheets",
    "write_usage_rows",
]

#: The round-level usage ledger's file name.
SKILL_USAGE_NAME = "skill_usage.jsonl"

_AVAILABLE_RE = re.compile(
    r"<available_skills>(.*?)</available_skills>", re.DOTALL)
_NAME_RE = re.compile(r"<name>(.*?)</name>", re.DOTALL)


def _expected_names(folder: Mapping[str, Any]) -> List[str]:
    """The skill directory names the manifest ships, in manifest order."""
    names = []
    for entry in folder.get("files") or []:
        filename = entry.get("filename")
        if isinstance(filename, str) and filename:
            names.append(PurePosixPath(filename).parts[0])
    return names


def _steps(record: Any) -> List[Any]:
    return list(getattr(record, "steps", None) or [])


def _attempt(record: Any) -> int:
    value = getattr(record, "attempt_index", None)
    if value is None:
        value = getattr(record, "sample_index", 0)
    return int(value)


def _record_id(record: Any) -> str:
    value = getattr(record, "trajectory_id", None)
    if value is None:
        value = getattr(record, "sheet_id", "")
    return str(value)


def _correctness(record: Any) -> Optional[bool]:
    value = getattr(record, "correctness", None)
    if value is None:
        value = getattr(record, "raw_correctness", None)
    return value


def _available_names(record: Any) -> Optional[List[str]]:
    """The names in the initial prompt's <available_skills> block."""
    steps = _steps(record)
    if not steps:
        return None
    match = _AVAILABLE_RE.search(steps[0].action)
    if match is None:
        return None
    return [name.strip() for name in _NAME_RE.findall(match.group(1))]


def _delivery_status(folder: Mapping[str, Any],
                     available: Optional[List[str]],
                     read_ids: List[str]) -> str:
    expected = _expected_names(folder)
    if not expected:
        return "no_skills"
    if available is None:
        # The stored trajectory often starts at the first agent command
        # (TB's reconstruction drops the initial prompt). A read proves
        # the model could reach the folder; without one, the list cannot
        # be observed either way.
        return "ok" if read_ids else "unverified"
    if sorted(available) != sorted(expected):
        return "manifest_mismatch"
    return "ok"


def _first_agent_step(record: Any) -> Optional[int]:
    """The first step after the initial prompt that carries an action."""
    steps = _steps(record)
    for index, step in enumerate(steps):
        if index == 0:
            continue
        if step.action.strip():
            return index
    return None


def _outcome(record: Any) -> str:
    return {True: "success", False: "failure"}.get(
        _correctness(record), "unknown")


def build_usage_rows(*, task_id: str, round_: int,
                     executions: Optional[Iterable[Any]] = None,
                     trajectories: Optional[Iterable[Any]] = None,
                     folder: Mapping[str, Any]) -> List[dict]:
    """One usage row per stored attempt of one task.

    ``executions`` accepts the new trial-level ExecutionRecord (or an
    AnswerSheet's execution); ``trajectories`` is the legacy spelling and
    remains for old callers/tests.  The read extraction only needs the
    record's steps, attempt index, id and raw correctness.
    """
    records = executions if executions is not None else (trajectories or [])
    rows: List[dict] = []
    expected_ids = [entry["skill_id"] for entry in folder.get("files") or []]
    for record in records:
        available = _available_names(record)
        events = skill_read_events(record, folder)
        read_ids = [event["skill_id"] for event in events]
        read_set = set(read_ids)
        first_read = events[0]["step_index"] if events else None
        first_agent = _first_agent_step(record)
        rows.append({
            "schema": "skill-usage-v1",
            "task_id": task_id,
            "round": round_,
            "attempt": _attempt(record),
            "trajectory_id": _record_id(record),
            "delivery": "folder",
            "description_mode": folder.get(
                "description_mode", DEFAULT_DESCRIPTION_MODE),
            "available_list_present": available is not None,
            "available_names": available or [],
            "available_skill_ids": expected_ids,
            "delivery_status": _delivery_status(
                folder, available, read_ids),
            "reads": events,
            "read_skill_ids": read_ids,
            "unread_skill_ids": [
                entry["skill_id"] for entry in folder.get("files") or []
                if entry["skill_id"] not in read_set],
            "first_agent_step": first_agent,
            "first_read_step": first_read,
            "outcome": _outcome(record),
        })
    rows.sort(key=lambda row: (row["task_id"], row["attempt"]))
    return rows


def build_usage_rows_from_sheets(
        sheets: Sequence[AnswerSheet], *, round_: int,
        folder_by_task: Optional[Mapping[str, Mapping[str, Any]]] = None
        ) -> List[dict]:
    """Usage rows derived from the trial-level AnswerSheets themselves.

    The folder manifest is read from the execution record's artifacts
    (carried there by the live driver) or from ``folder_by_task`` for a
    legacy sheet; a sheet without either contributes no usage row.
    """
    by_task: Dict[str, List[ExecutionRecord]] = {}
    for sheet in sheets:
        execution = sheet.execution
        if execution is None or execution.kind != "trajectory":
            continue
        folder = execution.artifacts.get("skill_folder")
        if folder is None and folder_by_task is not None:
            folder = folder_by_task.get(execution.task_ref)
        if not folder:
            continue
        by_task.setdefault(execution.task_ref, []).append(execution)
    rows: List[dict] = []
    for task_id in sorted(by_task):
        executions = by_task[task_id]
        folder = executions[0].artifacts.get("skill_folder") or \
            (folder_by_task or {}).get(task_id)
        if not folder:
            continue
        rows.extend(build_usage_rows(
            task_id=task_id, round_=round_, executions=executions,
            folder=folder))
    return rows


def write_usage_rows(path: Any, rows: List[dict]) -> None:
    """Write the round's rows in deterministic order (atomic write)."""
    write_jsonl(path, sorted(rows, key=lambda row: (row["task_id"],
                                                    row["attempt"])))
