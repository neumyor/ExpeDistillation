"""Unit tests for blocks/author.py: the skill-field parsers."""

import pytest

from expedistill.blocks.author import (
    parse_content_fields,
    parse_skill_fields,
    parse_trigger_fields,
)
from expedistill.infra.reliability import ModelOutputError

FIELDS = {
    "summary": "check the credentials before a retry",
    "when_to_use": "When the service rejects a login.",
    "steps": ["Check the credentials in the config file.",
              "Restart the service."],
    "not_applicable_when": "The service has no login.",
}


def test_full_payload_parses_and_ignores_extra_keys():
    assert parse_skill_fields("T", {**FIELDS, "extra": "x"}) == FIELDS


def test_the_two_sides_parse_only_their_own_fields():
    assert parse_trigger_fields("T", FIELDS) == {
        "summary": FIELDS["summary"], "when_to_use": FIELDS["when_to_use"]}
    assert parse_content_fields("T", FIELDS) == {
        "steps": FIELDS["steps"],
        "not_applicable_when": FIELDS["not_applicable_when"]}


def test_a_side_needs_only_its_own_fields():
    parse_trigger_fields("T", {"summary": "s", "when_to_use": "w"})
    parse_content_fields("T", {"steps": ["a"], "not_applicable_when": "n"})
    with pytest.raises(ModelOutputError, match="missing required field"):
        parse_trigger_fields("T", {"steps": ["a"]})


def test_self_numbering_is_stripped_and_newlines_normalized():
    fields = parse_skill_fields("T", {
        **FIELDS,
        "when_to_use": "When the service rejects a login.\r\nSecond line.",
        "steps": ["1. Check the credentials.\rRestart.", "2) Restart."],
    })
    assert fields["when_to_use"] == \
        "When the service rejects a login.\nSecond line."
    assert fields["steps"] == ["Check the credentials.\nRestart.",
                               "Restart."]


def test_a_non_object_raises():
    with pytest.raises(ModelOutputError, match="JSON object"):
        parse_skill_fields("T", ["not", "an", "object"])


@pytest.mark.parametrize(
    "field, value",
    [
        ("when_to_use", ""),
        ("when_to_use", "   "),
        ("not_applicable_when", 7),
        ("steps", []),
        ("steps", [""]),
        ("steps", ["1. "]),
        ("steps", [7]),
        ("steps", "not a list"),
    ],
)
def test_bad_fields_raise(field, value):
    with pytest.raises(ModelOutputError):
        parse_skill_fields("T", {**FIELDS, field: value})


@pytest.mark.parametrize(
    "summary, match",
    [
        ("", "non-empty"),
        (7, "must be a string"),
        ("one line\na second line", "ONE line"),
        ("carriage return\rinside", "ONE line"),
    ],
)
def test_bad_summaries_raise(summary, match):
    with pytest.raises(ModelOutputError, match=match):
        parse_trigger_fields("T", {**FIELDS, "summary": summary})
