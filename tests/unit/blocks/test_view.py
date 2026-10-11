"""D5 view unit tests: numbering, format, truncation, and None-rules.

Pure functions, so every test is direct computation; determinism is asserted
explicitly (same inputs, same output across calls)."""

from expedistill.benchmarks.base import ViewRules
from expedistill.blocks.view import render_steps_view, render_trajectory_view
from expedistill.core.models import Step, Trajectory

TIGHT_RULES = ViewRules(
    max_obs_chars=30, head_keep=10, tail_keep=5,
    truncation_marker="[... {omitted} characters omitted ...]")


def make_traj(n_steps=2, status="failure"):
    return Trajectory(
        trajectory_id="t/0", task_id="task-7", benchmark="tb",
        model_role="weak", model_name="m", attempt_index=0,
        steps=[Step(action=f"do {i}", observation=f"saw {i}") for i in range(n_steps)],
        status=status)


def test_numbering_starts_at_zero_and_format_is_exact():
    view = render_steps_view([Step(action="ls -la", observation="file.txt")])
    assert view == "step 0\naction: ls -la\nobservation: file.txt"


def test_blank_line_between_steps_and_sequential_numbering():
    view = render_steps_view([
        Step(action="a0", observation="o0"),
        Step(action="a1", observation="o1"),
    ])
    assert view == (
        "step 0\naction: a0\nobservation: o0\n\n"
        "step 1\naction: a1\nobservation: o1")


def test_empty_steps_render_to_empty_string():
    assert render_steps_view([]) == ""


def test_no_truncation_without_rules():
    long_text = "x" * 5000
    view = render_steps_view([Step(action=long_text, observation=long_text)])
    assert long_text in view
    assert "omitted" not in view


def test_truncation_applies_marker_with_omitted_size():
    observation = "0123456789" * 10  # 100 chars, way over max_obs_chars=30
    view = render_steps_view([Step(action="cat log", observation=observation)], TIGHT_RULES)
    assert "100" not in view  # sanity: the raw length is not part of the view
    omitted = 100 - TIGHT_RULES.head_keep - TIGHT_RULES.tail_keep
    assert f"[... {omitted} characters omitted ...]" in view
    # head_keep=10 then the marker then tail_keep=5, in that order
    assert view == (
        "step 0\naction: cat log\nobservation: "
        + "0123456789"  # head_keep=10
        + "\n[... 85 characters omitted ...]\n"
        + "56789")  # tail_keep=5


def test_actions_are_truncated_with_the_same_rule():
    long_command = "echo " + "a" * 100
    view = render_steps_view([Step(action=long_command, observation="ok")], TIGHT_RULES)
    omitted = len(long_command) - TIGHT_RULES.head_keep - TIGHT_RULES.tail_keep
    assert f"[... {omitted} characters omitted ...]" in view
    assert long_command not in view


def test_short_texts_pass_through_unchanged_under_rules():
    view = render_steps_view([Step(action="ls", observation="done")], TIGHT_RULES)
    assert view == "step 0\naction: ls\nobservation: done"


def test_render_is_deterministic():
    traj = make_traj()
    first = render_trajectory_view(traj, TIGHT_RULES)
    second = render_trajectory_view(traj, TIGHT_RULES)
    assert first == second


def test_trajectory_view_header_and_steps():
    traj = make_traj(n_steps=2, status="success")
    view = render_trajectory_view(traj)
    assert view.startswith("task: task-7 (final status: success)\n\n")
    assert "step 0\naction: do 0\nobservation: saw 0" in view
    assert "step 1\naction: do 1\nobservation: saw 1" in view


def test_trajectory_view_with_rules_truncates_observations():
    traj = Trajectory(
        trajectory_id="t/1", task_id="task-8", benchmark="tb",
        model_role="strong", model_name="m", attempt_index=0,
        steps=[Step(action="run", observation="y" * 200)],
        status="failure")
    view = render_trajectory_view(traj, TIGHT_RULES)
    assert "characters omitted" in view
    assert "y" * 50 not in view


def test_trajectory_view_without_steps_is_just_the_header():
    view = render_trajectory_view(make_traj(n_steps=0))
    assert view == "task: task-7 (final status: failure)"


def test_step_dicts_are_accepted():
    view = render_steps_view([{"action": "ls", "observation": "ok"}])
    assert view == "step 0\naction: ls\nobservation: ok"
