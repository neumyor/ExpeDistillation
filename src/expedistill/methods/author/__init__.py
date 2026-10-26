"""The author family: writing new skills and exam revisions.

``methods.author.write`` owns the P02 add writer (``write_new``) and the
legacy exam group writer (``revise_group``, used with answer-sheet
evidence). P04/P06 revisions live in ``methods.author.revise`` and read
only ``SkillAttemptEvidence``. The P02 add and exam paths keep the old
``EvidencePackage`` shape; the new writer does not.
"""

from dataclasses import dataclass

__all__ = ["AuthoringParams"]

@dataclass(frozen=True)
class AuthoringParams:
    """The ``distill_diagnose`` authoring method's (empty) parameters."""
