"""The learn decision family: parameters for the P04/P06 strategies."""

from dataclasses import dataclass

__all__ = ["TaskLevelParams", "SkillLevelParams"]


@dataclass(frozen=True)
class TaskLevelParams:
    """The task-level strategy's empty parameter set."""


@dataclass(frozen=True)
class SkillLevelParams:
    """The skill-level strategy's parameters.

    ``unread_in_won_group`` decides the 32-skill ambiguity: a skill that
    was never read but whose live group won (P03 accepted it as part of
    the group). ``delete`` follows "never triggered -> delete"; ``keep``
    follows "the group won, trust its whole membership".
    """

    unread_in_won_group: str = "delete"

    def __post_init__(self) -> None:
        if self.unread_in_won_group not in ("delete", "keep"):
            raise ValueError(
                "unread_in_won_group must be 'delete' or 'keep', got "
                f"{self.unread_in_won_group!r}")
