"""K2 segment_checked composite tests: llm.fake scripts + a temporary run dir.

Covers the composite's own responsibilities (resume, retry wiring, audit on
exhaustion), not the blocks it wires (those have their own unit tests):
happy path, retry-then-success with the error preamble in the retried view,
exhaustion -> None + audit record, resume with zero model calls, G1
ModelOutputError as a failed attempt, and the K cap. AGENTS.md section 8
rule 2 (interrupt-then-resume equals one uninterrupted run) is the resume
test: the stored result of the first call is re-returned identically."""

from expedistill.composites.segment_checked import segment_checked
from expedistill.core.models import Step, Subtask, Trajectory
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm.fake import json_script, make_fake_handle

TRAJ_ID = "traj/tb:t1/strong/00"


def make_trajectory(role="strong", correctness=False, n_steps=4,
                    trajectory_id=TRAJ_ID):
    return Trajectory(
        trajectory_id=trajectory_id,
        task_id="tb:t1",
        benchmark="tb",
        model_role=role,
        model_name="fake-model",
        attempt_index=0,
        steps=[Step(action=f"a{i}", observation=f"o{i}")
               for i in range(n_steps)],
        correctness=correctness,
    )


def seg(start, end, label="seg", description="does things", success=True):
    return {"start": start, "end": end, "label": label,
            "description": description, "success": success}


def valid_payload():
    return [seg(0, 2, "setup", "prepare the environment", True),
            seg(2, 4, "run", "execute the plan", False)]


def gap_payload():
    # Schema-valid, covers everything except step 1: D7 must reject it.
    return [seg(0, 1), seg(2, 4)]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_builds_and_stores_subtasks_with_correct_fields(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=json_script(valid_payload()))
    result = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)

    assert len(result) == 2
    first, second = result
    assert isinstance(first, Subtask)
    assert first.subtask_id == f"{TRAJ_ID}/seg00"
    assert second.subtask_id == f"{TRAJ_ID}/seg01"
    assert first.task_id == "tb:t1"
    assert first.trajectory_id == TRAJ_ID
    assert first.source == "strong"
    assert first.trajectory_success is False  # correctness was False
    assert first.step_range == (0, 2)
    assert second.step_range == (2, 4)
    assert first.label == "setup"
    assert first.description == "prepare the environment"
    assert first.success is True
    assert second.success is False
    # Deterministic context join: empty for the first segment, the previous
    # segments' "label: description" entries afterwards.
    assert first.context_summary == ""
    assert second.context_summary == "setup: prepare the environment"
    # One model call on the happy path; the view is the D5 rendering.
    assert len(handle.client.calls) == 1
    assert "step 0" in handle.client.calls[0]["user"]
    # Everything is persisted under the store's subtasks kind.
    for subtask in result:
        assert store.exists("subtasks", subtask.subtask_id)
        loaded = store.load("subtasks", subtask.subtask_id, Subtask)
        assert loaded == subtask


def test_source_and_trajectory_success_follow_the_trajectory(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=json_script(valid_payload()))
    result = segment_checked(
        store=store, trajectory=make_trajectory(role="weak", correctness=True),
        K=4, view_rules=None, handle=handle, segment_retry_limit=0,
        run_dir=tmp_path)
    assert result[0].source == "weak"
    assert result[0].trajectory_success is True


# ---------------------------------------------------------------------------
# Retry then success
# ---------------------------------------------------------------------------

def test_retry_prepends_the_error_preamble_and_then_succeeds(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=[
        json_script(gap_payload()),
        json_script(valid_payload()),
    ])
    result = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)

    assert [s.subtask_id for s in result] == [f"{TRAJ_ID}/seg00",
                                             f"{TRAJ_ID}/seg01"]
    first_user = handle.client.calls[0]["user"]
    retry_user = handle.client.calls[1]["user"]
    assert "NOTE:" not in first_user
    assert "NOTE: your previous segmentation attempt failed validation" in retry_user
    assert "Re-segment the full trajectory." in retry_user
    # The D7 errors of the first attempt ride inside the preamble.
    assert "gap or overlap" in retry_user
    # The retried view still contains the full trajectory.
    assert "step 0" in retry_user


def test_context_summary_is_capped_at_500_chars(tmp_path):
    store = Store(tmp_path)
    payload = [
        seg(0, 1, "a", "x" * 300),
        seg(1, 2, "b", "y" * 300),
        seg(2, 3, "c", "z" * 10),
    ]
    handle = make_fake_handle(script=json_script(payload))
    result = segment_checked(
        store=store, trajectory=make_trajectory(n_steps=3), K=3,
        view_rules=None, handle=handle, segment_retry_limit=0,
        run_dir=tmp_path)
    assert result[2].context_summary == ("a: " + "x" * 300 + "; b: " + "y" * 300)[:500]
    assert len(result[2].context_summary) == 500


# ---------------------------------------------------------------------------
# Exhaustion: skip + audit
# ---------------------------------------------------------------------------

def test_exhaustion_returns_none_and_writes_the_audit_record(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=[
        json_script(gap_payload()),
        json_script(gap_payload()),
    ])
    result = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)

    assert result is None
    assert store.ids("subtasks") == []
    records = read_jsonl(tmp_path / "stage2_segments" / "segmentation_audit.jsonl")
    assert len(records) == 1
    record = records[0]
    assert record["trajectory_id"] == TRAJ_ID
    assert record["attempts"] == 2
    assert record["status"] == "skipped"
    assert any("gap or overlap" in error for error in record["last_errors"])


def test_exhaustion_with_run_dir_none_audits_into_the_store_run_dir(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=[json_script(gap_payload())])
    result = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=0)  # no run_dir argument
    assert result is None
    records = read_jsonl(tmp_path / "stage2_segments" / "segmentation_audit.jsonl")
    assert len(records) == 1
    assert records[0]["attempts"] == 1


# ---------------------------------------------------------------------------
# Resume (interrupt-then-resume equals one uninterrupted run)
# ---------------------------------------------------------------------------

def test_resume_returns_stored_subtasks_without_model_calls(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=json_script(valid_payload()))
    first = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)

    # A "restarted process": fresh store handle, fresh handle whose script
    # raises on any call, no run_dir needed for the resume lookup.
    store2 = Store(tmp_path)
    fresh_handle = make_fake_handle(script=[])
    second = segment_checked(
        store=store2, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=fresh_handle, segment_retry_limit=1)

    assert second == first
    assert fresh_handle.client.calls == []


def test_resume_ignores_other_trajectories_subtasks(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=json_script(valid_payload()))
    segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=0, run_dir=tmp_path)

    other = make_trajectory(correctness=False, trajectory_id="traj/tb:t1/weak/00",
                            role="weak")
    other_handle = make_fake_handle(script=json_script(valid_payload()))
    result = segment_checked(
        store=store, trajectory=other, K=4, view_rules=None,
        handle=other_handle, segment_retry_limit=0, run_dir=tmp_path)
    assert len(result) == 2
    assert result[0].subtask_id == "traj/tb:t1/weak/00/seg00"
    assert result[0].source == "weak"
    assert len(other_handle.client.calls) == 1


# ---------------------------------------------------------------------------
# ModelOutputError from G1 counts as a failed attempt
# ---------------------------------------------------------------------------

def test_model_output_error_is_a_failed_attempt_then_success(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=[
        json_script({"start": 0, "end": 2}),  # not a JSON array: G1 raises
        json_script(valid_payload()),
    ])
    result = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)

    assert [s.step_range for s in result] == [(0, 2), (2, 4)]
    retry_user = handle.client.calls[1]["user"]
    assert "previous output was not a valid segmentation response" in retry_user


def test_model_output_error_exhaustion_is_skipped_not_crashed(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=[
        json_script({"start": 0}),
        json_script([42]),
    ])
    result = segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)
    assert result is None
    records = read_jsonl(tmp_path / "stage2_segments" / "segmentation_audit.jsonl")
    assert records[0]["status"] == "skipped"
    assert records[0]["last_errors"]


# ---------------------------------------------------------------------------
# K is respected
# ---------------------------------------------------------------------------

def test_more_than_k_segments_fail_validation_until_at_most_k(tmp_path):
    store = Store(tmp_path)
    too_many = [seg(0, 2), seg(2, 4), seg(4, 6)]
    handle = make_fake_handle(script=[
        json_script(too_many),
        json_script([seg(0, 3), seg(3, 6)]),
    ])
    trajectory = make_trajectory(n_steps=6)
    result = segment_checked(
        store=store, trajectory=trajectory, K=2, view_rules=None,
        handle=handle, segment_retry_limit=1, run_dir=tmp_path)

    assert len(result) == 2  # the retry payload, which respects K=2
    retry_user = handle.client.calls[1]["user"]
    assert "exceed the maximum of K=2" in retry_user


def test_exactly_k_segments_is_accepted(tmp_path):
    store = Store(tmp_path)
    payload = [seg(0, 1, "a"), seg(1, 2, "b"), seg(2, 3, "c")]
    handle = make_fake_handle(script=json_script(payload))
    result = segment_checked(
        store=store, trajectory=make_trajectory(n_steps=3), K=3,
        view_rules=None, handle=handle, segment_retry_limit=0,
        run_dir=tmp_path)
    assert [s.segment_index for s in result] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Infrastructure failures are not segmentation failures
# ---------------------------------------------------------------------------

def test_infrastructure_error_propagates_without_audit_record(tmp_path):
    import pytest

    from expedistill.infra.reliability import InfrastructureError

    store = Store(tmp_path)
    # First round: invalid output (a failed attempt K2 owns); second round:
    # the service goes down. The outage must surface, not be counted as
    # another failed attempt or end in a "skipped" record.
    handle = make_fake_handle(script=[
        json_script(gap_payload()),
        InfrastructureError("connection reset"),
    ])
    with pytest.raises(InfrastructureError):
        segment_checked(
            store=store, trajectory=make_trajectory(), K=4, view_rules=None,
            handle=handle, segment_retry_limit=3, run_dir=tmp_path)

    assert read_jsonl(
        tmp_path / "stage2_segments" / "segmentation_audit.jsonl") == []
    assert store.ids("subtasks") == []


# ---------------------------------------------------------------------------
# Exhaustion is terminal within a run directory
# ---------------------------------------------------------------------------

def test_skipped_trajectory_is_not_reoffered_on_resume(tmp_path):
    store = Store(tmp_path)
    handle = make_fake_handle(script=[json_script(gap_payload())])
    assert segment_checked(
        store=store, trajectory=make_trajectory(), K=4, view_rules=None,
        handle=handle, segment_retry_limit=0, run_dir=tmp_path) is None

    # A later process whose model would now answer well: the frozen run
    # already decided this unit, so no call is made and nothing is stored.
    fresh = make_fake_handle(script=[json_script(valid_payload())])
    result = segment_checked(
        store=Store(tmp_path), trajectory=make_trajectory(), K=4,
        view_rules=None, handle=fresh, segment_retry_limit=0,
        run_dir=tmp_path)

    assert result is None
    assert fresh.client.calls == []
    assert Store(tmp_path).ids("subtasks") == []
    records = read_jsonl(tmp_path / "stage2_segments" / "segmentation_audit.jsonl")
    assert len(records) == 1
