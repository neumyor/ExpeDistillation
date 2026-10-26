"""Attribution drivers: turn real attempt windows into per-skill facts.

The driver is decision-agnostic: it receives the skills to revise and
returns ``SkillAttemptEvidence``. It never decides keep/revise/delete.
"""

from dataclasses import dataclass

__all__ = ["AttributionParams"]


@dataclass(frozen=True)
class AttributionParams:
    """Window sizes for per-attempt attribution."""

    before_steps: int = 10
    after_steps: int = 10

    def __post_init__(self) -> None:
        if self.before_steps < 0 or self.after_steps < 0:
            raise ValueError(
                "attribution window sizes must be >= 0, got "
                f"{self.before_steps!r}/{self.after_steps!r}")
