"""Delivery drivers: how an arm's skill injection is built.

Params for every method live here (light, never a driver import) so
stage 0 can validate them before anything freezes.
"""

from dataclasses import dataclass
from typing import Any

from expedistill.core.config import ConfigError
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    DESCRIPTION_MODES,
)

__all__ = ["ExamFolderParams", "FolderParams", "GroupByTaskParams",
           "NoneParams", "PLAYBOOK_SOURCES", "RetrievalParams",
           "WholeBankParams", "validate_playbook_source"]


@dataclass(frozen=True)
class WholeBankParams:
    """The whole bank, one fixed injection.

    ``verbatim`` keeps every skill's text exactly as stored (no bullet, no
    newline collapsing); the default renders the library layout D9 pins.
    Set it for skill documents imported from other methods.
    """

    verbatim: bool = False


PLAYBOOK_SOURCES = ("", "learned")


def validate_playbook_source(where: str, params: Any) -> None:
    """``playbook_source`` is empty or ``learned``, and a learned playbook
    cannot be combined with a playbook file (two sources, one slot)."""
    if params.playbook_source not in PLAYBOOK_SOURCES:
        raise ConfigError(
            f"{where} playbook_source must be one of {PLAYBOOK_SOURCES}, "
            f"got {params.playbook_source!r}")
    if params.playbook_source and (params.playbook_file or params.playbook):
        raise ConfigError(
            f"{where} sets playbook_source {params.playbook_source!r} and "
            "also a playbook file; a delivery has one playbook source")


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
    #: A global playbook rendered at the top of every skill block (the
    #: 2026-11-04 P0 switch). Empty keeps the pre-playbook prompt
    #: byte-identical; the phase resolver fills it from ``playbook_file``.
    playbook: str = ""
    #: The config keys the phase resolver consumes: the repo-relative
    #: playbook file and its frozen sha256. They stay in the params so
    #: the load-time params check (stage 0) sees no unknown key; the
    #: drivers read only ``playbook``.
    playbook_file: str = ""
    playbook_sha256: str = ""
    #: ``learned`` takes the playbook from the earlier learn phase that
    #: wrote one (stages/learn_playbook.py) instead of from a file.
    playbook_source: str = ""

    def __post_init__(self) -> None:
        if self.description_mode not in DESCRIPTION_MODES:
            raise ConfigError(
                "methods.delivery.folder description_mode must be one of "
                f"{DESCRIPTION_MODES}, got {self.description_mode!r}")
        for name in ("playbook", "playbook_file", "playbook_sha256"):
            if not isinstance(getattr(self, name), str):
                raise ConfigError(
                    f"methods.delivery.folder {name} must be a string, got "
                    f"{type(getattr(self, name)).__name__}")
        validate_playbook_source("methods.delivery.folder", self)


@dataclass(frozen=True)
class NoneParams:
    """No parameters: no skills, the bare injection."""


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


def __getattr__(name: str):
    """Lazily re-export ``ExamFolderParams`` from its driver module.

    The exam catalog's params class lives in ``exam_folder.py`` beside
    the driver it configures (one self-contained unit), while this
    package must stay driver-free so stage 0 can validate every phase
    without loading drivers. Resolving the name on first access keeps
    both: importing the package loads no driver, and
    ``from expedistill.methods.delivery import ExamFolderParams`` still
    works.
    """
    if name == "ExamFolderParams":
        from expedistill.methods.delivery.exam_folder import (
            ExamFolderParams,
        )
        return ExamFolderParams
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}")
