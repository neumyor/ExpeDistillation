"""The shared description composition used by the folder delivery and the
folder materializer (inject/skill_text.py)."""

import pytest

from expedistill.inject.skill_text import (
    DESCRIPTION_MODES,
    description_text,
    validate_description_mode,
    when_to_use_line,
)

_CONTENT = "\n".join([
    "[When to use] The service rejects connections.",
    "Check the log before restarting.",
    "[Steps]",
    "1. Check the service status.",
    "[Do not use when] The status command already reports running.",
])

_RECORD = {
    "skill_id": "bank/merge/0001",
    "summary": "Restart a stuck service safely.",
    "content": _CONTENT,
}


def test_the_trigger_is_the_marker_remainder_until_the_next_section():
    assert when_to_use_line(_CONTENT) == (
        "The service rejects connections. Check the log before restarting.")
    assert when_to_use_line("[Steps]\n1. do it") == ""


def test_summary_only_mode():
    assert description_text(_RECORD, "summary") == \
        "Restart a stuck service safely."


def test_trigger_only_mode():
    assert description_text(_RECORD, "when_to_use") == (
        "The service rejects connections. Check the log before restarting.")


def test_combined_mode_adds_the_explicit_joiner():
    assert description_text(_RECORD, "summary_then_when_to_use") == (
        "Restart a stuck service safely. Use when: The service rejects "
        "connections. Check the log before restarting.")


def test_combined_mode_does_not_repeat_a_use_when_prefix():
    record = {**_RECORD, "content": "[When to use] Use this when the "
                                  "service rejects connections."}
    assert description_text(record, "summary_then_when_to_use") == (
        "Restart a stuck service safely. Use this when the service "
        "rejects connections.")


def test_each_mode_falls_back_to_the_other_field():
    empty_summary = {**_RECORD, "summary": ""}
    assert description_text(empty_summary, "summary") == (
        "The service rejects connections. Check the log before restarting.")
    markerless = {**_RECORD, "content": "no sections here"}
    assert description_text(markerless, "when_to_use") == \
        "Restart a stuck service safely."


def test_both_empty_gets_a_non_empty_generic_description():
    record = {"skill_id": "bank/merge/0001", "summary": "",
              "content": "no sections here"}
    assert description_text(record, "summary_then_when_to_use") == \
        "Skill bank/merge/0001."


def test_the_description_is_one_line_and_bounded():
    record = {**_RECORD, "summary": "a" * 2000}
    text = description_text(record, "summary")
    assert len(text) == 1024
    assert "\n" not in text


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="description_mode"):
        validate_description_mode("unknown")
    assert DESCRIPTION_MODES == (
        "summary", "when_to_use", "summary_then_when_to_use")
