"""Unit tests for extract_json, adapted from the SkillExpand json_output tests:
fenced blocks, prose around unfenced objects, arrays, quoting, trailing
commas, gateway prefixes, and the deliberate rejection of truncated output.
The 2026-10-13 incident suite (docs/decisions/
g2_multiline_and_json_extraction_fix_20261014.md) pins the span-relative
fence priority: an unfenced object whose string values embed ``` code
blocks must win over the fragments those blocks contain."""

import json

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


# ---------------------------------------------------------------------------
# The 2026-10-13 incident (run tb21_split_v2_20261013_2125): an UNFENCED JSON
# object whose string values embed ``` code blocks. Unconditional
# fence-first scanning parsed a stray `[]` / `[0]` fragment inside the
# embedded block before the enclosing object, and the reply reached the
# caller as a top-level array ("expected a JSON object, got list", 5/315
# stage-3 units). The fence priority is span-relative now; these tests pin
# the new order against the old failure and its guard.
# ---------------------------------------------------------------------------

def test_unfenced_object_with_embedded_code_fence_fragments():
    # Dump 0178 shape, shortened: the whole reply is one complete JSON
    # object; its steps value embeds a ``` block whose code contains `[]`,
    # `[0]`, `['request_id']` and `argv[1]` fragments.
    steps_item = (
        "Run this exact block:\n"
        "```\n"
        "python3 - <<'PY'\n"
        "seen = []\n"
        "if len(sys.argv) > 1:\n"
        "    seen.append(sys.argv[1])\n"
        "req = json.loads(open('<file>').read())\n"
        "print(req['request_id'])\n"
        "print(data[0])\n"
        "PY\n"
        "```"
    )
    text = json.dumps({
        "when_to_use": "probe an unknown file",
        "steps": ["Run `ls` and note <file>.", steps_item],
        "verify": "the block prints a request id",
        "not_applicable_when": "the file is missing",
    }, ensure_ascii=False)

    parsed = extract_json(text)

    assert isinstance(parsed, dict)
    assert parsed["steps"][1] == steps_item


def test_unfenced_python_literal_with_embedded_code_fence():
    # The same incident shape through the literal_eval last resort (a
    # Python-quoted reply): fence-first used to return the embedded `[]`
    # fragment as an array; the whole text must reach literal_eval and
    # parse as the dict.
    text = ("{'cmd': 'run:\\n```\\nitems = []\\nprint(data[0])\\n```\\n"
            "done', 'ok': True}")

    assert extract_json(text) == {
        "cmd": "run:\n```\nitems = []\nprint(data[0])\n```\ndone",
        "ok": True,
    }


def test_prose_then_unfenced_object_with_embedded_fence():
    # Prose prefix (so the whole text cannot parse), the object carries an
    # embedded fence with a bracket fragment: the object must win, not the
    # fragment inside the fence.
    text = ('Sure, here is the skill.\n'
            '{"steps": ["run `ls`", "then:\\n```\\nflags = []\\n```"]}')

    parsed = extract_json(text)

    assert isinstance(parsed, dict)
    assert parsed["steps"][1] == "then:\n```\nflags = []\n```"


def test_reasoning_fragment_span_loses_to_fenced_answer():
    # The priority guard: reasoning junk with its own span (`[0]`) in front
    # of a fenced answer must NOT win -- the wrapper fence keeps its old
    # precedence over full-text spans.
    text = ("Let me check the first item [0] before answering.\n"
            '```json\n{"verdict": "correct"}\n```')

    assert extract_json(text) == {"verdict": "correct"}
