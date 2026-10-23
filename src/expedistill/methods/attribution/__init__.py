"""Attribution drivers: read one measured group, produce per-skill facts.

The driver never decides keep/revise/delete. It gathers the baseline and
live facts, the recorded usage facts and the sibling descriptions, asks
the attribution model one question for the whole group, and returns one
fact-only ``EvidencePackage`` per member for the shared author.
"""

from dataclasses import dataclass

__all__ = ["AttributionParams"]


@dataclass(frozen=True)
class AttributionParams:
    """The group attribution method's (empty) parameter set."""
