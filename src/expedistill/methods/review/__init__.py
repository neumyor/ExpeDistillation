"""The learn phase's ``review`` parameters (layer 6, light package).

One mapping per learn phase configures the parts of the five-step learn
flow that are method choices: the segmentation budget (``K``,
``retry_limit``) and whether weak-model failure segments are card
material (``write_lessons``). The flow itself lives in
``stages/phase_learn.py``.
"""

from dataclasses import dataclass

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
      segments only).
    """

    K: int = 5
    retry_limit: int = 2
    write_lessons: bool = True

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
