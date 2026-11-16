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
from expedistill.llm.call import extract_json


@pytest.mark.parametrize("text,expected", [
    ('Here you go:\n```json\n{"verdict": "correct"}\n```\nDone.',
     {"verdict": "correct"}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Let me think... the answer is {"a": 1, "b": [2, 3]} as shown.',
     {"a": 1, "b": [2, 3]}),
    ('```json\n[{"start": 0}, {"start": 1}]\n```',
     [{"start": 0}, {"start": 1}]),
    ('Segments:\n[{"start": 0, "end": 2}] hope this helps',
     [{"start": 0, "end": 2}]),
    ('{"note": "a literal } and { inside", "ok": true}',
     {"note": "a literal } and { inside", "ok": True}),
    ('{"first": 1} explanatory text {"second": 2}', {"first": 1}),
    ('{"a": 1,}', {"a": 1}),
    ("[1, 2,]", [1, 2]),
    # A trailing comma inside a string value must be left untouched.
    ('{"note": "a,}"}', {"note": "a,}"}),
    ('json\n{"verdict": "correct"}', {"verdict": "correct"}),
    ("{'verdict': 'correct', 'ok': True}", {"verdict": "correct", "ok": True}),
], ids=["fenced-block", "fence-without-language", "unfenced-after-prose",
        "fenced-array", "unfenced-array-with-prose", "braces-in-strings",
        "first-of-two-objects", "trailing-comma-object",
        "trailing-comma-array", "comma-inside-string", "gateway-prefix",
        "python-literal-last-resort"])
def test_extracts_the_json_value(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", [
    '{"verdict": "cor', '[{"a": 1}, {"b":', "I cannot answer that question.",
    "", None,
], ids=["truncated-object", "truncated-array", "plain-text", "empty", "none"])
def test_unparseable_output_is_rejected(text):
    with pytest.raises(ModelOutputError):
        extract_json(text)


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
