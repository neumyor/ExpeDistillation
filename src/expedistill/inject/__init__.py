"""Layer 1: D9 skill injection and D10 bank-length accounting (pure functions).

The single source of "skills go into the prompt": exam answering (G4) and real
execution (E1) both render the block here, so acceptance-time and test-time
injection are the same code path by construction. No disk IO, no model calls.
"""

from expedistill.inject.skills import (
    bank_reference,
    count_bank_tokens,
    estimate_tokens,
    format_skills_block,
    skill_ref,
)

__all__ = [
    "bank_reference",
    "count_bank_tokens",
    "estimate_tokens",
    "format_skills_block",
    "skill_ref",
]
