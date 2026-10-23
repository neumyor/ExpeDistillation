"""The learn decision family: parameters for the P04/P06 strategies."""

from dataclasses import dataclass

__all__ = ["TaskLevelParams", "SkillLevelParams"]


@dataclass(frozen=True)
class TaskLevelParams:
    """The task-level strategy's empty parameter set."""


@dataclass(frozen=True)
class SkillLevelParams:
    """The skill-level strategy's empty parameter set."""
