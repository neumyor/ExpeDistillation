"""Tests for benchmarks/tb/trajectory.py against the recorded fixture shape
(tests/fixtures/tb/trajectory.json): pair mapping, malformed-step skipping,
garbage-input safety, and Harbor usage totals."""

import json
from pathlib import Path

from expedistill.benchmarks.tb.trajectory import (
    reconstruct,
    reconstruct_skip_counts,
    reconstruct_steps,
    total_usage,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tb"


def _traj():
    return json.loads((FIXTURES / "trajectory.json").read_text(encoding="utf-8"))


def test_reconstruct_maps_interaction_steps_to_pairs():
    steps = reconstruct_steps(_traj())
    assert len(steps) == 2
    first = steps[0]
    # Action keeps the model's tool-call rendering: one line per call of
    # the step's batch, arguments as JSON.
    assert first["action"] == (
        'bash_command({"duration": 0.1, "keystrokes": "ls -la\\n"})\n'
        'bash_command({"duration": 0.1, "keystrokes": "ls -la data/\\n"})')
    assert "root@0fa59dab:/app# ls -la" in first["observation"]
    assert steps[1]["action"].startswith(
        'bash_command({"duration": 30.0, "keystrokes": "python train.py')
    assert "model saved to /app/model.bin" in steps[1]["observation"]


def test_malformed_steps_are_skipped_and_counted():
    steps, skipped = reconstruct(_traj())
    # Step 4: tool_calls present but observation.results empty.
    # Step 5: tool_calls is not a list. Both must be skipped, never crash.
    assert skipped == 2
    assert len(steps) == 2


def test_instruction_step_is_neither_kept_nor_counted():
    # The leading source:"user" step carries the task instruction; it has no
    # tool calls and no observation, so it is not a "skipped" step.
    _, skipped = reconstruct(_traj())
    assert skipped == 2


def test_reconstruct_never_crashes_on_garbage():
    assert reconstruct_steps({}) == []
    assert reconstruct_steps({"steps": "not-a-list"}) == []
    assert reconstruct_steps({"steps": [None, 42, "x"]}) == []
    assert reconstruct(None) == ([], 0)
    assert reconstruct([]) == ([], 0)


def test_skip_counts_categorize_dropped_interaction_steps():
    # Real-data shapes (tmp/tbval sample trials): rounds where the model's
    # output failed to parse are dropped because they carry no tool calls,
    # and context-handoff system steps are dropped because their observation
    # has no readable content. The counts must surface in the metadata while
    # the kept steps stay byte-identical to the pre-categorization pairing.
    traj = {
        "steps": [
            {"step_id": 1, "source": "user", "message": "the instruction"},
            {"step_id": 2, "source": "agent",
             "tool_calls": [{"tool_call_id": "c1", "function_name":
                             "bash_command",
                             "arguments": {"keystrokes": "ls\n"}}],
             "observation": {"results": [{"content": "file list"}]}},
            # Two parse-failure rounds: no tool calls, the environment
            # answered with the recorded "Previous response had parsing
            # errors" feedback.
            {"step_id": 3, "source": "agent", "message": "not json",
             "observation": {"results": [{"content":
                 "Previous response had parsing errors:\n"
                 "ERROR: No valid JSON found in response\n"
                 "WARNINGS: - No valid JSON object found\n"}]}},
            {"step_id": 4, "source": "agent", "message": "still not json",
             "observation": {"results": [{"content":
                 "Previous response had parsing errors:\n"
                 "ERROR: Invalid JSON: Expecting property name"}]}},
            # One context-handoff system step: observation is a bare "None"
            # with nothing to render.
            {"step_id": 5, "source": "system",
             "message": "Performed context summarization and handoff.",
             "observation": "None"},
            # One other no-tool-call step with a readable observation.
            {"step_id": 6, "source": "agent",
             "observation": {"results": [{"content": "unrelated note"}]}},
        ],
    }
    steps, skipped = reconstruct(traj)
    assert steps == [{"action": 'bash_command({"keystrokes": "ls\\n"})',
                      "observation": "file list"}]
    assert skipped == 4
    assert reconstruct_skip_counts(traj) == {
        "skipped_steps": 4,
        "skipped_parse_failures": 2,
        "skipped_handoffs": 1,
        "skipped_other": 1,
    }


def test_skip_counts_of_fixture_malformed_steps_are_other():
    # The recorded fixture's two unmapped steps both carry tool_calls (one
    # with an empty observation, one with a non-list tool_calls), so they
    # fall in "other", not in the parse-failure or handoff buckets.
    counts = reconstruct_skip_counts(_traj())
    assert counts == {
        "skipped_steps": 2,
        "skipped_parse_failures": 0,
        "skipped_handoffs": 0,
        "skipped_other": 2,
    }


def test_skip_counts_never_crashes_on_garbage():
    zero = {"skipped_steps": 0, "skipped_parse_failures": 0,
            "skipped_handoffs": 0, "skipped_other": 0}
    assert reconstruct_skip_counts({}) == zero
    assert reconstruct_skip_counts({"steps": "not-a-list"}) == zero
    assert reconstruct_skip_counts({"steps": [None, 42, "x"]}) == {
        "skipped_steps": 3, "skipped_parse_failures": 0,
        "skipped_handoffs": 0, "skipped_other": 3}


def test_total_usage_reads_final_metrics():
    assert total_usage(_traj()) == (6333, 1224)


def test_total_usage_missing_metrics_is_none_never_zero():
    assert total_usage({}) == (None, None)
    assert total_usage({"final_metrics": {"total_prompt_tokens": 10}}) == (10, None)
    assert total_usage({"final_metrics": {
        "total_prompt_tokens": None, "total_completion_tokens": None,
    }}) == (None, None)
    # A bool is an int subclass; it is not a token count.
    assert total_usage({"final_metrics": {
        "total_prompt_tokens": True, "total_completion_tokens": 5,
    }}) == (None, 5)
