"""The learn phase's ``review`` parameters (layer 6, light package).

One mapping per learn phase configures the parts of the five-step learn
flow that are method choices: the segmentation budget (``K``,
``retry_limit``), whether weak-model failure segments are card
material (``write_lessons``), and the three ablation switches (``cards``,
``revise_scope``, ``evolve_profile``). The switches default to the full
method and are written into a config only when an ablation turns one
off, so a config without them freezes exactly what it did before they
existed. The flow itself lives in ``stages/phase_learn.py``.
"""

from dataclasses import dataclass
from typing import Optional

from expedistill.composites.evidence import REVISE_BOTH, REVISE_SCOPES
from expedistill.core.config import ConfigError

__all__ = ["ReviewParams"]


@dataclass(frozen=True)
class ReviewParams:
    """One learn phase's review parameters.

    * ``K`` -- the most segments one trajectory is cut into;
    * ``retry_limit`` -- how many times a segmentation that fails its
      check is redone;
    * ``write_lessons`` -- whether the weak model's failure segments of a
      type are material for that type's card (off: the strong model's
      segments only);
    * ``cards`` -- ``false`` leaves the skill bank untouched: no card is
      written, rewritten or discarded, while the profile and the playbook
      still evolve (the ``no_skills`` ablation);
    * ``revise_scope`` -- ``both`` (the method), ``trigger_only`` or
      ``content_only``: the evidence of the side that is out of scope is
      recorded but triggers no rewrite and does not count toward the
      delete rule; gap evidence writes new cards under every scope.
      None (absent) means ``both``; it is refused together with
      ``cards: false``, which rewrites nothing at all;
    * ``evolve_profile`` -- ``false`` keeps the profile empty, so every
      writer reads the neutral no-profile section.
    """

    K: int = 5
    retry_limit: int = 2
    write_lessons: bool = True
    cards: bool = True
    revise_scope: Optional[str] = None
    evolve_profile: bool = True

    @property
    def scope(self) -> str:
        """The effective revise scope."""
        return REVISE_BOTH if self.revise_scope is None \
            else self.revise_scope

    def __post_init__(self) -> None:
        if isinstance(self.K, bool) or not isinstance(self.K, int) \
                or self.K < 1:
            raise ConfigError(
                "review method 'review': K must be an int >= 1, got "
                f"{self.K!r}")
        if isinstance(self.retry_limit, bool) \
                or not isinstance(self.retry_limit, int) \
                or self.retry_limit < 0:
            raise ConfigError(
                "review method 'review': retry_limit must be an int >= 0, "
                f"got {self.retry_limit!r}")
        if not isinstance(self.write_lessons, bool):
            raise ConfigError(
                "review method 'review': write_lessons must be a bool, got "
                f"{self.write_lessons!r}")
        for name in ("cards", "evolve_profile"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(
                    f"review method 'review': {name} must be a bool, got "
                    f"{getattr(self, name)!r}")
        if self.revise_scope is not None \
                and self.revise_scope not in REVISE_SCOPES:
            raise ConfigError(
                f"review method 'review': revise_scope must be one of "
                f"{list(REVISE_SCOPES)}, got {self.revise_scope!r}")
        if not self.cards and self.revise_scope is not None:
            raise ConfigError(
                "review method 'review': revise_scope and cards: false "
                "are mutually exclusive; with cards off no card is "
                "rewritten, so there is no scope to set")
