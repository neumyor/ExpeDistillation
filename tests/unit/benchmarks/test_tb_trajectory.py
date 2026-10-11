"""Tests for benchmarks/tb/trajectory.py against the recorded fixture shape
(tests/fixtures/tb/trajectory.json): pair mapping, malformed-step skipping,
garbage-input safety, and Harbor usage totals."""

import json
from pathlib import Path

from expedistill.benchmarks.tb.trajectory import (
    reconstruct,
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
