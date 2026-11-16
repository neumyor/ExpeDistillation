"""G1 segment and D7 validation unit tests.

G1 is tested against helpers.fake_llm (json_script / FakeClient), D7 against
hand-built segment lists. Success VALUES are never judged -- only presence
and type -- and that is asserted explicitly (README stage 2)."""

import pytest

from expedistill.infra.reliability import ModelOutputError
from expedistill.blocks.segment import segment, validate_segments
from tests.helpers.fake_llm import json_script, make_fake_handle

VIEW = "task: t (final status: failure)\n\nstep 0\naction: a\nobservation: o"


def good_segment(start, end, success=True):
    return {"start": start, "end": end, "label": "setup",
            "description": "prepare the environment", "success": success}


# ---------------------------------------------------------------------------
# G1 happy path
# ---------------------------------------------------------------------------

def test_segment_returns_the_models_dicts_unchanged():
    payload = [good_segment(0, 3), good_segment(3, 5, success=False)]
    handle = make_fake_handle(script=json_script(payload))
    result = segment(VIEW, K=4, handle=handle)
    assert result == payload


def test_segment_sends_view_and_k_in_the_prompt():
    payload = [good_segment(0, 1)]
    handle = make_fake_handle(script=json_script(payload))
    segment("step 0\naction: a\nobservation: o", K=7, handle=handle)
    (call,) = handle.client.calls
    assert "step 0" in call["user"]
    assert "at most 7 such subtasks" in call["user"]
    assert "SEGMENT" not in call["user"]  # prompt name is not leaked as text


def test_segment_records_usage_when_recorder_given():
    recorded = []

    class Recorder:
        def record(self, **kw):
            recorded.append(kw)

    payload = [good_segment(0, 1)]
    handle = make_fake_handle(script=json_script(payload))
    segment(VIEW, K=2, handle=handle, usage=Recorder())
    assert recorded and recorded[0]["prompt"] == "SEGMENT"
    assert recorded[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# G1 schema validation: every bad-entry shape raises ModelOutputError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"start": 0, "end": 2},                      # top level not an array
    ["not an object"],                           # entry not an object
    [{"start": True, "end": 2, "label": "a", "description": "d", "success": True}],
    [{"start": "0", "end": 2, "label": "a", "description": "d", "success": True}],
    [{"start": 0.0, "end": 2, "label": "a", "description": "d", "success": True}],
    [{"start": 0, "end": True, "label": "a", "description": "d", "success": True}],
    [{"start": -1, "end": 2, "label": "a", "description": "d", "success": True}],
    [{"start": 3, "end": 3, "label": "a", "description": "d", "success": True}],
    [{"start": 4, "end": 2, "label": "a", "description": "d", "success": True}],
    [{"start": 0, "end": 2, "label": "", "description": "d", "success": True}],
    [{"start": 0, "end": 2, "description": "d", "success": True}],   # no label
    [{"start": 0, "end": 2, "label": "   ", "description": "d", "success": True}],
    [{"start": 0, "end": 2, "label": 5, "description": "d", "success": True}],
    [{"start": 0, "end": 2, "label": "a", "description": "", "success": True}],
    [{"start": 0, "end": 2, "label": "a", "description": None, "success": True}],
    [{"start": 0, "end": 2, "label": "a", "description": "d"}],      # no success
    [{"start": 0, "end": 2, "label": "a", "description": "d", "success": "true"}],
    [{"start": 0, "end": 2, "label": "a", "description": "d", "success": 1}],
])
def test_bad_entry_shapes_raise_model_output_error(payload):
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError):
        segment(VIEW, K=3, handle=handle)


def test_error_message_names_the_offending_segment_and_field():
    payload = [good_segment(0, 2), {"start": 1, "end": 2, "label": "a",
                                    "description": "d", "success": "yes"}]
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError) as excinfo:
        segment(VIEW, K=3, handle=handle)
    message = str(excinfo.value)
    assert "segment 1" in message
    assert "success" in message


def test_segment_does_not_check_coverage_that_is_d7s_job():
    # Two segments that leave step 2 uncovered: schema-valid, so G1 returns
    # them; only D7 rejects the coverage.
    payload = [good_segment(0, 1), good_segment(3, 4)]
    handle = make_fake_handle(script=json_script(payload))
    result = segment(VIEW, K=3, handle=handle)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# D7 validation
# ---------------------------------------------------------------------------

def test_valid_full_coverage_passes():
    segs = [good_segment(0, 3), good_segment(3, 7, success=False)]
    assert validate_segments(7, segs, K=5) == []


def test_exactly_k_segments_is_valid():
    segs = [good_segment(0, 1), good_segment(1, 2), good_segment(2, 3)]
    assert validate_segments(3, segs, K=3) == []


def test_empty_list_is_an_error():
    errors = validate_segments(5, [], K=3)
    assert len(errors) == 1
    assert "empty" in errors[0]


# Each case: (total steps, segments, K) and the fragments the errors must
# contain; the fragments of one tuple must appear in the same error line.
@pytest.mark.parametrize("total, segs, k, expected", [
    pytest.param(3, [good_segment(0, 1), good_segment(1, 2),
                     good_segment(2, 3)], 2, [("exceed", "K=2")],
                 id="more_than_k"),
    pytest.param(5, [good_segment(-1, 2), good_segment(2, 5)], 3,
                 [("outside",)], id="below_range"),
    pytest.param(5, [good_segment(0, 2), good_segment(2, 6)], 3,
                 [("outside",)], id="above_range"),
    pytest.param(4, [good_segment(0, 2), good_segment(2, 2),
                     good_segment(2, 4)], 3, [("empty range",)],
                 id="empty_range"),
    pytest.param(5, [good_segment(0, 2), good_segment(3, 5)], 3,
                 [("gap or overlap",), ("coverage error",)], id="gap"),
    pytest.param(5, [good_segment(0, 3), good_segment(2, 5)], 3,
                 [("gap or overlap",)], id="overlap"),
    pytest.param(5, [good_segment(1, 5)], 3,
                 [("starts at step 1, expected 0",)], id="first_start"),
    pytest.param(5, [good_segment(0, 4)], 3,
                 [("ends at step 4", "expected 5")], id="last_end"),
    pytest.param(4, [{"start": 0, "end": 2, "description": "d",
                      "success": True}, good_segment(2, 4)], 3,
                 [("segment 0", "label")], id="missing_label"),
    pytest.param(4, ["nope"], 3, [("must be an object",)], id="non_dict"),
])
def test_invalid_segment_lists_are_errors(total, segs, k, expected):
    errors = validate_segments(total, segs, K=k)
    for fragments in expected:
        assert any(all(f in e for f in fragments) for e in errors)


def test_segments_out_of_order_still_cover_validly():
    # Coverage is judged after sorting by start (spec), so shuffled input
    # that covers exactly is valid.
    segs = [good_segment(4, 6), good_segment(0, 2), good_segment(2, 4)]
    assert validate_segments(6, segs, K=3) == []


def test_validation_never_judges_success_values():
    # All-true and all-false success values are equally valid: D7 checks
    # existence and type only, never semantics (README stage 2).
    for success in (True, False):
        segs = [good_segment(0, 2, success=success), good_segment(2, 4, success=success)]
        assert validate_segments(4, segs, K=3) == []
    # A trajectory that failed overall may still contain "successful"
    # segments; D7 must not cross-check against any overall status.
    assert validate_segments(4, [good_segment(0, 2, success=True),
                                 good_segment(2, 4, success=True)], K=3) == []


def test_coverage_check_skipped_when_boundaries_are_not_integers():
    # Per-segment errors already explain the problem; the coverage pass must
    # not crash on unsortable boundaries.
    segs = [good_segment(0, 2), {"start": "x", "end": 4, "label": "a",
                                 "description": "d", "success": True}]
    errors = validate_segments(4, segs, K=3)
    assert any("'start' must be an integer" in e for e in errors)
    assert not any("coverage" in e for e in errors)
