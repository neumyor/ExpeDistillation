"""The accepted-infra-shortfall record: one file name, one shape.

``{"unit_ids": [...]}`` -- the durable "these holes are accepted"
decision, written before any judgment reads the shrunken sample and
honoured by every later call with or without the flag (AGENTS.md
section 6). Stage 1 keeps it per run, the live rounds keep it per round;
the read/write pair was pasted in three places with identical bodies, so
it lives here once.

Stage 6's accepted record is a DIFFERENT shape (per-arm id lists) and
stays in its stage module on purpose.
"""

from pathlib import Path
from typing import Collection, List

from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import read_json, write_json

__all__ = ["SHORTFALL_NAME", "read_unit_ids", "write_unit_ids"]

#: The record's file name wherever it lives (stage 1 dir, round dir).
SHORTFALL_NAME = "accepted_infra_shortfall.json"


def read_unit_ids(path: Path) -> List[str]:
    """The record's accepted unit ids (sorted; empty when it never existed).

    Fail loud on an unreadable or malformed record: it is a per-run
    decision every skip set and completeness test reads back, so a
    corrupt one must stop the run, never silently widen the sample.
    """
    path = Path(path)
    if not path.exists():
        return []
    try:
        unit_ids = read_json(path)["unit_ids"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CodeBugError(
            f"unreadable accepted-shortfall record {path}: {exc}") from None
    if not isinstance(unit_ids, list) or not all(
            isinstance(unit_id, str) for unit_id in unit_ids):
        raise CodeBugError(
            f"invalid accepted-shortfall record {path}: {unit_ids!r}")
    return sorted(unit_ids)


def write_unit_ids(path: Path, unit_ids: Collection[str]) -> None:
    """Persist the accepted unit-id set (atomic write, sorted)."""
    write_json(Path(path), {"unit_ids": sorted(set(unit_ids))}, indent=2)
