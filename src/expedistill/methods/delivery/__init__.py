"""Delivery drivers: how an arm's skill injection is built.

Params for every method live here (light, never a driver import) so
stage 0 can validate them before anything freezes.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    DESCRIPTION_MODES,
)

__all__ = ["FolderParams", "GroupByTaskParams", "NoneParams",
           "PerSkillParams", "RetrievalParams", "WholeBankParams"]


@dataclass(frozen=True)
class WholeBankParams:
    """No parameters: the whole bank, one fixed injection."""


@dataclass(frozen=True)
class FolderParams:
    """The folder delivery's parameters.

    ``description_mode`` chooses what the per-skill ``SKILL.md`` YAML
    ``description`` (and the catalog's one-line column) is built from:

    - ``summary``: the skill's WHAT sentence;
    - ``when_to_use``: the ``[When to use]`` trigger;
    - ``summary_then_when_to_use``: both, joined by ``Use when:``.

    The folder layout is fixed: one ``SKILL.md`` per skill directory.
    Harbor's Terminus discovers those files itself and renders the
    ``<available_skills>`` list, so the catalog file is a fallback, not
    the agent's primary directory.
    """

    description_mode: str = DEFAULT_DESCRIPTION_MODE

    def __post_init__(self) -> None:
        if self.description_mode not in DESCRIPTION_MODES:
            raise ConfigError(
                "methods.delivery.folder description_mode must be one of "
                f"{DESCRIPTION_MODES}, got {self.description_mode!r}")


@dataclass(frozen=True)
class NoneParams:
    """No parameters: no skills, the bare injection."""


@dataclass(frozen=True)
class PerSkillParams:
    """No parameters: one skill, one single-skill block."""


@dataclass(frozen=True)
class GroupByTaskParams:
    """No parameters: the pending group of the task being injected."""


@dataclass(frozen=True)
class RetrievalParams:
    """Per-task selection: category routing width, top-k, top-1 gate."""

    top_k: int
    max_categories: int
    min_score: float

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ConfigError(
                f"methods.delivery.retrieval top_k must be >= 1, got {self.top_k}")
        if self.max_categories < 1:
            raise ConfigError(
                "methods.delivery.retrieval max_categories must be >= 1, "
                f"got {self.max_categories}")
        # NaN and inf fail the range comparison; int is accepted beside
        # float (a YAML "0" parses as int and the grid's bounds are legal).
        if not 0.0 <= self.min_score < 1.0:
            raise ConfigError(
                "methods.delivery.retrieval min_score must be a number in "
                f"[0, 1), got {self.min_score}")
