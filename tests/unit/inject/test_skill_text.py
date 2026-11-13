"""The skill renderers (inject/skill_text.py): the card every consumer
reads and the description the folder delivery and materializer share."""

import pytest

from expedistill.core.models import Skill
from expedistill.inject.skill_text import (
    DESCRIPTION_MODES,
    SKILL_MARKERS,
    description_text,
    normalize_step,
    render_card,
    validate_description_mode,
    when_to_use_line,
)

_RECORD = {
    "skill_id": "bank/merge/0001",
    "summary": "Restart a stuck service safely.",
    "when_to_use": "The service rejects connections.\n"
                   "Check the log before restarting.",
    "steps": ["Check the service status."],
    "not_applicable_when": "The status command already reports running.",
}

DISTILL_FIELDS = {
    "when_to_use": "When the service rejects a login.",
    "steps": ["Check the credentials in the config file.",
              "Restart the service."],
    "not_applicable_when": "The subtask is not about logging in.",
}

DISTILL_CARD = "\n".join([
    "[When to use] When the service rejects a login.",
    "[Steps]",
    "1. Check the credentials in the config file.",
    "2. Restart the service.",
    "[Do not use when] The subtask is not about logging in.",
])

#: A heredoc-shaped step (the 2026-10-13 incident payload class): the
#: continuation lines sit at column 0, exactly as the model wrote them.
HEREDOC_FIELDS = {
    "when_to_use": "When you must hash a file without extra tools.",
    "steps": [
        "Check the file exists with `ls <file>`.",
        "Run this exact command:\npython3 - <<'PY'\nimport hashlib\n"
        "print(hashlib.sha256(open('<file>', 'rb').read()).hexdigest())\nPY",
        "Compare the printed digest with the expected value.",
    ],
    "not_applicable_when": "The file is not readable.",
}

HEREDOC_CARD = "\n".join([
    "[When to use] When you must hash a file without extra tools.",
    "[Steps]",
    "1. Check the file exists with `ls <file>`.",
    "2. Run this exact command:",
    "python3 - <<'PY'",
    "import hashlib",
    "print(hashlib.sha256(open('<file>', 'rb').read()).hexdigest())",
    "PY",
    "3. Compare the printed digest with the expected value.",
    "[Do not use when] The file is not readable.",
])


def _skill(**fields):
    base = {"skill_id": "s", "subtask_type": "type/001", "source_subtasks": ["t"],
            "summary": "A summary.", **DISTILL_FIELDS}
    base.update(fields)
    return Skill(**base)


# ---------------------------------------------------------------------------
# render_card
# ---------------------------------------------------------------------------

def test_markers_are_pinned():
    assert SKILL_MARKERS == ("[When to use]", "[Steps]", "[Do not use when]")


def test_card_is_the_exact_marker_layout():
    assert render_card(_skill()) == DISTILL_CARD


def test_card_of_a_mapping_equals_the_card_of_the_skill():
    assert render_card(_skill().to_dict()) == DISTILL_CARD


def test_summary_is_not_on_the_card():
    assert "A summary." not in render_card(_skill())


def test_multiline_step_keeps_its_continuation_lines_at_column_0():
    assert render_card(_skill(**HEREDOC_FIELDS)) == HEREDOC_CARD


def test_multiline_scalar_continues_at_column_0():
    card = render_card(_skill(
        not_applicable_when="Not about logging in.\nRead-only tasks."))
    assert card.split("\n")[-2:] == [
        "[Do not use when] Not about logging in.", "Read-only tasks."]


def test_crlf_and_self_numbering_are_normalized():
    card = render_card(_skill(
        when_to_use="When it fails.\r\nSecond line.",
        steps=["1. Check.\rRestart.", "2) Verify."]))
    assert "\r" not in card
    assert card.split("\n") == [
        "[When to use] When it fails.", "Second line.", "[Steps]",
        "1. Check.", "Restart.", "2. Verify.",
        "[Do not use when] The subtask is not about logging in."]


def test_normalize_step_strips_one_self_numbering():
    assert normalize_step("  12) Restart.  ") == "Restart."
    assert normalize_step("Restart.") == "Restart."


def test_verbatim_skill_renders_its_document():
    skill = _skill(when_to_use="", steps=[], not_applicable_when="",
                   verbatim_text="# Imported\n\n- keep this")
    assert render_card(skill) == "# Imported\n\n- keep this"


# ---------------------------------------------------------------------------
# descriptions
# ---------------------------------------------------------------------------

def test_the_trigger_line_is_the_when_to_use_field_on_one_line():
    assert when_to_use_line(_RECORD) == (
        "The service rejects connections. Check the log before restarting.")
    assert when_to_use_line({**_RECORD, "when_to_use": ""}) == ""


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
    record = {**_RECORD, "when_to_use": "Use this when the service "
                                        "rejects connections."}
    assert description_text(record, "summary_then_when_to_use") == (
        "Restart a stuck service safely. Use this when the service "
        "rejects connections.")


def test_each_mode_falls_back_to_the_other_field():
    empty_summary = {**_RECORD, "summary": ""}
    assert description_text(empty_summary, "summary") == (
        "The service rejects connections. Check the log before restarting.")
    no_trigger = {**_RECORD, "when_to_use": ""}
    assert description_text(no_trigger, "when_to_use") == \
        "Restart a stuck service safely."


def test_both_empty_gets_a_non_empty_generic_description():
    record = {"skill_id": "bank/merge/0001", "summary": "",
              "when_to_use": ""}
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
