"""The delivery methods' params classes.

Light by design (no driver import), so stage 0 can validate every
phase's delivery mapping before anything freezes.
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    DESCRIPTION_MODES,
)

__all__ = ["FolderParams", "NoneParams", "WholeBankParams"]


@dataclass(frozen=True)
class WholeBankParams:
    """The whole bank, one fixed injection.

    ``verbatim`` keeps every skill's card (an imported skill's card is its
    ``verbatim_text``) exactly as rendered (no bullet, no
    newline collapsing); the default renders the library layout D9 pins.
    Set it for skill documents imported from other methods.
    """

    verbatim: bool = False


@dataclass(frozen=True)
class FolderParams:
    """The folder delivery's parameters.

    ``description_mode`` chooses what the per-skill ``SKILL.md`` YAML
    ``description`` (and the catalog's one-line column) is built from:

    - ``summary``: the skill's WHAT sentence;
    - ``when_to_use``: the "when to use" trigger;
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
