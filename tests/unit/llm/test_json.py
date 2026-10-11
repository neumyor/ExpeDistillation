"""Unit tests for extract_json, adapted from the SkillExpand json_output tests:
fenced blocks, prose around unfenced objects, arrays, quoting, trailing
commas, gateway prefixes, and the deliberate rejection of truncated output."""

import pytest

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import extract_json


def test_fenced_json_block():
    text = 'Here you go:\n```json\n{"verdict": "correct"}\n```\nDone.'
    assert extract_json(text) == {"verdict": "correct"}


def test_fenced_block_without_language_tag():
    text = "```\n{\"a\": 1}\n```"
    assert extract_json(text) == {"a": 1}


def test_unfenced_object_after_prose():
    text = 'Let me think... the answer is {"a": 1, "b": [2, 3]} as shown.'
    assert extract_json(text) == {"a": 1, "b": [2, 3]}


def test_fenced_array():
    text = "```json\n[{\"start\": 0}, {\"start\": 1}]\n```"
    assert extract_json(text) == [{"start": 0}, {"start": 1}]


def test_unfenced_array_with_prose():
    text = "Segments:\n[{\"start\": 0, \"end\": 2}] hope this helps"
    assert extract_json(text) == [{"start": 0, "end": 2}]


def test_braces_inside_strings_do_not_confuse_the_scanner():
    text = '{"note": "a literal } and { inside", "ok": true}'
    assert extract_json(text) == {"note": "a literal } and { inside", "ok": True}


def test_two_complete_objects_first_wins():
    text = '{"first": 1} explanatory text {"second": 2}'
    assert extract_json(text) == {"first": 1}


def test_trailing_comma_repaired_outside_strings():
    assert extract_json('{"a": 1,}') == {"a": 1}
    assert extract_json("[1, 2,]") == [1, 2]
    # A trailing comma inside a string value must be left untouched.
    assert extract_json('{"note": "a,}"}') == {"note": "a,}"}


def test_gateway_json_prefix_stripped():
    text = 'json\n{"verdict": "correct"}'
    assert extract_json(text) == {"verdict": "correct"}


def test_python_literal_dict_as_last_resort():
    assert extract_json("{'verdict': 'correct', 'ok': True}") == {
        "verdict": "correct",
        "ok": True,
    }


def test_truncated_json_rejected():
    with pytest.raises(ModelOutputError):
        extract_json('{"verdict": "cor')


def test_truncated_array_rejected():
    with pytest.raises(ModelOutputError):
        extract_json('[{"a": 1}, {"b":')


def test_plain_text_rejected():
    with pytest.raises(ModelOutputError):
        extract_json("I cannot answer that question.")


def test_empty_and_none_rejected():
    with pytest.raises(ModelOutputError):
        extract_json("")
    with pytest.raises(ModelOutputError):
        extract_json(None)
