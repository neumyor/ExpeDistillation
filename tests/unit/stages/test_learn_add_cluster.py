"""P02's cluster-by-trigger landing: artifacts, resume and fallbacks."""

from types import SimpleNamespace

from expedistill.core.contracts import TaskListRow
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.store import read_json, read_jsonl
from expedistill.methods.author import ClusterAuthoringParams
from expedistill.methods.review.contracts import ReviewItem
from expedistill.stages import learn_add, learn_add_cluster

TASK_A = make_task_id("tb", "t1")
TASK_B = make_task_id("tb", "t2")


class _Store:
    def __init__(self):
        self.saved = {}
        self.trajectories = {}

    def exists(self, kind, object_id):
        return object_id in self.saved

    def ids(self, kind):
        return sorted(self.trajectories) if kind == "trajectories" else []

    def load(self, kind, object_id, cls=None):
        if kind == "trajectories":
            return self.trajectories[object_id]
        return self.saved[object_id]

    def save(self, obj, kind):
        self.saved[obj.skill_id] = obj


def _record_run(store, task_id, role, success, attempt=0):
    store.trajectories[make_trajectory_id(task_id, role, attempt)] = (
        SimpleNamespace(task_id=task_id, model_role=role,
                        correctness=success))


def _subtask(task_id, segment, source="strong", success=True):
    trajectory = make_trajectory_id(task_id, source, 0)
    subtask_id = make_subtask_id(trajectory, segment)
    return SimpleNamespace(
        subtask_id=subtask_id, task_id=task_id, trajectory_id=trajectory,
        source=source, trajectory_success=success, segment_index=segment,
        step_range=(0, 1), label=f"segment {segment}",
        description=f"do segment {segment}", success=success,
        context_summary="")


def _experience(task_id, segment, **kwargs):
    subtask = _subtask(task_id, segment, **kwargs)
    return {
        "subtask": subtask,
        "steps_view": f"action: do {segment}\nobservation: ok",
        "mode": "distill" if subtask.success else "diagnose",
        "segment_position": f"segment {segment + 1}",
    }


def _ctx(tmp_path, store):
    return SimpleNamespace(
        store=store,
        run_dir=tmp_path / "run",
        usage=None,
        config=SimpleNamespace(
            benchmark="tb",
            rules={"trajectory_select": "strong1_weak1",
                   "segment_select": "strong_success_weak_failed"},
            effective_weak_profile=lambda: ""),
        run_settings=SimpleNamespace(workers=1),
        handle=lambda role: SimpleNamespace(role=role))


def _phase():
    return SimpleNamespace(
        label="p02:learn",
        spec=SimpleNamespace(author={"name": "cluster_by_trigger"}))


def _row(task_id):
    return TaskListRow(target_kind="task", target_id=task_id,
                       action="add", reason="weak fails, no skill yet",
                       basis_unit="u")


def _item(task_id, experiences):
    package = SimpleNamespace(
        target=task_id, facts=[], segments=[],
        source_experiences=list(experiences))
    return ReviewItem(row=_row(task_id), evidence=package)


def _install(monkeypatch):
    calls = {"classify": 0, "cluster": 0, "write": 0}

    def classify(experience, **kwargs):
        calls["classify"] += 1
        return {"family": "when_command_fails", "reason": "it errored"}

    def cluster(family, experiences, **kwargs):
        calls["cluster"] += 1
        ids = sorted(item["subtask"].subtask_id for item in experiences)
        return [{
            "family": family,
            "cluster_id": learn_add_cluster.author.cluster_id_for(
                family, ids),
            "theme": "read the error before retrying",
            "reason": "all members read an error",
            "subtask_ids": ids,
        }]

    def write(cluster_row, experiences_by_id, **kwargs):
        calls["write"] += 1
        ids = sorted(cluster_row["subtask_ids"])
        return Skill(
            skill_id=make_skill_id(ids[0]), content="[When to use] x",
            subtask_id=ids[0], source_subtasks=ids,
            summary="read the error first", version=1, status="pending")

    monkeypatch.setattr(learn_add_cluster.author, "classify_trigger",
                        classify)
    monkeypatch.setattr(learn_add_cluster.author, "cluster_family", cluster)
    monkeypatch.setattr(learn_add_cluster.author, "write_cluster_skill",
                        write)
    return calls


def test_cluster_path_writes_one_skill_and_its_artifacts(
        monkeypatch, tmp_path):
    store = _Store()
    ctx = _ctx(tmp_path, store)
    phase = _phase()
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    experiences_a = [_experience(TASK_A, 0), _experience(TASK_A, 1)]
    experiences_b = [_experience(TASK_B, 0)]
    _record_run(store, TASK_A, "strong", True)
    _record_run(store, TASK_B, "strong", True)
    calls = _install(monkeypatch)

    failed = learn_add_cluster.execute_add_rows(
        ctx, phase, [_item(TASK_A, experiences_a),
                     _item(TASK_B, experiences_b)], phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))

    assert failed == set()
    assert len(store.saved) == 1
    skill = next(iter(store.saved.values()))
    assert len(skill.source_subtasks) == 3
    assert skill.status == "pending"
    assert calls == {"classify": 3, "cluster": 1, "write": 1}

    triggers = read_jsonl(phase_dir / "trigger_families.jsonl")
    assert len(triggers) == 3
    clusters = read_jsonl(phase_dir / "clusters.jsonl")
    assert len(clusters) == 1
    assert clusters[0]["subtask_ids"] == sorted(skill.source_subtasks)
    view = (phase_dir / "clusters" / "when_command_fails.md").read_text(
        encoding="utf-8")
    assert "read the error before retrying" in view
    outcomes = read_jsonl(phase_dir / "cluster_outcomes.jsonl")
    assert [row["outcome"] for row in outcomes] == ["added"]
    summary = read_json(phase_dir / "cluster_summary.json")
    assert summary["skills"] == 1
    assert summary["outcomes"] == {"added": 1}
    assert summary["uncovered_add_tasks"] == [{
        "task_id": TASK_B,
        "add_index": 2,
        "eligible": False,
        "strong_success": True,
        "weak_failure": False,
        "absorbed_by": [clusters[0]["cluster_id"]],
    }]


def test_a_completed_cluster_is_not_reclassified_or_rewritten(
        monkeypatch, tmp_path):
    store = _Store()
    ctx = _ctx(tmp_path, store)
    phase = _phase()
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    experiences_a = [_experience(TASK_A, 0), _experience(TASK_A, 1)]
    calls = _install(monkeypatch)
    items = [_item(TASK_A, experiences_a)]

    learn_add_cluster.execute_add_rows(
        ctx, phase, items, phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))
    assert calls == {"classify": 2, "cluster": 1, "write": 1}

    learn_add_cluster.execute_add_rows(
        ctx, phase, items, phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))

    assert calls == {"classify": 2, "cluster": 1, "write": 1}
    assert len(read_jsonl(phase_dir / "cluster_outcomes.jsonl")) == 1


def test_a_missing_receipt_is_recovered_as_skipped_existing(
        monkeypatch, tmp_path):
    store = _Store()
    ctx = _ctx(tmp_path, store)
    phase = _phase()
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    experiences_a = [_experience(TASK_A, 0), _experience(TASK_A, 1)]
    calls = _install(monkeypatch)
    items = [_item(TASK_A, experiences_a)]

    learn_add_cluster.execute_add_rows(
        ctx, phase, items, phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))
    # Simulate a crash after store.save but before the outcome receipt.
    (phase_dir / "cluster_outcomes.jsonl").unlink()
    calls_before = dict(calls)

    learn_add_cluster.execute_add_rows(
        ctx, phase, items, phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))

    assert calls == calls_before  # the cluster is read from disk, not re-asked
    outcomes = read_jsonl(phase_dir / "cluster_outcomes.jsonl")
    assert [row["outcome"] for row in outcomes] == ["skipped_existing"]


def test_a_no_material_task_gets_the_old_skipped_receipt(
        monkeypatch, tmp_path):
    store = _Store()
    ctx = _ctx(tmp_path, store)
    phase = _phase()
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    _install(monkeypatch)

    learn_add_cluster.execute_add_rows(
        ctx, phase, [ReviewItem(row=_row(TASK_A), evidence=None)],
        phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))

    outcomes = read_jsonl(phase_dir / "outcomes.jsonl")
    assert [row["outcome"] for row in outcomes] == ["skipped_no_material"]
    assert not store.saved


def test_learn_add_dispatches_the_new_author_method(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(
        learn_add.method_registry, "resolve",
        lambda family, raw: (object(), ClusterAuthoringParams()))
    monkeypatch.setattr(
        learn_add_cluster, "execute_add_rows",
        lambda ctx, phase, items, phase_dir, author_params=None,
        skip_targets=(), source_phase_label="": (
            called.append((len(items), author_params)) or set()))
    ctx = SimpleNamespace(run_dir=tmp_path / "run")
    phase = _phase()
    item = _item(TASK_A, [])

    failed = learn_add.execute_add_rows(ctx, phase, [item])

    assert failed == set()
    assert called and called[0][0] == 1
    assert (tmp_path / "run" / "phases" / "p02_learn"
            / "task_list.jsonl").exists()


def test_uncovered_add_task_marks_eligible_runs(monkeypatch, tmp_path):
    store = _Store()
    ctx = _ctx(tmp_path, store)
    phase = _phase()
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    experiences_a = [_experience(TASK_A, 0)]
    experiences_b = [_experience(TASK_B, 0),
                     _experience(TASK_B, 1, source="weak", success=False)]
    _record_run(store, TASK_A, "strong", True)
    _record_run(store, TASK_B, "strong", True)
    _record_run(store, TASK_B, "weak", False)
    _install(monkeypatch)
    items = [_item(TASK_A, experiences_a), _item(TASK_B, experiences_b)]

    learn_add_cluster.execute_add_rows(
        ctx, phase, items, phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))

    summary = read_json(phase_dir / "cluster_summary.json")
    uncovered = summary["uncovered_add_tasks"]
    assert len(uncovered) == 1
    assert uncovered[0]["task_id"] == TASK_B
    assert uncovered[0]["add_index"] == 2
    assert uncovered[0]["eligible"] is True
    assert uncovered[0]["strong_success"] is True
    assert uncovered[0]["weak_failure"] is True
    assert uncovered[0]["absorbed_by"]


def test_uncovered_eligibility_reads_all_baseline_runs(
        monkeypatch, tmp_path):
    """A weak failure whose segments were all cut away still counts."""
    store = _Store()
    ctx = _ctx(tmp_path, store)
    phase = _phase()
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    experiences_a = [_experience(TASK_A, 0)]
    experiences_b = [_experience(TASK_B, 0)]
    _record_run(store, TASK_A, "strong", True)
    _record_run(store, TASK_B, "strong", True)
    _record_run(store, TASK_B, "weak", False)
    _install(monkeypatch)
    items = [_item(TASK_A, experiences_a), _item(TASK_B, experiences_b)]

    learn_add_cluster.execute_add_rows(
        ctx, phase, items, phase_dir,
        author_params=ClusterAuthoringParams(max_chars_per_card=800))

    uncovered = read_json(phase_dir / "cluster_summary.json")[
        "uncovered_add_tasks"]
    assert len(uncovered) == 1
    assert uncovered[0]["task_id"] == TASK_B
    assert uncovered[0]["eligible"] is True
    assert uncovered[0]["weak_failure"] is True
