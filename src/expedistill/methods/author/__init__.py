"""The author family's contract: writing and revising skills.

The author family's one method (``distill_diagnose``, plan section 3's
registry key set) is the new-skill writing (G2) plus the revision
writing the report kind dispatches (G6 for exam reports, G11 for live).
``AuthoringParams`` moved here from the authoring slot package in M3
(the registry's new ``(author, distill_diagnose)`` entry resolves it);
the slot package re-exports it so the v3 config path is byte-identical
until the slot dies in M4. The ``exams`` switch stays for the v3
ablation pair -- M4 removes it with the call-point move. Driver-less
until M4: the phase wiring lands with the config schema.
"""

from dataclasses import dataclass

__all__ = ["AuthoringParams"]


@dataclass(frozen=True)
class AuthoringParams:
    """Whether the authoring stage generates exam questions."""

    exams: bool = True
