"""Post-hoc usage facts for a live folder round (layer 5, pure).

The live execution records the whole terminal session; this module turns
that record plus the delivery's folder manifest into one row per attempt.
The row keeps the hard facts only -- which skills the model read, which
stayed unread, whether the initial prompt carried the skill list -- and
the scorer's verdict.  Application and relevance are judged later, in the
learning phase's evidence driver; acceptance remains numeric.

The initial prompt's ``<available_skills>`` block, when the stored
trajectory still carries it, is compared with the manifest.  TB's local
reconstruction starts at the first agent command, so the block is often
absent; a recorded read then proves the model could reach the folder,
and a run with neither the block nor a read is reported as
``unverified`` rather than as a delivery failure.
"""

import re
from pathlib import PurePosixPath
from typing import Any, Iterable, List, Mapping, Optional

from expedistill.composites.skill_use import skill_read_events
from expedistill.core.models import Trajectory
from expedistill.inject.skill_text import DEFAULT_DESCRIPTION_MODE
from expedistill.infra.store import write_jsonl

__all__ = ["SKILL_USAGE_NAME", "build_usage_rows", "write_usage_rows"]

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


def _available_names(trajectory: Trajectory) -> Optional[List[str]]:
    """The names in the initial prompt's <available_skills> block.

    ``None`` means the block is absent; a list may still be empty.
    """
    if not trajectory.steps:
        return None
    match = _AVAILABLE_RE.search(trajectory.steps[0].action)
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


def _first_agent_step(trajectory: Trajectory) -> Optional[int]:
    """The first step after the initial prompt that carries an action."""
    for index, step in enumerate(trajectory.steps):
        if index == 0:
            continue
        if step.action.strip():
            return index
    return None


def _outcome(trajectory: Trajectory) -> str:
    return {True: "success", False: "failure"}.get(
        trajectory.correctness, "unknown")


def build_usage_rows(*, task_id: str, round_: int,
                     trajectories: Iterable[Trajectory],
                     folder: Mapping[str, Any]) -> List[dict]:
    """One usage row per stored attempt of one task."""
    rows: List[dict] = []
    expected_ids = [entry["skill_id"] for entry in folder.get("files") or []]
    for trajectory in trajectories:
        available = _available_names(trajectory)
        events = skill_read_events(trajectory, folder)
        read_ids = [event["skill_id"] for event in events]
        read_set = set(read_ids)
        first_read = events[0]["step_index"] if events else None
        first_agent = _first_agent_step(trajectory)
        rows.append({
            "schema": "skill-usage-v1",
            "task_id": task_id,
            "round": round_,
            "attempt": trajectory.attempt_index,
            "trajectory_id": trajectory.trajectory_id,
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
            "outcome": _outcome(trajectory),
        })
    rows.sort(key=lambda row: (row["task_id"], row["attempt"]))
    return rows


def write_usage_rows(path: Any, rows: List[dict]) -> None:
    """Write the round's rows in deterministic order (atomic write)."""
    write_jsonl(path, sorted(rows, key=lambda row: (row["task_id"],
                                                    row["attempt"])))
