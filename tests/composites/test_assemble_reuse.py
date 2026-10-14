"""assemble_reuse tests: a synthetic source run, assembled end to end.

No real run directory is read (tests/fixtures discipline): the fixture
builds a complete fake source run in tmp_path -- manifest frozen by the
REAL stage 0 over a stub benchmark, then the five object kinds, the
curated bank with mixed-lineage merges, the stage-2/3 ledgers and the
forbidden files -- and the tests assert the full-chain filter, the
identity whitelist diff, the negative list, the integrity invariants,
the run lock and the merged-original grouping the integration plan
(rulings 1/4/5, contract 6/10) pins.

The baseline level (revision 3: stage-1-only reuse, stages 2-6 fresh
under the current protocol) gets its own block below: it reuses the
same fixture but a config that deliberately differs from the source run
in rule names, skill_delivery, K and weak_profile -- allowed at this
level -- while the pinned minimum (benchmark, both executor models,
both attempt counts, train within the source task set) still refuses
loudly when it drifts.
"""

import dataclasses
import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
import urllib.parse
from pathlib import Path

import pytest

from expedistill.benchmarks.base import BenchmarkPlugin, Task, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.core.config import ExperimentConfig
from expedistill.core.models import (
    Attempt,
    Exam,
    Skill,
    Subtask,
    Trajectory,
)
from expedistill.composites.assemble_reuse import (
    assemble_reuse,
    skill_lineage_tasks,
)
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.stages import stage0_freeze
from expedistill.stages.context import StageContext

_FAKE = "asmbench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")
STRONG, WEAK = "big-model", "weak-7b"


# ---------------------------------------------------------------------------
# Stub benchmark plugin (register-at-import through the real machinery)
# ---------------------------------------------------------------------------

class _Plugin(BenchmarkPlugin):
    def __init__(self):
        self.load_specs = []

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        self.load_specs.append(tasks_spec)
        ids = list(TASK_IDS) if tasks_spec is None else list(tasks_spec)
        unknown = [i for i in ids if i not in TASK_IDS]
        if unknown:
            raise ConfigError(f"unknown task ids: {unknown}")
        return [Task(task_id=i, instruction=f"do {i}") for i in ids]

    def run(self, task, handle, skills_block, workdir):
        raise AssertionError("the assembly must never execute tasks")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "ASMB_TASK"


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry()
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()


def install_plugin():
    """Serve the stub plugin package through the real import machinery."""
    fullname = _MODULE

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(_Plugin())

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    sys.modules.pop(fullname, None)
    sys.meta_path.append(Finder())
    return fullname


# ---------------------------------------------------------------------------
# The synthetic source run
# ---------------------------------------------------------------------------

def _traj(task, role, attempt=0, meta=None):
    task_id = f"{_FAKE}:{task}"
    return Trajectory(
        trajectory_id=f"traj/{task_id}/{role}/{attempt:02d}",
        task_id=task_id,
        benchmark=_FAKE,
        model_role=role,
        model_name=STRONG if role == "strong" else WEAK,
        attempt_index=attempt,
        steps=[],
        status="success",
        meta=dict(meta or {}),
    )


def _subtask(task, role, seg, trajectory=None):
    trajectory = trajectory or _traj(task, role)
    return Subtask(
        subtask_id=f"{trajectory.trajectory_id}/seg{seg:02d}",
        task_id=trajectory.task_id,
        trajectory_id=trajectory.trajectory_id,
        source=role,
        trajectory_success=True,
        segment_index=seg,
        step_range=(0, 2),
        label=f"seg {seg}",
        description="a segment",
        success=True,
    )


def _skill(subtask_id, status, merged_from=(), content=None):
    return Skill(
        skill_id=f"{subtask_id}/skill",
        content=content or f"content of {subtask_id}",
        subtask_id=subtask_id,
        status=status,
        merged_from=list(merged_from),
    )


def _merged_skill(serial, anchor_subtask, sources, status="accepted"):
    return Skill(
        skill_id=f"bank/merge/{serial:04d}",
        content=f"merged content {serial}",
        subtask_id=anchor_subtask,
        status=status,
        merged_from=list(sources),
    )


def _exam(subtask_id):
    return Exam(
        exam_id=f"{subtask_id}/exam",
        question_text=f"question for {subtask_id}",
        reference_answer="the answer",
        subtask_id=subtask_id,
    )


def _attempt(exam_id, serial, *, purpose="bare", skill_id=None,
             skill_version=None, verdict=True):
    return Attempt(
        attempt_id=f"{exam_id}/att{serial:02d}",
        exam_id=exam_id,
        skill_id=skill_id,
        skill_version=skill_version,
        purpose=purpose,
        weak_answer="an answer",
        judge_verdict=verdict,
        judge_reason="ok",
    )


def make_source_config():
    """The source experiment: full mode over the whole task set."""
    return ExperimentConfig(
        benchmark=_FAKE,
        mode="full",
        models={"executor_strong": STRONG, "executor_weak": WEAK},
        weak_profile="a weak model",
        n_attempts_strong=1,
        n_attempts_weak=1,
        K=4,
        rules={"task_select": "all_valid_failed",
               "segment_select": "strong_all_weak_failed",
               "trajectory_select": "strong2_weak2"},
    )


REUSE_CONFIG_YAML = f"""
benchmark: {_FAKE}
mode: split
split:
  train: [t1]
  test: [t2]
  reserved: [t3]
models:
  executor_strong: {STRONG}
  executor_weak: {WEAK}
weak_profile: a weak model
n_attempts_strong: 1
n_attempts_weak: 1
K: 4
segment_retry_limit: 2
retry_limit: 2
rules:
  task_select: all_valid_failed
  segment_select: strong_all_weak_failed
  trajectory_select: strong2_weak2
skill_delivery:
  mode: retrieval
  top_k: 2
  max_categories: 2
  min_score: 0.02
"""

# The baseline-level config (revision 3): stage-1-only reuse, so it
# deliberately differs from the source run in every key that only acts
# downstream -- rule names (v2), skill_delivery, K, retry family,
# budgets, weak_profile -- and only the pinned minimum (benchmark, both
# executor models, both attempt counts) matches. A bank-level assembly
# with this config would be REFUSED on each of those keys; a baseline
# assembly must accept them all.
BASELINE_CONFIG_YAML = f"""
benchmark: {_FAKE}
mode: split
split:
  train: [t1]
  test: [t2]
  reserved: [t3]
models:
  executor_strong: {STRONG}
  executor_weak: {WEAK}
weak_profile: a completely different profile
n_attempts_strong: 1
n_attempts_weak: 1
K: 9
segment_retry_limit: 5
retry_limit: 7
bank_token_budget: 12345
curation_rounds: 1
rules:
  task_select: any_weak_failed
  segment_select: strong_success_weak_failed
  trajectory_select: strong2_weak2
skill_delivery:
  mode: retrieval
  top_k: 3
  max_categories: 2
  min_score: 0.05
"""


class SourceRun:
    """The built source run plus the ids the assertions refer to."""

    def __init__(self, run_dir, ids):
        self.run_dir = run_dir
        self.ids = ids


def build_source_run(tmp_path: Path, source_tasks=None) -> SourceRun:
    """One complete fake source run (see the module docstring).

    Task layout: t1 = train, t2 = test, t3 = reserved for the reuse
    config below. The bank deliberately contains a MIXED-LINEAGE merge
    (train anchor + reserved source) that the full-chain criterion must
    drop and the anchor-only criterion would keep.

    ``source_tasks`` restricts the source run's task set (the manifest
    is what the identity checks read): the baseline subset test needs a
    source whose task set does NOT contain everything the plugin knows,
    so a train list reaching outside the SOURCE set can be caught.
    """
    install_plugin()
    run_dir = tmp_path / "source"
    config = make_source_config()
    if source_tasks is not None:
        config = dataclasses.replace(config, tasks=list(source_tasks))
    ctx = StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={},
        workdir_root=run_dir / "work",
    )
    stage0_freeze.run(ctx)
    store = ctx.store

    trajs = {
        "t1s": _traj("t1", "strong",
                     meta={"campaign_origin_run": "archived-campaign"}),
        "t1w": _traj("t1", "weak"),
        "t2s": _traj("t2", "strong"),
        "t3s": _traj("t3", "strong"),
    }
    for traj in trajs.values():
        store.save(traj, "trajectories")

    subtasks = {
        "st1s": _subtask("t1", "strong", 0, trajs["t1s"]),
        "st1w": _subtask("t1", "weak", 0, trajs["t1w"]),
        "st1s2": _subtask("t1", "strong", 1, trajs["t1s"]),
        "st1s3": _subtask("t1", "strong", 2, trajs["t1s"]),
        "st1w2": _subtask("t1", "weak", 1, trajs["t1w"]),
        "st2s": _subtask("t2", "strong", 0, trajs["t2s"]),
        "st3s": _subtask("t3", "strong", 0, trajs["t3s"]),
    }
    for subtask in subtasks.values():
        store.save(subtask, "subtasks")

    # Originals: every status the store can hold, on both sides of the
    # split. S_C's only consumer is the mixed merge, so it must come back
    # as a DANGLING merged original.
    s_a = _skill(subtasks["st1s"].subtask_id, "accepted")
    s_b = _skill(subtasks["st1w"].subtask_id, "merged")
    s_c = _skill(subtasks["st1s2"].subtask_id, "merged")
    s_d = _skill(subtasks["st1w2"].subtask_id, "discarded",
                 content="never banked")
    s_e = _skill(subtasks["st1s3"].subtask_id, "merged")
    s_t = _skill(subtasks["st2s"].subtask_id, "accepted")
    s_r = _skill(subtasks["st3s"].subtask_id, "accepted")
    m0 = _merged_skill(0, subtasks["st1s"].subtask_id,
                       [s_b.skill_id, s_e.skill_id])
    m_mix = _merged_skill(1, subtasks["st1s2"].subtask_id,
                          [s_c.skill_id, s_r.skill_id])
    skills = [s_a, s_b, s_c, s_d, s_e, s_t, s_r, m0, m_mix]
    for skill in skills:
        store.save(skill, "skills")

    exams = {name: _exam(subtask.subtask_id)
             for name, subtask in subtasks.items()}
    for exam in exams.values():
        store.save(exam, "exams")

    attempts = [
        _attempt(exams["st1s"].exam_id, 0, purpose="bare"),
        _attempt(exams["st1s"].exam_id, 1, purpose="acceptance",
                 skill_id=s_a.skill_id, skill_version=1),
        # m0's regression evidence: one attempt per original's exam, both
        # train-side, both kept.
        _attempt(exams["st1s"].exam_id, 2, purpose="regression",
                 skill_id=m0.skill_id, skill_version=1),
        _attempt(exams["st1s3"].exam_id, 0, purpose="regression",
                 skill_id=m0.skill_id, skill_version=1),
        # The mixed merge's regression attempt: train exam, dropped skill.
        _attempt(exams["st1s2"].exam_id, 0, purpose="regression",
                 skill_id=m_mix.skill_id, skill_version=1),
        # A test-side acceptance attempt, dropped with its skill and exam.
        _attempt(exams["st2s"].exam_id, 0, purpose="acceptance",
                 skill_id=s_t.skill_id, skill_version=1),
    ]
    for attempt in attempts:
        store.save(attempt, "attempts")

    # The curated bank: stage 5's discipline is rows sorted by skill_id.
    for skill in sorted(skills, key=lambda skill: skill.skill_id):
        if skill.status == "accepted":
            store.append_bank(skill.to_dict())

    # Stage-2/3 ledgers and stage-1's accepted holes.
    store.run_dir.joinpath("stage2_segments").mkdir(exist_ok=True)
    (run_dir / "stage2_segments" / "segmentation_audit.jsonl").write_text(
        json.dumps({"trajectory_id": trajs["t1w"].trajectory_id,
                    "status": "skipped", "reason": "empty trajectory"})
        + "\n"
        + json.dumps({"trajectory_id": trajs["t3s"].trajectory_id,
                      "status": "skipped", "reason": "empty trajectory"})
        + "\n", encoding="utf-8")
    (run_dir / "stage3_generation" / "generation_failures.jsonl").write_text(
        json.dumps({"subtask_id": subtasks["st1s2"].subtask_id,
                    "error_class": "ModelOutputError", "message": "bad"})
        + "\n"
        + json.dumps({"subtask_id": subtasks["st3s"].subtask_id,
                      "error_class": "ModelOutputError", "message": "bad"})
        + "\n", encoding="utf-8")
    (run_dir / "stage1_baseline" / "accepted_infra_shortfall.json").write_text(
        json.dumps({"unit_ids": [trajs["t1s"].trajectory_id,
                                  trajs["t2s"].trajectory_id]}),
        encoding="utf-8")

    # Everything the negative list forbids, present in the SOURCE (it is a
    # finished run); the assembly must copy none of it.
    (run_dir / "pipeline_log.jsonl").write_text("[]\n", encoding="utf-8")
    (run_dir / "failed_units.jsonl").write_text("[]\n", encoding="utf-8")
    (run_dir / "skip_audit.jsonl").write_text("[]\n", encoding="utf-8")
    (run_dir / "stage1_summary.json").write_text("{}", encoding="utf-8")
    (run_dir / "stage2_summary.json").write_text("{}", encoding="utf-8")
    (run_dir / "stage3_generation" / "stage3_summary.json").write_text(
        "{}", encoding="utf-8")
    (run_dir / "stage4_acceptance" / "stage4_summary.json").write_text(
        "{}", encoding="utf-8")
    (run_dir / "stage4_acceptance" / "leak_verdicts.jsonl").write_text(
        "[]\n", encoding="utf-8")
    (run_dir / "stage4_acceptance" / "rewrite_failures.jsonl").write_text(
        "[]\n", encoding="utf-8")
    (run_dir / "stage5_curation" / "stage5_summary.json").write_text(
        "{}", encoding="utf-8")
    (run_dir / "stage5_curation" / "curation_log.json").write_text(
        "{}", encoding="utf-8")
    (run_dir / "stage6_test" / "bank").mkdir(parents=True)
    (run_dir / "stage6_test" / "bank" / "proof.json").write_text(
        "{}", encoding="utf-8")
    (run_dir / "work" / "stage1").mkdir(parents=True)
    (run_dir / "llm_requests" / "usage.jsonl").write_text(
        "[]\n", encoding="utf-8")

    return SourceRun(run_dir, {
        "s_a": s_a.skill_id, "s_b": s_b.skill_id, "s_c": s_c.skill_id,
        "s_d": s_d.skill_id, "s_e": s_e.skill_id,
        "s_t": s_t.skill_id, "s_r": s_r.skill_id,
        "m0": m0.skill_id, "m_mix": m_mix.skill_id,
        "traj": {name: traj.trajectory_id for name, traj in trajs.items()},
        "subtask": {name: subtask.subtask_id
                    for name, subtask in subtasks.items()},
        "exam": {name: exam.exam_id for name, exam in exams.items()},
    })


def write_reuse_config(tmp_path: Path, text: str = REUSE_CONFIG_YAML) -> Path:
    path = tmp_path / "reuse.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_assembles_train_lineage_with_full_chain_bank_filter(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    counts = summary["counts"]
    assert counts["trajectories"] == 2  # t1's both arms only
    assert counts["subtasks"] == 5  # every t1-side segment
    # 5 train originals (accepted, merged x2, discarded, merged) + the
    # kept merge; the MIXED merge is dropped by the full-chain criterion.
    assert counts["skills"] == 6
    assert counts["exams"] == 5
    assert counts["attempts"] == 4  # bare + s_a + m0's two regressions
    assert counts["bank_rows"] == 2  # s_a and m0 only
    assert counts["generation_failure_rows"] == 1  # train row only
    assert counts["segmentation_audit_rows"] == 2  # whole file, verbatim
    assert counts["accepted_infra_shortfall_units"] == 1  # train unit only
    assert summary["split_sizes"] == {"train": 1, "test": 1, "reserved": 1}

    # The full-chain criterion (the "42-equivalent" assertion): the mixed
    # merge has a TRAIN anchor but a reserved source, so it is in neither
    # the bank nor the skill store of the assembled run. (No Store() here:
    # it would create the directory skeleton and mask the negative-list
    # assertions below.)
    bank_ids = [row["skill_id"]
                for row in read_jsonl(run_dir / "skill_bank.jsonl")]
    assert bank_ids == [source.ids["m0"], source.ids["s_a"]]
    kept_skill_ids = sorted(
        urllib.parse.unquote(path.stem) for path in
        (run_dir / "stage3_generation" / "skills").glob("*.json"))
    assert kept_skill_ids == sorted([
        source.ids["s_a"], source.ids["s_b"], source.ids["s_c"],
        source.ids["s_d"], source.ids["s_e"], source.ids["m0"]])

    # Copied objects carry zero test/reserved lineage (asserted inside the
    # assembly; spot-checked here on the ids).
    kept_traj_ids = {path.name for path in
                     (run_dir / "stage1_baseline" / "trajectories").glob(
                         "*.json")}
    assert len(kept_traj_ids) == 2
    assert not [name for name in kept_traj_ids if "t2" in name or
                "t3" in name]

    # The negative list: nothing forbidden exists in the assembled run.
    for name in ("manifest.json", "pipeline_log.jsonl", "failed_units.jsonl",
                 "skip_audit.jsonl", "stage1_summary.json",
                 "stage2_summary.json"):
        assert not (run_dir / name).exists(), name
    assert not (run_dir / "stage3_generation" / "stage3_summary.json").exists()
    assert not (run_dir / "stage4_acceptance" / "stage4_summary.json").exists()
    assert not (run_dir / "stage4_acceptance" / "leak_verdicts.jsonl").exists()
    assert not (run_dir / "stage4_acceptance" /
                "rewrite_failures.jsonl").exists()
    assert not (run_dir / "stage5_curation").exists()
    assert not (run_dir / "stage6_test").exists()
    assert not (run_dir / "work").exists()
    assert not (run_dir / "llm_requests").exists()

    # Integrity: the kept bank rows are bit-identical to the copied skills
    # (asserted inside; the bank file is the readable evidence).
    for row in read_jsonl(run_dir / "skill_bank.jsonl"):
        path = (run_dir / "stage3_generation" / "skills"
                / (urllib.parse.quote(row["skill_id"], safe="")
                   + ".json"))
        assert read_json(path) == row

    # Stage-3 failures: only the train-side row came over.
    kept_failures = read_jsonl(
        run_dir / "stage3_generation" / "generation_failures.jsonl")
    assert [row["subtask_id"] for row in kept_failures] \
        == [source.ids["subtask"]["st1s2"]]

    # The segmentation audit is the WHOLE source file, validation-side row
    # included (ruling 5's lazy-audit exemption).
    source_audit = (source.run_dir / "stage2_segments"
                    / "segmentation_audit.jsonl").read_bytes()
    assert (run_dir / "stage2_segments"
            / "segmentation_audit.jsonl").read_bytes() == source_audit

    # Stage-1 holes: only the train-side unit carried over.
    shortfall = read_json(
        run_dir / "stage1_baseline" / "accepted_infra_shortfall.json")
    assert shortfall["unit_ids"] == [source.ids["traj"]["t1s"]]


def test_assembled_from_records_provenance_and_merged_groups(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    merged = summary["merged_originals"]
    # Train-side merged originals: s_b and s_e are consumed by the kept
    # merge m0; s_c's only consumer is the dropped mixed merge, so it
    # dangles. Counted by the assembly, never hardcoded in the tool.
    assert merged["train_merged_originals"] == 3
    assert merged["consumed_by_kept_merges"] == 2
    assert merged["dangling"] == [source.ids["s_c"]]

    text = (run_dir / "ASSEMBLED_FROM.md").read_text(encoding="utf-8")
    assert source.ids["s_c"] in text  # the dangling id is named
    assert "seed 42" in text
    assert "2fd12b88aafdd04a52c298e3940bcb189f9766d6" in text
    assert "远端任务目录的内容漂移" in text  # the digest-coverage disclaimer
    assert "全链" in text  # the full-chain criterion is stated
    # Stage-1 seeding provenance: the one campaign-seeded trajectory.
    assert "archived-campaign" in text
    # Checksums and per-class counts are in the record.
    assert summary["checksums"]["skill_bank"][:16] in text

    # The summary the CLI prints is JSON-safe and names the run pieces.
    json.dumps(summary)
    assert summary["source_run"] == str(source.run_dir)
    assert summary["assembled_from"] == "ASSEMBLED_FROM.md"
    # The identity diff: exactly the allowed keys moved (section level).
    assert summary["allowed_identity_diffs"] == [
        "config.mode", "config.skill_delivery", "config.split",
        "mode", "split"]


# ---------------------------------------------------------------------------
# Identity whitelist diff (ruling 4)
# ---------------------------------------------------------------------------

def _assemble_with_config(tmp_path, config_text):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path, config_text)
    return source, config_path, tmp_path / "assembled"


def test_identity_diff_rejects_a_changed_method_key(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, REUSE_CONFIG_YAML.replace("K: 4", "K: 9"))
    with pytest.raises(ConfigError, match="config.K"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()  # refused before anything was written


def test_identity_diff_rejects_a_changed_model_name(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, REUSE_CONFIG_YAML.replace(STRONG, "other-strong"))
    with pytest.raises(ConfigError, match="model_roles.executor_strong"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_changed_rule_name(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, REUSE_CONFIG_YAML.replace(
            "task_select: all_valid_failed", "task_select: any_weak_failed"))
    with pytest.raises(ConfigError, match="rules"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_changed_weak_profile(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, REUSE_CONFIG_YAML.replace(
            "weak_profile: a weak model", "weak_profile: another profile"))
    with pytest.raises(ConfigError, match="weak_profile"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_changed_benchmark(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, REUSE_CONFIG_YAML.replace(
            f"benchmark: {_FAKE}", "benchmark: otherbench"))
    with pytest.raises(ConfigError, match="benchmark"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_allows_only_the_whitelisted_keys(tmp_path):
    # tasks/skill_delivery/mode/split diffs are allowed; a drift on any
    # other key is not. The allowed diff list is asserted in the happy
    # path; here the reverse: dropping the tasks key (the source ran with
    # tasks=None, so no diff arises) is fine too, and the mixed-run
    # manifest without a skill_delivery key (a source frozen by pre-v2
    # code, like the real TB run) still assembles.
    source = build_source_run(tmp_path)
    manifest_path = source.run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    del manifest["config"]["skill_delivery"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config_path = write_reuse_config(tmp_path)
    summary = assemble_reuse(
        source.run_dir, config_path, tmp_path / "assembled")
    assert "config.skill_delivery" in summary["allowed_identity_diffs"]


def test_prompt_digest_drift_is_disclosed_not_refused(tmp_path):
    # The mixed-protocol declaration (ruling 14): the new run freezes the
    # CURRENT registry's digests and never calls them, so a prompt change
    # between the source run and the assembly is DISCLOSED, not refused.
    source = build_source_run(tmp_path)
    manifest_path = source.run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["prompt_digests"]["SEGMENT"] = "0" * 64
    manifest["config"]["prompts_digests"]["SEGMENT"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config_path = write_reuse_config(tmp_path)

    summary = assemble_reuse(
        source.run_dir, config_path, tmp_path / "assembled")

    assert summary["prompt_digest_drift"] == ["SEGMENT"]
    text = (tmp_path / "assembled" / "ASSEMBLED_FROM.md").read_text(
        encoding="utf-8")
    assert "SEGMENT" in text
    assert "混装声明" in text
    # The digest drift is NOT an identity diff: the method inputs all
    # still match.
    assert "config.prompts_digests" not in summary["allowed_identity_diffs"]


def test_ratio_split_reuse_config_is_refused(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(
        tmp_path, REUSE_CONFIG_YAML.replace(
            "split:\n  train: [t1]\n  test: [t2]\n  reserved: [t3]",
            "split:\n  ratio: 0.34\n  seed: 7"))
    with pytest.raises(ConfigError, match="explicit frozen split lists"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


# ---------------------------------------------------------------------------
# Refusals around the run directory
# ---------------------------------------------------------------------------

def test_refuses_run_dir_that_already_has_a_manifest(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="already contains"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_refuses_source_run_without_a_manifest(tmp_path):
    config_path = write_reuse_config(tmp_path)
    empty_source = tmp_path / "not-a-run"
    empty_source.mkdir()
    with pytest.raises(ConfigError, match="no manifest"):
        assemble_reuse(empty_source, config_path, tmp_path / "assembled")


def test_refuses_assembling_a_run_dir_onto_the_source(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    with pytest.raises(ConfigError, match="must differ"):
        assemble_reuse(source.run_dir, config_path, source.run_dir)


def test_refuses_a_locked_run_dir(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    lock = RunLock(run_dir)
    lock.acquire()
    try:
        with pytest.raises(Exception, match="locked"):
            assemble_reuse(source.run_dir, config_path, run_dir)
    finally:
        lock.release()


def test_negative_list_assertion_fires_on_a_dirty_run_dir(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    run_dir.mkdir()
    (run_dir / "pipeline_log.jsonl").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="pipeline_log.jsonl"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_reassembly_over_a_partial_copy_does_not_duplicate(tmp_path):
    # Idempotency: a failed assembly can leave files behind; re-running
    # overwrites them and rewrites the ledgers in full, so the bank never
    # grows a duplicate row.
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    assemble_reuse(source.run_dir, config_path, run_dir)
    first_bank = (run_dir / "skill_bank.jsonl").read_bytes()
    summary = assemble_reuse(source.run_dir, config_path, run_dir)
    assert (run_dir / "skill_bank.jsonl").read_bytes() == first_bank
    assert summary["counts"]["bank_rows"] == 2


# ---------------------------------------------------------------------------
# Integrity invariants over a corrupt source
# ---------------------------------------------------------------------------

def test_refuses_a_bank_row_without_a_stored_skill(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    with (source.run_dir / "skill_bank.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "skill_id": "traj/asmbench:nowhere/strong/00/seg00/skill",
            "content": "ghost", "subtask_id":
            "traj/asmbench:nowhere/strong/00/seg00", "version": 1,
            "status": "accepted", "discard_reason": None, "group": None,
            "rewrite_history": [], "merged_from": [],
            "schema_version": 1}) + "\n")
    with pytest.raises(ConfigError, match="does not store"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


def test_refuses_a_bank_row_that_differs_from_its_stored_skill(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    rows = read_jsonl(source.run_dir / "skill_bank.jsonl")
    rows[0]["content"] = rows[0]["content"] + " (edited)"
    (source.run_dir / "skill_bank.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8")
    with pytest.raises(ConfigError, match="differs from the stored skill"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


def test_refuses_a_source_whose_accepted_set_exceeds_its_bank(tmp_path):
    # An accepted train skill missing from the bank would make the
    # assembled store's accepted set larger than the filtered bank --
    # exactly the staleness the integrity invariant refuses.
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    rows = read_jsonl(source.run_dir / "skill_bank.jsonl")
    rows = [row for row in rows
            if row["skill_id"] != source.ids["s_a"]]
    (source.run_dir / "skill_bank.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8")
    with pytest.raises(ConfigError, match="accepted"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


# ---------------------------------------------------------------------------
# skill_lineage_tasks (contract 10)
# ---------------------------------------------------------------------------

def _lineage_fixture():
    subtasks = {
        "traj/b:t1/strong/00/seg00":
            Subtask(subtask_id="traj/b:t1/strong/00/seg00",
                    task_id="b:t1", trajectory_id="traj/b:t1/strong/00",
                    source="strong", trajectory_success=True,
                    segment_index=0, step_range=(0, 1), label="l",
                    description="d", success=True),
        "traj/b:t2/weak/00/seg00":
            Subtask(subtask_id="traj/b:t2/weak/00/seg00",
                    task_id="b:t2", trajectory_id="traj/b:t2/weak/00",
                    source="weak", trajectory_success=True,
                    segment_index=0, step_range=(0, 1), label="l",
                    description="d", success=True),
    }
    original_t1 = Skill(skill_id="traj/b:t1/strong/00/seg00/skill",
                        content="c", subtask_id="traj/b:t1/strong/00/seg00")
    original_t2 = Skill(skill_id="traj/b:t2/weak/00/seg00/skill",
                        content="c", subtask_id="traj/b:t2/weak/00/seg00")
    merge = Skill(skill_id="bank/merge/0000", content="c",
                  subtask_id="traj/b:t1/strong/00/seg00",
                  merged_from=[original_t1.skill_id, original_t2.skill_id])
    return subtasks, original_t1, original_t2, merge


def test_lineage_of_an_original_is_its_own_task():
    subtasks, original_t1, _, _ = _lineage_fixture()
    assert skill_lineage_tasks(original_t1, subtasks) == {"b:t1"}


def test_lineage_of_a_merge_is_the_union_of_all_originals():
    # The anchor-only view would say {b:t1}; the full chain names the
    # test-side member too (ruling 1's leak).
    subtasks, _, original_t2, merge = _lineage_fixture()
    assert skill_lineage_tasks(merge, subtasks) == {"b:t1", "b:t2"}
    assert skill_lineage_tasks(original_t2, subtasks) == {"b:t2"}


def test_lineage_resolves_nested_merges_through_skills_by_id():
    subtasks, original_t1, original_t2, merge = _lineage_fixture()
    nested = Skill(skill_id="bank/merge/0001", content="c",
                   subtask_id="traj/b:t1/strong/00/seg00",
                   merged_from=[merge.skill_id])
    skills = {merge.skill_id: merge, original_t1.skill_id: original_t1,
              original_t2.skill_id: original_t2}
    assert skill_lineage_tasks(
        nested, subtasks, skills_by_id=skills) == {"b:t1", "b:t2"}
    # Without skills_by_id a nested merged id cannot be resolved: a loud
    # ConfigError, never a silently empty (and therefore train-pure)
    # lineage.
    with pytest.raises(ConfigError, match="skills_by_id"):
        skill_lineage_tasks(nested, subtasks)


def test_lineage_of_a_skill_with_a_missing_anchor_is_refused():
    subtasks, _, _, _ = _lineage_fixture()
    orphan = Skill(skill_id="traj/b:t9/strong/00/seg00/skill",
                   content="c", subtask_id="traj/b:t9/strong/00/seg00")
    with pytest.raises(ConfigError, match="anchor subtask"):
        skill_lineage_tasks(orphan, subtasks)


def test_lineage_of_a_merge_naming_a_missing_original_is_refused():
    subtasks, _, _, _ = _lineage_fixture()
    broken = Skill(skill_id="bank/merge/0002", content="c",
                   subtask_id="traj/b:t1/strong/00/seg00",
                   merged_from=["traj/b:t1/strong/00/seg09/skill"])
    with pytest.raises(ConfigError, match="does not store"):
        skill_lineage_tasks(broken, subtasks)


# ---------------------------------------------------------------------------
# The baseline level (revision 3): stage-1-only reuse
# ---------------------------------------------------------------------------

def test_baseline_level_copies_only_trajectories_and_shortfall(tmp_path):
    source = build_source_run(tmp_path)
    # The config differs from the source run in rule names,
    # skill_delivery, K, the retry family, budgets and weak_profile; the
    # baseline level must accept every one of those (revision 3: they
    # only act on the freshly re-run downstream stages).
    config_path = write_reuse_config(tmp_path, BASELINE_CONFIG_YAML)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir,
                             level="baseline")

    assert summary["level"] == "baseline"
    assert summary["counts"] == {
        "trajectories": 2,  # t1's both arms only
        "accepted_infra_shortfall_units": 1,  # the train-side hole
    }
    assert summary["split_sizes"] == {"train": 1, "test": 1, "reserved": 1}
    assert summary["train_subset_of_source_tasks"] == {
        "train": 1, "source_tasks": 3, "outside_source": []}
    # The levels are distinguishable: the bank record's identity section
    # does not exist here (no whitelist diff at the baseline level).
    assert "allowed_identity_diffs" not in summary
    json.dumps(summary)

    # The copied trajectories are byte-identical to the source files.
    kept_paths = sorted(
        (run_dir / "stage1_baseline" / "trajectories").glob("*.json"))
    assert [urllib.parse.unquote(path.stem) for path in kept_paths] == \
        sorted([source.ids["traj"]["t1s"], source.ids["traj"]["t1w"]])
    for path in kept_paths:
        source_path = (source.run_dir / "stage1_baseline" / "trajectories"
                       / path.name)
        assert path.read_bytes() == source_path.read_bytes()

    # The train-side accepted hole carried over; the test-side one did
    # not.
    shortfall = read_json(
        run_dir / "stage1_baseline" / "accepted_infra_shortfall.json")
    assert shortfall["unit_ids"] == [source.ids["traj"]["t1s"]]

    # Zero downstream copy: none of the four downstream kind directories
    # exists, nor do the bank, the stage-2/3 ledgers or anything on the
    # shared negative list (the assembly asserted the same inside).
    for kind_dir in ("stage2_segments/subtasks", "stage3_generation/skills",
                     "stage3_generation/exams",
                     "stage4_acceptance/attempts"):
        assert not (run_dir / kind_dir).exists(), kind_dir
    for name in ("skill_bank.jsonl", "manifest.json", "pipeline_log.jsonl",
                 "failed_units.jsonl", "skip_audit.jsonl",
                 "stage1_summary.json", "stage2_summary.json"):
        assert not (run_dir / name).exists(), name
    assert not (run_dir / "stage2_segments").exists()
    assert not (run_dir / "stage3_generation").exists()
    assert not (run_dir / "stage4_acceptance").exists()
    assert not (run_dir / "stage5_curation").exists()
    assert not (run_dir / "stage6_test").exists()
    assert not (run_dir / "work").exists()
    assert not (run_dir / "llm_requests").exists()

    # The baseline provenance record: the fresh-run declaration, the
    # subset proof and the trajectory provenance (campaign seeding
    # included -- the trajectories are the one artifact this level
    # carries, so their origin is load-bearing).
    text = (run_dir / "ASSEMBLED_FROM.md").read_text(encoding="utf-8")
    assert "baseline 档位" in text
    assert "阶段 2–6 将按当前协议全新执行" in text
    assert "⊆ 源任务集" in text
    assert "archived-campaign" in text
    assert summary["checksums"]["trajectories"][:16] in text


def test_baseline_refuses_a_changed_executor_model(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, BASELINE_CONFIG_YAML.replace(WEAK, "other-weak"))
    with pytest.raises(ConfigError, match="executor_weak"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()  # refused before anything was written


def test_baseline_refuses_changed_n_attempts(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, BASELINE_CONFIG_YAML.replace("n_attempts_weak: 1",
                                               "n_attempts_weak: 2"))
    with pytest.raises(ConfigError, match="n_attempts_weak"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()


def test_baseline_refuses_a_changed_benchmark(tmp_path):
    source, config_path, run_dir = _assemble_with_config(
        tmp_path, BASELINE_CONFIG_YAML.replace(
            f"benchmark: {_FAKE}", "benchmark: otherbench"))
    with pytest.raises(ConfigError, match="otherbench"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()


def test_baseline_refuses_train_outside_the_source_task_set(tmp_path):
    # A source over a restricted task set, then a train list reaching
    # outside it: the trajectories to copy would not exist in the source
    # run, so the subset pin refuses before any copy.
    source = build_source_run(tmp_path, source_tasks=["t1", "t2"])
    config_path = write_reuse_config(
        tmp_path, BASELINE_CONFIG_YAML.replace(
            "train: [t1]\n  test: [t2]\n  reserved: [t3]",
            "train: [t3]\n  test: [t1]\n  reserved: []"))
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match="does not contain"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()


def test_baseline_skips_a_missing_shortfall_file(tmp_path):
    source = build_source_run(tmp_path)
    (source.run_dir / "stage1_baseline"
     / "accepted_infra_shortfall.json").unlink()
    config_path = write_reuse_config(tmp_path, BASELINE_CONFIG_YAML)
    summary = assemble_reuse(
        source.run_dir, config_path, tmp_path / "assembled",
        level="baseline")
    assert summary["counts"] == {
        "trajectories": 2, "accepted_infra_shortfall_units": 0}
    assert not (tmp_path / "assembled" / "stage1_baseline"
                / "accepted_infra_shortfall.json").exists()


def test_rejects_an_unknown_level(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    with pytest.raises(ConfigError, match="level must be one of"):
        assemble_reuse(source.run_dir, config_path,
                       tmp_path / "assembled", level="nope")


def test_cli_passes_the_level_only_when_requested(monkeypatch, tmp_path):
    # --level baseline flows through to the composite; the default call
    # keeps the historical three-argument shape (no level kwarg), so a
    # stub of the original signature keeps working for bank assemblies.
    from expedistill import cli

    calls = {}

    def fake_assemble_reuse(**kwargs):
        calls.clear()
        calls.update(kwargs)
        return {"level": kwargs.get("level", "bank"), "counts": {}}

    monkeypatch.setattr(cli, "assemble_reuse", fake_assemble_reuse)
    base = ["assemble-reuse",
             "--source-run", str(tmp_path / "source"),
             "--config", str(tmp_path / "cfg.yaml"),
             "--run-dir", str(tmp_path / "run")]
    assert cli.main(base) == 0
    assert "level" not in calls
    assert cli.main(base + ["--level", "baseline"]) == 0
    assert calls["level"] == "baseline"
    assert cli.main(base + ["--level", "bank"]) == 0
    assert "level" not in calls


def test_cli_assemble_reuse_baseline_end_to_end(capsys, tmp_path):
    # The REAL assemble_reuse through the CLI at the baseline level: the
    # synthetic source run assembled into a fresh, stage-1-only run.
    from expedistill import cli

    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path, BASELINE_CONFIG_YAML)
    run_dir = tmp_path / "assembled"
    code = cli.main([
        "assemble-reuse",
        "--source-run", str(source.run_dir),
        "--config", str(config_path),
        "--run-dir", str(run_dir),
        "--level", "baseline",
    ])
    captured = capsys.readouterr()
    assert code == 0
    line = captured.out.splitlines()[-1]
    assert line.startswith("assemble-reuse: ")
    summary = json.loads(line[len("assemble-reuse: "):])
    assert summary["level"] == "baseline"
    assert summary["counts"]["trajectories"] == 2
    assert (run_dir / "ASSEMBLED_FROM.md").is_file()
    assert not (run_dir / "manifest.json").exists()
    assert not (run_dir / "skill_bank.jsonl").exists()


# ---------------------------------------------------------------------------
# Acceptance-protocol combinations (decision doc 2026-10-14, ruling A15)
# ---------------------------------------------------------------------------

#: A live_run target block (the first TB live config's shape, section 2.1):
#: the criterion is pre-registered by name, the depth is the user-agreed 3.
_LIVE_ACCEPTANCE_YAML = """
acceptance:
  mode: live_run
  n_attempts: 3
  improvement: strict_rate_gain
"""

_EXPLICIT_EXAM_YAML = """
acceptance:
  mode: exam
"""


def build_live_source_run(tmp_path: Path) -> SourceRun:
    """The exam fixture's full source run, re-frozen under a live_run
    acceptance config: every object file is copied verbatim from the exam
    source and only the manifest is frozen by the real stage 0 over the
    live config (the identity walk reads the manifest; the copy keeps the
    fixture a finished run, not a bare manifest)."""
    import shutil

    source = build_source_run(tmp_path)
    run_dir = tmp_path / "live-source"
    for item in source.run_dir.iterdir():
        if item.is_dir():
            shutil.copytree(item, run_dir / item.name)
        elif item.name != "manifest.json":
            shutil.copy2(item, run_dir / item.name)
    config = dataclasses.replace(
        make_source_config(),
        acceptance={"mode": "live_run", "n_attempts": 3,
                   "improvement": "strict_rate_gain"})
    ctx = StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={},
        workdir_root=run_dir / "work",
    )
    stage0_freeze.run(ctx)
    return SourceRun(run_dir, source.ids)


@pytest.mark.parametrize("acceptance_yaml", [
    "",  # no acceptance key at all: the frozen v1 exam target
    _LIVE_ACCEPTANCE_YAML,
], ids=["exam", "live_run"])
def test_baseline_level_allows_exam_and_live_targets(
        tmp_path, acceptance_yaml):
    """Stage-1-only reuse re-runs stages 2-6 fresh, so the acceptance
    protocol (a stage-4 key) may differ from the source run in either
    direction: the pinned minimum at this level never walks it."""
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(
        tmp_path, BASELINE_CONFIG_YAML + acceptance_yaml)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir,
                             level="baseline")

    assert summary["level"] == "baseline"
    assert summary["counts"] == {
        "trajectories": 2, "accepted_infra_shortfall_units": 1}


def test_bank_level_refuses_an_exam_to_live_target(tmp_path):
    """The bank's acceptance protocol identity cannot be swapped: the
    copied skills/exams were accepted under the source run's exam, and a
    live_run target would re-freeze a different stage-4 protocol over
    them."""
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(
        tmp_path, REUSE_CONFIG_YAML + _LIVE_ACCEPTANCE_YAML)
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match=r"config\.acceptance"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()


def test_bank_level_refuses_a_live_to_exam_target(tmp_path):
    """The reverse direction is refused by the same identity walk: a
    live-accepted bank cannot be re-frozen as an exam-accepted one."""
    source = build_live_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path, REUSE_CONFIG_YAML)
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match=r"config\.acceptance"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()


def test_bank_level_refuses_an_explicit_exam_acceptance_target(tmp_path):
    """The conservative corner (ruling A15): the source ran the default
    exam and the reuse config says the same thing explicitly -- same
    protocol, but the frozen manifest's KEY SET differs (the source's
    config carries no acceptance key, the target's carries {mode: exam}),
    and the identity walk refuses on the key, never guessing at semantic
    equivalence."""
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(
        tmp_path, REUSE_CONFIG_YAML + _EXPLICIT_EXAM_YAML)
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match=r"config\.acceptance"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()


def test_bank_level_live_to_live_reuse_discloses_no_live_digest_drift(
        tmp_path):
    """Same-acceptance live->live reuse goes through, and the two G10/G11
    prompt digests are on BOTH sides (assemble_reuse's digest-copy
    syncs with stage 0's live branch), so the drift disclosure reports no
    "prompt set" drift -- without the sync it would misreport the whole
    live prompt pair as config drift."""
    source = build_live_source_run(tmp_path)
    config_path = write_reuse_config(
        tmp_path, REUSE_CONFIG_YAML + _LIVE_ACCEPTANCE_YAML)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    assert summary["level"] == "bank"
    assert "LIVE_RUN_ATTRIBUTION" not in summary["prompt_digest_drift"]
    assert "LIVE_SKILL_REVISION" not in summary["prompt_digest_drift"]
    assert summary["prompt_digest_drift"] == []
    # The no-drift assertion is not vacuous: the source manifest really
    # froze the two live digests (the live branch of the digest copy), so
    # both sides of the comparison carried them.
    source_manifest = read_json(source.run_dir / "manifest.json")
    from expedistill import prompts
    assert source_manifest["prompt_digests"][
        "LIVE_RUN_ATTRIBUTION"] == prompts.digest("LIVE_RUN_ATTRIBUTION")
    assert source_manifest["prompt_digests"][
        "LIVE_SKILL_REVISION"] == prompts.digest("LIVE_SKILL_REVISION")
