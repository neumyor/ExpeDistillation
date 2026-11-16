"""Unit and answer-sheet identity rules (layer 5, pure).

The unit id is the join key between a measurement specification, a trial's
coverage record and the unit-level summary.  It is deliberately not the
persisted trajectory id: the unit id names the measurement slot (phase,
benchmark, task, role and canonical injected skills), while a trial id
names one execution of that slot.
"""

import hashlib
import urllib.parse
from typing import Any

__all__ = [
    "canonical_skills",
    "make_gap_sheet_id",
    "make_unit_id",
    "unit_id_for_trajectory",
]


def canonical_skills(skills_injected: Any) -> str:
    """The canonical text of a unit's ``skills_injected`` raw value:
    ``None`` (no injection) or a bank reference string, kept distinct."""
    if skills_injected is None:
        return "none"
    return f"bank:{skills_injected}"


def make_unit_id(*, phase_label: str, benchmark: str, task_ref: str,
                 role: str, skills_injected: Any) -> str:
    """A unit's id: phase, benchmark, task, role and injection digest."""
    digest = hashlib.sha256(
        canonical_skills(skills_injected).encode("utf-8")).hexdigest()[:8]
    return f"{phase_label}:{benchmark}:{task_ref}:{role}:{digest}"


def make_gap_sheet_id(unit_id: str, attempt_index: Any) -> str:
    """The synthetic id of a missing/terminal trial's answer-sheet envelope.

    The id is also the envelope's file name (percent-encoded), so it has to
    fit the filesystem's 255-byte component limit. A unit id embeds the
    benchmark's task id, and ALFWorld's game paths push the encoded name
    past that limit. Short ids keep the readable form;
    a long one keeps a short head plus a digest of the full pair, so the id
    stays deterministic and unique (nothing parses the format).
    """
    candidate = f"gap:{unit_id}:{attempt_index}"
    encoded = urllib.parse.quote(candidate, safe="").encode("utf-8")
    if len(encoded) + len(".json") + 14 <= 255:  # + the atomic temp suffix
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
    return f"gap:{unit_id[:48]}:{attempt_index}:{digest}"


def unit_id_for_trajectory(*, phase_label: str, benchmark: str,
                           trajectory: Any) -> str:
    """The unit id a stored trajectory belongs to (delivery unit slot)."""
    return make_unit_id(
        phase_label=phase_label, benchmark=benchmark,
        task_ref=trajectory.task_id, role=trajectory.model_role,
        skills_injected=trajectory.skills_injected)
