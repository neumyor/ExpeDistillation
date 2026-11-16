"""G2 (blocks.subtask_types): the typing reply's checks and the program's
type ids."""

import pytest

from expedistill.blocks.subtask_types import (
    parse_typing_reply,
    render_type_table,
    segment_card,
    type_segments,
)
from expedistill.core.models import Subtask, SubtaskType
from expedistill.infra.reliability import ModelOutputError
from tests.helpers.fake_llm import json_script, make_fake_handle

TABLE = [SubtaskType(type_id="type/001", name="find the object",
                     entry_state="The object is not in view.",
                     phase_label="p02:learn"),
         SubtaskType(type_id="type/002", name="heat the object",
                     entry_state="The object is held.",
                     phase_label="p02:learn")]
IDS = ["seg/a", "seg/b", "seg/c"]


def _new(key, name="clean the object", entry="The object is dirty."):
    return {"key": key, "name": name, "entry_state": entry}


def _parse(assignments, new_types=(), table=TABLE, expected=IDS):
    return parse_typing_reply(
        {"new_types": list(new_types),
         "assignments": [{"subtask_id": subtask_id, "type": ref}
                         for subtask_id, ref in assignments]},
        expected=expected, table=table, phase_label="p04:learn")


def test_existing_and_new_types_resolve_to_ids():
    result = _parse([("seg/a", "type/002"), ("seg/b", "n1"),
                     ("seg/c", "n1")], [_new("n1")])
    assert result.assignments == {"seg/a": "type/002", "seg/b": "type/003",
                                  "seg/c": "type/003"}
    assert [row.to_dict() for row in result.new_types] == [SubtaskType(
        type_id="type/003", name="clean the object",
        entry_state="The object is dirty.",
        phase_label="p04:learn").to_dict()]


def test_new_ids_follow_the_segments_order_not_the_reply_order():
    forward = _parse([("seg/a", "y"), ("seg/b", "x"), ("seg/c", "x")],
                     [_new("x", "x name"), _new("y", "y name")])
    backward = _parse([("seg/c", "x"), ("seg/b", "x"), ("seg/a", "y")],
                      [_new("y", "y name"), _new("x", "x name")])
    assert forward == backward
    assert [(row.type_id, row.name) for row in forward.new_types] == [
        ("type/003", "y name"), ("type/004", "x name")]


def test_an_empty_table_starts_at_the_first_serial():
    result = _parse([("seg/a", "k")], [_new("k")], table=[],
                    expected=["seg/a"])
    assert result.assignments == {"seg/a": "type/001"}


def test_an_unused_new_type_is_dropped():
    result = _parse([(subtask_id, "type/001") for subtask_id in IDS],
                    [_new("spare")])
    assert result.new_types == []


@pytest.mark.parametrize("assignments, new_types, problem", [
    ([("seg/a", "type/001"), ("seg/b", "type/001")], [], "unassigned"),
    ([("seg/a", "type/001"), ("seg/a", "type/001"), ("seg/b", "type/001"),
      ("seg/c", "type/001")], [], "assigned twice"),
    ([("seg/a", "type/001"), ("seg/b", "type/001"), ("seg/c", "type/001"),
      ("seg/z", "type/001")], [], "not one of the segments"),
    ([("seg/a", "type/009"), ("seg/b", "type/001"), ("seg/c", "type/001")],
     [], "neither in the table nor declared"),
    ([("seg/a", "n1"), ("seg/b", "n1"), ("seg/c", "n1")],
     [_new("n1"), _new("n1")], "declared twice"),
    ([("seg/a", "n1"), ("seg/b", "n1"), ("seg/c", "n1")],
     [_new("n1", name=" ")], "name"),
    ([("seg/a", "type/001"), ("seg/b", "type/001"), ("seg/c", "type/001")],
     [_new("type/001")], "reuse existing type ids"),
    ([("seg/a", ["type/001"]), ("seg/b", "type/001"),
      ("seg/c", "type/001")], [], "neither in the table"),
])
def test_a_reply_that_breaks_the_rules_is_refused(assignments, new_types,
                                                  problem):
    with pytest.raises(ModelOutputError, match=problem):
        _parse(assignments, new_types)


def test_a_reply_of_the_wrong_shape_is_refused():
    for parsed in ([], {"new_types": []}, {"assignments": {}}):
        with pytest.raises(ModelOutputError):
            parse_typing_reply(parsed, expected=IDS, table=TABLE,
                               phase_label="p04:learn")


def test_the_call_shows_the_table_and_the_cards_in_id_order():
    reply = json_script({"new_types": [], "assignments": [
        {"subtask_id": subtask_id, "type": "type/001"}
        for subtask_id in IDS]})
    handle = make_fake_handle(script=[reply])
    cards = {subtask_id: f"CARD {subtask_id}"
             for subtask_id in reversed(IDS)}

    result = type_segments(cards, TABLE, phase_label="p04:learn",
                           handle=handle, correction_note="FIX IT. ")

    prompt = handle.client.calls[0]["user"]
    assert render_type_table(TABLE) in prompt
    assert prompt.index("CARD seg/a") < prompt.index("CARD seg/c")
    assert "FIX IT." in prompt
    assert set(result.assignments) == set(IDS)
    assert render_type_table([]) == "(none)"


def test_a_segment_card_clips_its_steps_view():
    subtask = Subtask(subtask_id="t/seg00", phase_label="p02:learn",
                      task_id="tb:t1", trajectory_id="t", source="strong",
                      trajectory_success=True, segment_index=0,
                      step_range=(0, 3), label="find it",
                      description="look around", success=True)
    card = segment_card(subtask, "x" * 5000, 300)
    assert "SUBTASK_ID: t/seg00" in card and "LABEL: find it" in card
    assert "steps elided" in card
    assert len(card) < 600
