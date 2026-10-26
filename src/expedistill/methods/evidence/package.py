"""Compatibility re-export of the shared evidence-package shape.

The shape moved to ``composites/evidence_package.py`` so the
attribution and evidence families can both use it without importing
each other (the methods-family layering rule).
"""

from expedistill.composites.evidence_package import (  # noqa: F401
    ACTION_FACT_KINDS,
    EvidencePackage,
    ensure_fact_only,
)

__all__ = [
    "ACTION_FACT_KINDS",
    "EvidencePackage",
    "ensure_fact_only",
]
