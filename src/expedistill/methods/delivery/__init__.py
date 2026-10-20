"""Delivery drivers: how an arm's skill injection is built.

Params for every method live here (light, never a driver import) so
stage 0 can validate them before anything freezes.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["FolderParams", "GroupByTaskParams", "NoneParams",
           "PerSkillParams", "RetrievalParams", "WholeBankParams"]


@dataclass(frozen=True)
class WholeBankParams:
    """No parameters: the whole bank, one fixed injection."""


@dataclass(frozen=True)
class FolderParams:
    """The folder delivery's parameters.

    ``inline_catalog`` controls where the catalog text reaches the
    agent. ``False`` (the default, the M2 design) keeps the catalog in
    ``catalog.md`` inside the mounted folder; the agent must ``cat`` it.
    ``True`` puts the catalog text into the unit row's ``skills_block``
    so the benchmark can include it in the remote skill file the agent is
    told to read (the TB plugin writes it into ``SKILL.md``); the skill
    bodies still live in the folder for on-demand ``cat`` -- the catalog
    is no longer hidden behind a file the agent has to find and open,
    which the 2026-10-19 folder-run audit found the weak model rarely did
    (26/84 attempts hit a wrong path and saw no catalog lines). The
    manifest's ``catalog_text`` is populated either way, so the
    materialization and the M4 read-stats are unchanged.
    """

    inline_catalog: bool = False


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
