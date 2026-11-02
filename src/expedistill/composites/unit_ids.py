"""Unit and answer-sheet identity rules (layer 5, pure).

The unit id is the join key between a measurement specification, a trial's
coverage record and the unit-level summary.  It is deliberately not the
persisted trajectory id: the unit id names the measurement slot (phase,
benchmark, task/exam, role and canonical injected skills), while a trial id
names one execution of that slot.
"""

import hashlib
import json
import urllib.parse
from typing import Any

__all__ = [
    "canonical_skills",
    "make_gap_sheet_id",
    "make_unit_id",
    "phase_label_of_unit_id",
    "unit_id_for_trajectory",
]


def canonical_skills(skills_injected: Any) -> str:
    """The canonical text of a unit's ``skills_injected`` raw value.

    The three raw shapes stay distinguishable: ``None`` (no injection) is
    not ``[]`` (a retrieval that reached no threshold) is not a string
    (the whole-bank digest); a list (per-skill or whole-group references)
    is canonical JSON so member order and key order cannot change the id.
    """
    if skills_injected is None:
        return "none"
    if isinstance(skills_injected, str):
        return f"bank:{skills_injected}"
    return ("list:" + json.dumps(skills_injected, sort_keys=True,
                                 ensure_ascii=False,
                                 separators=(",", ":")))


def make_unit_id(*, phase_label: str, benchmark: str, task_ref: str,
                 role: str, skills_injected: Any) -> str:
    """A unit's id: phase, benchmark, task/exam, role and injection digest."""
    digest = hashlib.sha256(
        canonical_skills(skills_injected).encode("utf-8")).hexdigest()[:8]
    return f"{phase_label}:{benchmark}:{task_ref}:{role}:{digest}"


def make_gap_sheet_id(unit_id: str, attempt_index: Any) -> str:
    """The synthetic id of a missing/terminal trial's answer-sheet envelope.

    The id is also the envelope's file name (percent-encoded), so it has to
    fit the filesystem's 255-byte component limit. An exam unit id embeds
    the exam id, which embeds the benchmark's task id; ALFWorld's game paths
    push the encoded name past that limit. Short ids keep the readable form;
    a long one keeps a short head plus a digest of the full pair, so the id
    stays deterministic and unique (nothing parses the format).
    """
    candidate = f"gap:{unit_id}:{attempt_index}"
    encoded = urllib.parse.quote(candidate, safe="").encode("utf-8")
    if len(encoded) + len(".json") + 14 <= 255:  # + the atomic temp suffix
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
    return f"gap:{unit_id[:48]}:{attempt_index}:{digest}"


def phase_label_of_unit_id(unit_id: str) -> str:
    """The ``pNN:<name>`` phase label a unit id carries.

    Unit ids are ``phase_label:benchmark:task:role:sha8`` and the phase
    label is the only part with exactly one colon (the config rules ban
    colons in the local name), so the first two segments are the label.
    """
    parts = unit_id.split(":", 2)
    if (len(parts) < 3 or not parts[0].startswith("p")
            or not parts[0][1:].isdigit()):
        raise ValueError(f"malformed unit id {unit_id!r}")
    return f"{parts[0]}:{parts[1]}"


def unit_id_for_trajectory(*, phase_label: str, benchmark: str,
                           trajectory: Any) -> str:
    """The unit id a stored trajectory belongs to (delivery unit slot)."""
    return make_unit_id(
        phase_label=phase_label, benchmark=benchmark,
        task_ref=trajectory.task_id, role=trajectory.model_role,
        skills_injected=trajectory.skills_injected)
