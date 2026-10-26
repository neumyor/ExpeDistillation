"""The cluster-by-trigger authoring driver: classify, cluster, write."""

import pytest

from expedistill.core.ids import (
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Subtask
from expedistill.infra.reliability import ModelOutputError
from expedistill.methods.author import ClusterAuthoringParams
from expedistill.methods.author import cluster_by_trigger as cluster


def _subtask(segment: int, *, source: str = "strong",
             success: bool = True) -> Subtask:
    trajectory_id = make_trajectory_id(make_task_id("tb", "t1"), source, 0)
    subtask_id = make_subtask_id(trajectory_id, segment)
    return Subtask(
        subtask_id=subtask_id,
        task_id=make_task_id("tb", "t1"),
        trajectory_id=trajectory_id,
        source=source,
        trajectory_success=success,
        segment_index=segment,
        step_range=(segment * 2, segment * 2 + 2),
        label=f"segment {segment}",
        description=f"do the thing at segment {segment}",
        success=success,
        context_summary="the previous segment did setup",
    )


def _experience(segment: int, **kwargs):
    return {
        "subtask": _subtask(segment, **kwargs),
        "steps_view": f"step 0\naction: act {segment}\nobservation: ok",
        "mode": "distill" if kwargs.get("success", True) else "diagnose",
        "segment_position": f"segment {segment + 1} of 3",
    }


def _sections(when="When a command fails, hangs, or repeats, treat it"):
    return {
        "when_to_use": when,
        "steps": ["When the error appears, read it; verify the cause."],
        "not_applicable_when": "the command succeeded",
        "summary": "read the error before retrying",
    }


def test_build_card_keeps_identity_and_clips_steps():
    experience = _experience(0)
    experience["steps_view"] = "x" * 500
    card = cluster.build_card(experience, 80)
    assert "SUBTASK_ID:" in card
    assert "TASK:" in card
    assert "steps elided" in card


def test_classify_trigger_returns_one_of_the_five_families(monkeypatch):
    monkeypatch.setattr(
        cluster, "call_model",
        lambda *args, **kwargs: {
            "family": "when_command_fails",
            "reason": "the command errored",
        })
    result = cluster.classify_trigger(
        _experience(0), handle=object(), max_chars=200)
    assert result == {
        "family": "when_command_fails",
        "reason": "the command errored",
    }


def test_classify_trigger_rejects_an_unknown_family(monkeypatch):
    monkeypatch.setattr(
        cluster, "call_model",
        lambda *args, **kwargs: {"family": "invented", "reason": "x"})
    with pytest.raises(ModelOutputError, match="unknown family"):
        cluster.classify_trigger(
            _experience(0), handle=object(), max_chars=200)


def test_cluster_family_builds_deterministic_cluster_ids(monkeypatch):
    def fake_call(handle, name, variables, **kwargs):
        assert name == "CLUSTER_SUBTASKS"
        return {"clusters": [
            {"theme": "read the error first",
             "subtask_ids": [item["subtask"].subtask_id
                             for item in experiences][:2],
             "reason": "both read an error"},
            {"theme": "check the environment",
             "subtask_ids": [experiences[2]["subtask"].subtask_id],
             "reason": "an outlier"},
        ]}

    experiences = [_experience(i) for i in range(3)]
    monkeypatch.setattr(cluster, "call_model", fake_call)
    clusters = cluster.cluster_family(
        "when_command_fails", experiences, handle=object(),
        params=ClusterAuthoringParams(max_cards_per_prompt=3))
    assert [c["cluster_id"] for c in clusters] == sorted(
        [c["cluster_id"] for c in clusters])
    assert clusters[0]["family"] == "when_command_fails"
    assert sorted(
        [item for c in clusters for item in c["subtask_ids"]]) == sorted(
        [experience["subtask"].subtask_id for experience in experiences])


def test_cluster_family_falls_back_to_singletons_on_invalid_partition(
        monkeypatch):
    monkeypatch.setattr(
        cluster, "call_model",
        lambda *args, **kwargs: {"clusters": [
            {"theme": "only one", "subtask_ids": [
                _experience(0)["subtask"].subtask_id],
             "reason": "partial"}]})
    experiences = [_experience(i) for i in range(2)]
    clusters = cluster.cluster_family(
        "when_command_fails", experiences, handle=object(),
        params=ClusterAuthoringParams(
            max_cards_per_prompt=10, cluster_retry_limit=1))
    assert len(clusters) == 2
    assert all(cluster.get("fallback") for cluster in clusters)
    assert all(len(cluster["subtask_ids"]) == 1 for cluster in clusters)


def test_write_cluster_skill_uses_the_smallest_subtask_as_anchor(
        monkeypatch):
    experiences = [_experience(0), _experience(1)]
    monkeypatch.setattr(
        cluster, "call_model",
        lambda *args, **kwargs: _sections())
    cluster_row = {
        "family": "when_command_fails",
        "theme": "read the error before retrying",
        "reason": "both read an error",
        "subtask_ids": sorted(
            experience["subtask"].subtask_id for experience in experiences),
    }
    by_id = {experience["subtask"].subtask_id: experience
             for experience in experiences}
    skill = cluster.write_cluster_skill(
        cluster_row, by_id, weak_profile="", handle=object(),
        params=ClusterAuthoringParams())
    assert skill.subtask_id == min(cluster_row["subtask_ids"])
    assert skill.skill_id == f"{skill.subtask_id}/skill"
    assert skill.source_subtasks == cluster_row["subtask_ids"]
    assert skill.status == "pending"
    assert skill.version == 1
    assert "[When to use]" in skill.content
    assert skill.summary == "read the error before retrying"


def test_write_cluster_skill_requires_the_family_opening_phrase(monkeypatch):
    monkeypatch.setattr(
        cluster, "call_model",
        lambda *args, **kwargs: _sections(when="At task start, do it"))
    experience = _experience(0)
    cluster_row = {
        "family": "when_command_fails",
        "theme": "read the error",
        "reason": "an error",
        "subtask_ids": [experience["subtask"].subtask_id],
    }
    with pytest.raises(ModelOutputError, match="must start with"):
        cluster.write_cluster_skill(
            cluster_row,
            {experience["subtask"].subtask_id: experience},
            weak_profile="", handle=object(),
            params=ClusterAuthoringParams())
