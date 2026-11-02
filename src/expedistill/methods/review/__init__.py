"""The review method family: decisions plus their evidence in one driver.

``review`` replaces the three former learn-phase slots (``decision``,
``attribution`` and ``evidence``).  The driver decides what each measured
skill needs and, only for the rows that need evidence, collects it.  The
authoring and curation families stay separate.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError
from expedistill.core.skill_sections import REVISE_SCOPES

__all__ = [
    "DECISION_NAMES",
    "MIN_TRIAL_LEVEL_INPUT_TOKENS",
    "REVISE_SCOPES",
    "ReviewParams",
    "UNREAD_POLICIES",
]

DECISION_NAMES = ("task_level", "skill_level", "trial_level")
UNREAD_POLICIES = ("delete", "keep")

#: ``trial_level`` sends a whole failed trial (plus the whole skill bank)
#: to the author and all of that skill's window evidence to the rewriter.
#: Both roles therefore need a declared 512K input budget.
MIN_TRIAL_LEVEL_INPUT_TOKENS = 524_288


@dataclass(frozen=True)
class ReviewParams:
    """One learn phase's review policy and evidence budgets.

    ``trial_level`` selects the per-trial review path; the other two names
    keep the existing live/exam paths.  The baseline-gap add path reads
    only the evidence fields.
    """

    decision: str = "task_level"
    unread_in_won_group: str = "delete"
    before_steps: int = 10
    after_steps: int = 10
    K: int = 5
    retry_limit: int = 2
    write_lessons: bool = True
    #: Which part of a skill this phase's revisions may change: ``body``
    #: (``[Steps]`` and ``[Do not use when]``), ``trigger`` (the summary
    #: and ``[When to use]``, what the reader sees in the card list before
    #: opening a card) or ``both``, the unrestricted method.
    revise_scope: str = "both"
    #: Whether this phase may write new skills from aggregated cross-task
    #: requirements (``trial_level``). Off, the requirements are recorded
    #: but not written, so the library keeps exactly its current members.
    new_skills: bool = True
    #: Land the skills the P02 add path writes as ``accepted`` instead of
    #: ``pending``, for arms with no later check whose pending skills no
    #: phase would ever accept.
    accept_new: bool = False

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
        if self.revise_scope not in REVISE_SCOPES:
            raise ConfigError(
                "review method 'review': revise_scope must be one of "
                f"{list(REVISE_SCOPES)}, got {self.revise_scope!r}")
        for name in ("new_skills", "accept_new"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(
                    f"review method 'review': {name} must be a bool, got "
                    f"{getattr(self, name)!r}")
