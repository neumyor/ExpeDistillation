"""The review method family: decisions plus their evidence in one driver.

``review`` replaces the three former learn-phase slots (``decision``,
``attribution`` and ``evidence``).  The driver decides what each measured
skill needs and, only for the rows that need evidence, collects it.  The
authoring and curation families stay separate.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["DECISION_NAMES", "ReviewParams", "UNREAD_POLICIES"]

DECISION_NAMES = ("task_level", "skill_level")
UNREAD_POLICIES = ("delete", "keep")


@dataclass(frozen=True)
class ReviewParams:
    """One learn phase's review policy and evidence budgets.

    ``decision`` and ``unread_in_won_group`` are used by the live/exam
    paths; the baseline-gap add path reads only the evidence fields.
    """

    decision: str = "task_level"
    unread_in_won_group: str = "delete"
    before_steps: int = 10
    after_steps: int = 10
    K: int = 5
    retry_limit: int = 2
    write_lessons: bool = True

    def __post_init__(self) -> None:
        if self.decision not in DECISION_NAMES:
            raise ConfigError(
                "review method 'review': decision must be one of "
                f"{list(DECISION_NAMES)}, got {self.decision!r}")
        if self.unread_in_won_group not in UNREAD_POLICIES:
            raise ConfigError(
                "review method 'review': unread_in_won_group must be one "
                f"of {list(UNREAD_POLICIES)}, got "
                f"{self.unread_in_won_group!r}")
        if self.before_steps < 0 or self.after_steps < 0:
            raise ConfigError(
                "review method 'review': before_steps and after_steps "
                f"must be >= 0, got {self.before_steps!r}/"
                f"{self.after_steps!r}")
        if self.K < 1:
            raise ConfigError(
                "review method 'review': K must be >= 1, got "
                f"{self.K!r}")
        if self.retry_limit < 0:
            raise ConfigError(
                "review method 'review': retry_limit must be >= 0, got "
                f"{self.retry_limit!r}")
        if not isinstance(self.write_lessons, bool):
            raise ConfigError(
                "review method 'review': write_lessons must be a bool, got "
                f"{self.write_lessons!r}")
