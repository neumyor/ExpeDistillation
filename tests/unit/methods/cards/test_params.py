"""CardAuthoringParams: the ``subtask_cards`` author method's guards."""

from dataclasses import fields

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods.cards import CardAuthoringParams


@pytest.mark.parametrize("name", [
    "typing_chunk_size", "max_segments_per_card", "max_chars_per_segment",
    "typing_max_chars_per_segment"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "8"])
def test_a_size_must_be_an_int_at_least_one(name, value):
    with pytest.raises(ConfigError, match=name):
        CardAuthoringParams(**{name: value})


@pytest.mark.parametrize("value", [-1, True, "2"])
def test_the_retry_limit_must_be_an_int_at_least_zero(value):
    with pytest.raises(ConfigError, match="write_retry_limit"):
        CardAuthoringParams(write_retry_limit=value)


def test_the_bounds_are_inclusive():
    params = CardAuthoringParams(typing_chunk_size=1,
                                 max_segments_per_card=1,
                                 max_chars_per_segment=1,
                                 typing_max_chars_per_segment=1,
                                 write_retry_limit=0)
    assert [getattr(params, item.name) for item in fields(params)] \
        == [1, 1, 1, 1, 0]


def test_typing_uses_the_writer_budget_unless_it_has_its_own():
    assert CardAuthoringParams(max_chars_per_segment=900).typing_chars == 900
    assert CardAuthoringParams(
        max_chars_per_segment=48000,
        typing_max_chars_per_segment=4000).typing_chars == 4000
