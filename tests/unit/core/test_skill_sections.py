"""Splitting and splicing the three sections of a stored skill text."""

import pytest

from expedistill.core.models import SKILL_MARKERS
from expedistill.core.skill_sections import (
    BODY_MARKERS,
    TRIGGER_MARKERS,
    split_sections,
    splice_sections,
)

WHEN, STEPS, NOT = SKILL_MARKERS

OLD = "\n".join([
    f"{WHEN} At task start, when the input is a CSV.",
    STEPS,
    "1. old step one",
    "2. old step two",
    f"{NOT} The input is not tabular.",
])

NEW = "\n".join([
    f"{WHEN} After the first environment check, when a header is present.",
    STEPS,
    "1. new step one",
    f"{NOT} There is no header.",
])


def test_markers_partition_into_trigger_and_body():
    assert TRIGGER_MARKERS == (WHEN,)
    assert BODY_MARKERS == (STEPS, NOT)


def test_split_keeps_each_block_with_its_marker():
    blocks = split_sections(OLD)
    assert list(blocks) == [WHEN, STEPS, NOT]
    assert blocks[STEPS] == f"{STEPS}\n1. old step one\n2. old step two"
    assert blocks[WHEN].startswith(WHEN)


def test_split_drops_text_before_the_first_marker():
    blocks = split_sections("Here is the skill:\n" + OLD)
    assert "Here is" not in "".join(blocks.values())


def test_splice_taking_nothing_new_reproduces_old():
    assert splice_sections(OLD, NEW, ()) == OLD


def test_splice_taking_everything_new_reproduces_new():
    assert splice_sections(OLD, NEW, SKILL_MARKERS) == NEW


def test_splice_the_body_from_new_keeps_the_old_trigger():
    out = split_sections(splice_sections(OLD, NEW, BODY_MARKERS))
    assert out[WHEN] == split_sections(OLD)[WHEN]
    assert out[STEPS] == split_sections(NEW)[STEPS]
    assert out[NOT] == split_sections(NEW)[NOT]


def test_splice_the_trigger_from_new_keeps_the_old_body():
    out = split_sections(splice_sections(OLD, NEW, TRIGGER_MARKERS))
    assert out[WHEN] == split_sections(NEW)[WHEN]
    assert out[STEPS] == split_sections(OLD)[STEPS]
    assert out[NOT] == split_sections(OLD)[NOT]


@pytest.mark.parametrize("missing", SKILL_MARKERS)
def test_splice_refuses_a_text_that_lacks_a_block_it_must_give(missing):
    broken = "\n".join(line for line in NEW.split("\n")
                       if not line.startswith(missing))
    with pytest.raises(ValueError, match="lacks"):
        splice_sections(OLD, broken, SKILL_MARKERS)
