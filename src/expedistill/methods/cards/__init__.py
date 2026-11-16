"""Writing skill cards per subtask type (layer 6): the learn phase's
``author`` family, method ``subtask_cards``.

Every learn phase types its new segments into the run's append-only subtask
type table (one ``SUBTASK_TYPING`` call per chunk of segments, each chunk
carrying the whole current table), and writes one card for each type that
has gap evidence and strong-model material (``CARD_WRITE``). A type has at
most one card (``skill_id = <type_id>/skill``), so two cards never cover
one type.

* ``material`` -- a type's strong successful and weak failed segments;
* ``writer`` -- ``write_card``, one card for one type.

This light package holds the author family's params class, so stage 0 can
validate a config without loading the drivers.
"""

from dataclasses import dataclass, fields
from typing import Optional

from expedistill.core.config import ConfigError

__all__ = ["CardAuthoringParams"]


@dataclass(frozen=True)
class CardAuthoringParams:
    """The ``subtask_cards`` author method's parameters.

    Input and output guards, not experimental tuning knobs:

    - ``typing_chunk_size``: the most segments one typing call classifies;
      more segments are typed in chunks by segment id;
    - ``max_segments_per_card``: the most material segments one card
      writing call reads;
    - ``max_chars_per_segment``: the character budget of one segment's
      rendered steps in the card writer's material;
    - ``typing_max_chars_per_segment``: the same budget in the typing
      call, which needs less than the writer; None (the default) uses
      ``max_chars_per_segment``;
    - ``write_retry_limit``: how many times a card whose output fails the
      checks is rewritten (after ``call_model``'s own format budget).
    """

    typing_chunk_size: int = 100
    max_segments_per_card: int = 8
    max_chars_per_segment: int = 1200
    typing_max_chars_per_segment: Optional[int] = None
    write_retry_limit: int = 2

    @property
    def typing_chars(self) -> int:
        """The per-segment character budget of the typing call."""
        if self.typing_max_chars_per_segment is None:
            return self.max_chars_per_segment
        return self.typing_max_chars_per_segment

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "typing_max_chars_per_segment" and value is None:
                continue
            floor = 0 if item.name == "write_retry_limit" else 1
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < floor:
                raise ConfigError(
                    f"author method 'subtask_cards': {item.name} must be an "
                    f"int >= {floor}, got {value!r}")
