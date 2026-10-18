"""assemble_reuse tests: a synthetic source run, assembled end to end.

No real run directory is read (tests/fixtures discipline): the fixture
builds a complete fake source run in tmp_path -- manifest frozen by the
REAL stage 0 over a stub benchmark, then the five object kinds, the
curated bank with mixed-lineage merges, the accepted-holes record and
the forbidden files -- and the tests assert the full-chain filter, the
identity whitelist diff, the negative list, the integrity invariants,
the run lock and the merged-original grouping the integration plan
(rulings 1/4/5, contract 6/10) pins.

Schema worlds (R10): the default fixture is a v4 PHASE-world source
(the bank is the last learn phase's artifact under phases/); a second
fixture re-shapes the same run into the v3 STAGE-world spelling (the
config's methods slots, the run-root bank, the stage-2/3 ledgers) so
both read paths are covered. The assembled product is always v4: the
filtered bank lands at the run root AND pre-seeded as the last learn
phase's artifact, and one test drives the REAL phase pipeline
(cli.run_experiment with refusing fake clients) over the assembled run
to pin the v4 resume contract -- every phase through the last learn
phase completes with ZERO model calls and the artifact byte-identical.

The baseline level (revision 3: the D phase's trajectories only, every
downstream phase fresh under the current protocol) gets its own block
below: it reuses the same fixture but a config that deliberately
differs from the source run in rule names, the phase definitions, K and
weak_profile -- allowed at this level -- while the pinned minimum
(benchmark, both executor models, both attempt counts, train within
the source task set) still refuses loudly when it drifts.
"""

import copy
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
import yaml

from expedistill.benchmarks.base import BenchmarkPlugin, Task, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.cli import run_experiment
from expedistill.composites import phase_bank, verdicts
from expedistill.composites.phase_bank import phase_bank_dir
from expedistill.composites.skill_status import stage3_candidates
from expedistill.core.config import (
    LearnPhaseSpec,
    ExperimentConfig,
    load_config,
    phase_label,
    upgrade_v3_config,
)
from expedistill.core import ids
from expedistill.core.models import (
    Attempt,
    Exam,
    Skill,
    Subtask,
    Trajectory,
)
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import Store, read_json, read_jsonl, write_json
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import FakeClient, json_script
from expedistill.stages import stage0_freeze
from expedistill.stages.assemble_reuse import (
    assemble_reuse,
    skill_lineage_tasks,
)
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
    # The resume test drives the real D phase, which judges stored runs
    # under the benchmark's default verdict rule (the w2a pattern).
    verdicts.BENCHMARK_VERDICT_RULES.setdefault(_FAKE, "scorer")
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.pop(_FAKE, None)


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
# The phase shapes (the exam method's [D, L] + [A, L] x 3 + [C] and the
# live method's [D, L, B, L, B, L, C], exactly what upgrade_v3_config
# emits for the corresponding v3 acceptance slots)
# ---------------------------------------------------------------------------

EXAM_SEQUENCE = ["D", "L", "A", "L", "A", "L", "A", "L", "C"]
LIVE_SEQUENCE = ["D", "L", "B", "L", "B", "L", "C"]

_LEARN_EXAM = {
    "kind": "learn",
    "evidence": {"name": "segment", "K": 4, "retry_limit": 2},
    "author": {"name": "distill_diagnose"},
    "curate": {"name": "agentbrew", "merge_check": "exam",
               "token_budget": 4096, "rounds": 3},
}
_LEARN_LIVE = {
    "kind": "learn",
    "evidence": {"name": "segment", "K": 4, "retry_limit": 2},
    "author": {"name": "distill_diagnose"},
    # A live sequence has no exam phase, so its merges are proven by the
    # round-1 group re-execution (the upgrade's mapping for live_run).
    "curate": {"name": "agentbrew", "merge_check": "none",
               "token_budget": 4096, "rounds": 3},
}

EXAM_PHASES = {
    "D": {"kind": "execute", "role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]}},
    "L": dict(_LEARN_EXAM),
    "A": {"kind": "execute", "role": "exam",
          "delivery": {"name": "per_skill"},
          "execution": {"name": "exam"}},
    "C": {"kind": "execute", "role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "D"}},
}

LIVE_PHASES = {
    "D": dict(EXAM_PHASES["D"]),
    "L": dict(_LEARN_LIVE),
    "B": {"kind": "execute", "role": "live", "tasks": "train",
          "delivery": {"name": "group_by_task"},
          "execution": {"name": "real", "n_attempts": 3},
          "compare": {"against": "D", "rule": "strict_rate_gain"}},
    "C": {"kind": "execute", "role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "D"}},
}


# ---------------------------------------------------------------------------
# Config builders (the source is constructed directly -- a parsed v4
# ExperimentConfig freezes the way a loaded one does; the reuse target
# goes through YAML + load_config, the way the CLI reads it)
# ---------------------------------------------------------------------------

_RULES = {"task_select": "all_valid_failed",
          "segment_select": "strong_all_weak_failed",
          "trajectory_select": "strong2_weak2"}


def make_source_config(*, live=False, n_attempts_weak=None):
    """The source experiment: full mode over the whole task set, in the
    exam or the live method shape. A live source's B phase measures at
    depth 3, which the checklist bounds by the global weak-model depth
    (the first TB live config's user-agreed shape)."""
    if n_attempts_weak is None:
        n_attempts_weak = 3 if live else 1
    return ExperimentConfig(
        benchmark=_FAKE,
        mode="full",
        models={"executor_strong": STRONG, "executor_weak": WEAK},
        weak_profile="a weak model",
        n_attempts_strong=1,
        n_attempts_weak=n_attempts_weak,
        rules=dict(_RULES),
        sequence=list(LIVE_SEQUENCE if live else EXAM_SEQUENCE),
        phases=copy.deepcopy(LIVE_PHASES if live else EXAM_PHASES),
        token_budgets={"default_max_output_tokens": 512},
    )


def reuse_config_dict(*, live=False, mode="split", c_delivery=None):
    """The reuse target: the source's method identity (the same sequence
    and phase definitions) over a NEW frozen split, with the final-test
    phase's delivery/compare re-chosen (the v4 image of the v3 delivery
    slot) -- exactly the keys the ruling-4 whitelist allows to move."""
    phases = copy.deepcopy(LIVE_PHASES if live else EXAM_PHASES)
    phases["C"]["delivery"] = (c_delivery if c_delivery is not None
                               else {"name": "retrieval", "top_k": 2,
                                     "max_categories": 2, "min_score": 0.02})
    # The mode decides the C phase's control arm (the same coupling
    # upgrade_v3_config freezes): full reuses D's weak arm, split runs
    # its own fresh bare arm.
    phases["C"]["compare"] = ({"against": "D"} if mode == "full"
                              else {"against": "fresh_bare"})
    config = {
        "benchmark": _FAKE,
        "mode": mode,
        "models": {"executor_strong": STRONG, "executor_weak": WEAK},
        "weak_profile": "a weak model",
        "n_attempts_strong": 1,
        "n_attempts_weak": 3 if live else 1,
        "schema_version": 4,
        "token_budgets": {"default_max_output_tokens": 512},
        "rules": dict(_RULES),
        "sequence": list(LIVE_SEQUENCE if live else EXAM_SEQUENCE),
        "phases": phases,
    }
    if mode == "split":
        config["split"] = {"train": ["t1"], "test": ["t2"],
                           "reserved": ["t3"]}
    return config


# The baseline-level target: the D trajectories are the only reused
# artifact, so it deliberately differs from the source run in every key
# that only acts downstream -- rule names (the v2 spellings), the whole
# sequence (one A round instead of three), K, the retry family, the
# curation method, budgets, the redispatch limit, weak_profile -- and
# only the pinned minimum (benchmark, both executor models, both attempt
# counts) matches. A bank-level assembly with this config would be
# REFUSED on each of those keys; a baseline assembly must accept them
# all.
def baseline_config_dict(*, live=False):
    config = {
        "benchmark": _FAKE,
        "mode": "split",
        "split": {"train": ["t1"], "test": ["t2"], "reserved": ["t3"]},
        "models": {"executor_strong": STRONG, "executor_weak": WEAK},
        "weak_profile": "a completely different profile",
        "n_attempts_strong": 1,
        "n_attempts_weak": 1,
        "schema_version": 4,
        "invalid_redispatch_limit": 5,
        "token_budgets": {"default_max_output_tokens": 999},
        "rules": {"task_select": "any_weak_failed",
                  "segment_select": "strong_success_weak_failed",
                  "trajectory_select": "strong2_weak2"},
        "sequence": (["D", "L", "B", "L", "B", "L", "C"] if live
                     else ["D", "L", "A", "L", "C"]),
        "phases": copy.deepcopy(LIVE_PHASES if live else EXAM_PHASES),
    }
    learn = config["phases"]["L"]
    learn["evidence"] = {"name": "segment", "K": 9, "retry_limit": 5}
    learn["curate"] = {"name": "none", "token_budget": 12345}
    if live:
        # The baseline level pins n_attempts (1), so the live target's B
        # phase measures at depth 1 instead of the source's 3 -- exactly
        # the kind of downstream difference this level allows.
        config["phases"]["B"]["execution"]["n_attempts"] = 1
    config["phases"]["C"]["delivery"] = {
        "name": "retrieval", "top_k": 3, "max_categories": 2,
        "min_score": 0.05}
    config["phases"]["C"]["compare"] = {"against": "fresh_bare"}
    return config


def write_config(tmp_path: Path, config: dict,
                 name: str = "reuse.yaml") -> Path:
    path = tmp_path / name
    path.write_text(
        yaml.safe_dump(config, sort_keys=True, allow_unicode=True),
        encoding="utf-8")
    return path


def write_reuse_config(tmp_path: Path, config: dict = None,
                       name: str = "reuse.yaml") -> Path:
    return write_config(
        tmp_path,
        reuse_config_dict() if config is None else config, name)


# ---------------------------------------------------------------------------
# The synthetic source run
# ---------------------------------------------------------------------------

def _traj(task, role, attempt=0, meta=None, correctness=True):
    task_id = f"{_FAKE}:{task}"
    return Trajectory(
        trajectory_id=f"traj/{task_id}/{role}/{attempt:02d}",
        task_id=task_id,
        benchmark=_FAKE,
        model_role=role,
        model_name=STRONG if role == "strong" else WEAK,
        attempt_index=attempt,
        steps=[],
        correctness=correctness,
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
        source_subtasks=[subtask_id],
        status=status,
        merged_from=list(merged_from),
    )


def _merged_skill(serial, anchor_subtask, sources, status="accepted",
                  source_subtasks=None):
    return Skill(
        skill_id=f"bank/merge/{serial:04d}",
        content=f"merged content {serial}",
        subtask_id=anchor_subtask,
        source_subtasks=source_subtasks or [anchor_subtask],
        status=status,
        merged_from=list(sources),
    )


def _exam(subtask_id):
    return Exam(
        exam_id=f"{subtask_id}/exam",
        question_text=f"question for {subtask_id}",
        reference_answer="the answer",
        subtask_id=subtask_id,
        skill_id=f"{subtask_id}/skill",
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


def _last_learn_label_of(config) -> str:
    positions = [index for index, name in enumerate(config.sequence)
                 if isinstance(config.phases[name], LearnPhaseSpec)]
    return phase_label(positions[-1], config.sequence[positions[-1]])


def _source_bank_path(run_dir: Path, config) -> Path:
    """Where THIS world's source keeps its bank: a v4 run's last learn
    phase artifact."""
    return phase_bank_dir(run_dir, _last_learn_label_of(config)) \
        / phase_bank.BANK_NAME


def _write_source_bank(run_dir: Path, config, rows) -> Path:
    path = _source_bank_path(run_dir, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows), encoding="utf-8")
    return path


class SourceRun:
    """The built source run plus the ids the assertions refer to."""

    def __init__(self, run_dir, ids, config):
        self.run_dir = run_dir
        self.ids = ids
        self.config = config

    @property
    def bank_path(self):
        return _source_bank_path(self.run_dir, self.config)


def build_source_run(tmp_path: Path, source_tasks=None, *,
                     live=False) -> SourceRun:
    """One complete fake v4 phase-world source run (see the module
    docstring).

    Task layout: t1 = train, t2 = test, t3 = reserved for the reuse
    config below. The bank deliberately contains a MIXED-LINEAGE merge
    (train anchor + reserved source) that the full-chain criterion must
    drop and the anchor-only criterion would keep. The bank is the LAST
    learn phase's artifact (p08:L for the exam shape, p06:L for the
    live one), and the phases/ tree carries a few real-shaped phase
    records the assembly must NOT copy.

    ``source_tasks`` restricts the source run's task set (the manifest
    is what the identity checks read): the baseline subset test needs a
    source whose task set does NOT contain everything the plugin knows,
    so a train list reaching outside the SOURCE set can be caught.
    """
    install_plugin()
    run_dir = tmp_path / ("live-source" if live else "source")
    config = make_source_config(live=live)
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
        # The weak arm FAILS the train task (the Wave 3 review's A-P1
        # fixture gap, closed 2026-10-16): a real reuse source always has
        # weak-failed train tasks, so the assembled run's first learn
        # phase derives a real add row and the zero-model-call resume
        # test covers the add-row x segmentation wiring in the assembled
        # context (skipped_existing, zero calls).
        "t1w": _traj("t1", "weak", correctness=False),
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

    # The curated bank: a v4 run keeps it as its last learn phase's
    # artifact, rows sorted by skill_id. A finished run also wrote the
    # small summary beside it -- one more phases/ file the assembly
    # must not bring over.
    bank_rows = [skill.to_dict() for skill in
                 sorted(skills, key=lambda skill: skill.skill_id)
                 if skill.status == "accepted"]
    _write_source_bank(run_dir, config, bank_rows)
    bank_dir = _source_bank_path(run_dir, config).parent
    write_json(bank_dir / phase_bank.SUMMARY_NAME,
               {"members": [row["skill_id"] for row in bank_rows],
                "size": len(bank_rows)}, indent=2)

    # Real-shaped phase records (a finished run wrote them; the assembly
    # copies none), plus the A/B phase's exam-generation failure ledger.
    label = _last_learn_label_of(config)
    index_of = {name: position for position, name
                in enumerate(config.sequence)}
    write_json(phase_bank_dir(run_dir, "p01:D") / "phase.json",
               {"label": "p01:D", "index": 0, "name": "D",
                "role": "baseline"}, indent=2)
    measure_label = f"p{index_of['B' if live else 'A'] + 1:02d}:" \
        + ("B" if live else "A")
    failures_dir = phase_bank_dir(run_dir, measure_label)
    failures_dir.mkdir(parents=True, exist_ok=True)
    (failures_dir / "generation_failures.jsonl").write_text(
        json.dumps({"exam_id": exams["st1s"].exam_id,
                    "error_class": "ModelOutputError",
                    "message": "bad"}) + "\n", encoding="utf-8")
    assert label != measure_label

    # Stage 1's accepted infra holes (the durable record both worlds
    # keep at the same path).
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
    }, config)


def _v3_config_dict(v4_config: dict) -> dict:
    """The v3 STAGE-world spelling of the fixture's v4 config: exactly
    what the v3 loader froze for the same experiment (the five methods
    slots, no sequence). The test asserts the upgrade round-trips it
    back to the v4 shape, so the hand-written spelling cannot drift."""
    v3 = {key: copy.deepcopy(value) for key, value in v4_config.items()
          if key not in ("sequence", "phases", "invalid_redispatch_limit",
                         "schema_version")}
    v3["schema_version"] = 3
    v3["methods"] = {
        "segmentation": {"name": "llm", "K": 4, "retry_limit": 2},
        "authoring": {"name": "distill_diagnose", "exams": True},
        "acceptance": {"name": "exam", "retry_limit": 2},
        "curation": {"name": "agentbrew", "token_budget": 4096,
                     "rounds": 3},
        "delivery": {"name": "whole_bank"},
    }
    return v3


def build_v3_source_run(tmp_path: Path, source_tasks=None) -> SourceRun:
    """The same fixture re-shaped into the v3 stage-world spelling: the
    manifest's config carries the methods slots, the bank moved to the
    run root, and the stage-2/3 ledgers exist (a v3 run wrote them)."""
    source = build_source_run(tmp_path, source_tasks)
    manifest_path = source.run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    v3_config = _v3_config_dict(manifest["config"])
    # Insurance on the hand-written spelling: the pure upgrade mapping
    # must reproduce the frozen v4 identity (through the canonical
    # parse the identity walk itself uses).
    assert ExperimentConfig.from_dict(
        upgrade_v3_config(v3_config)).to_dict() == manifest["config"]
    manifest["config"] = v3_config
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # The bank back to the run root (where a v3 run froze it) and the
    # phases/ tree gone (a v3 run has no phase directories).
    rows = read_jsonl(source.bank_path)
    (source.run_dir / "skill_bank.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows), encoding="utf-8")
    import shutil
    shutil.rmtree(source.run_dir / "phases")
    # The v3-era ledgers: the segmentation audit (lazy, run-level) and
    # stage 3's terminal generation-failure ledger.
    (source.run_dir / "stage2_segments"
     / "segmentation_audit.jsonl").write_text(
        json.dumps({"trajectory_id": source.ids["traj"]["t1w"],
                    "status": "skipped", "reason": "empty trajectory"})
        + "\n"
        + json.dumps({"trajectory_id": source.ids["traj"]["t3s"],
                      "status": "skipped", "reason": "empty trajectory"})
        + "\n", encoding="utf-8")
    (source.run_dir / "stage3_generation"
     / "generation_failures.jsonl").write_text(
        json.dumps({"subtask_id": source.ids["subtask"]["st1s2"],
                    "error_class": "ModelOutputError", "message": "bad"})
        + "\n"
        + json.dumps({"subtask_id": source.ids["subtask"]["st3s"],
                      "error_class": "ModelOutputError", "message": "bad"})
        + "\n", encoding="utf-8")
    return source


# ---------------------------------------------------------------------------
# The happy path (a v4 phase-world source)
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
    assert counts["pending_bank_rows"] == 0
    assert counts["generation_failure_rows"] == 0  # a v4 source has none
    assert counts["accepted_infra_shortfall_units"] == 1  # train unit only
    assert summary["split_sizes"] == {"train": 1, "test": 1, "reserved": 1}
    assert summary["source_schema"] == 4
    assert summary["preseeded_phase_bank"] == "p08:L"

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

    # The v4 product: the same rows pre-seeded as the LAST learn phase's
    # artifact, and NOTHING else under phases/ (the source's phase
    # records, ledgers and summaries all stayed behind).
    artifact = run_dir / "phases" / "p08_L" / "skill_bank.jsonl"
    assert read_jsonl(artifact) == read_jsonl(run_dir / "skill_bank.jsonl")
    assert sorted(
        path.relative_to(run_dir).as_posix()
        for path in (run_dir / "phases").rglob("*")
        if path.is_file()) == ["phases/p08_L/skill_bank.jsonl"]

    # Copied objects carry zero test/reserved lineage.
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
    assert not (run_dir / "stage5_curation" / "curation_log.json").exists()
    assert not (run_dir / "stage6_test").exists()
    assert not (run_dir / "work").exists()
    assert not (run_dir / "llm_requests").exists()

    # Integrity: the kept bank rows are bit-identical to the copied skills.
    for row in read_jsonl(run_dir / "skill_bank.jsonl"):
        path = (run_dir / "stage3_generation" / "skills"
                / (urllib.parse.quote(row["skill_id"], safe="")
                   + ".json"))
        assert read_json(path) == row

    # A v4 source's exam-phase generation failures and segmentation audit
    # never existed, so neither is in the product.
    assert not (run_dir / "stage2_segments" /
                "segmentation_audit.jsonl").exists()
    assert not (run_dir / "stage3_generation" /
                "generation_failures.jsonl").exists()

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
    assert "远端任务目录的内容漂移" in text  # the digest-coverage disclaimer
    assert "全链" in text  # the full-chain criterion is stated
    # Stage-1 seeding provenance: the one campaign-seeded trajectory.
    assert "archived-campaign" in text
    # The v4 product lines: the pre-seeded artifact and the zero-copy
    # phases/ declaration.
    assert "phases/p08_L" in text
    # Per-kind counts are in the record.
    assert f"| skill_bank 行 | {summary['counts']['bank_rows']} |" in text

    # The summary the CLI prints is JSON-safe and names the run pieces.
    json.dumps(summary)
    assert summary["source_run"] == str(source.run_dir)
    assert summary["assembled_from"] == "ASSEMBLED_FROM.md"
    # The identity diff: exactly the allowed keys moved (section level).
    assert summary["allowed_identity_diffs"] == [
        "config.mode", "config.phases.C.compare", "config.phases.C.delivery",
        "config.split", "mode", "split"]


# ---------------------------------------------------------------------------
# The v3 stage-world source (the other read path)
# ---------------------------------------------------------------------------

def test_v3_stage_world_source_assembles_equally(tmp_path):
    """A v3 source (methods slots, run-root bank, stage-2/3 ledgers)
    assembles exactly like a v4 one: the upgrade chain lands the same
    identity, the same keep sets and the same product. The v3-only
    artifacts are disclosed, not copied: the generation-failure ledger
    has no v4 image (its units will be re-offered), while the lazy
    segmentation audit IS copied whole (the one exempt record)."""
    source = build_v3_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    assert summary["source_schema"] == 3
    counts = summary["counts"]
    assert counts["trajectories"] == 2
    assert counts["subtasks"] == 5
    assert counts["skills"] == 6
    assert counts["exams"] == 5
    assert counts["attempts"] == 4
    assert counts["bank_rows"] == 2
    assert counts["generation_failure_rows"] == 1  # the train-side row
    assert counts["segmentation_audit_rows"] == 2
    # The read path difference: the v3 bank came from the run root, and
    # the product is the same v4 shape as the v4-source assembly.
    assert [row["skill_id"]
            for row in read_jsonl(run_dir / "skill_bank.jsonl")] == [
        source.ids["m0"], source.ids["s_a"]]
    assert read_jsonl(run_dir / "phases" / "p08_L"
                      / "skill_bank.jsonl") == read_jsonl(
        run_dir / "skill_bank.jsonl")
    # The generation-failure ledger is NOT copied (nothing in v4 reads
    # it; the units are disclosed as re-offers instead).
    assert not (run_dir / "stage3_generation" /
                "generation_failures.jsonl").exists()
    text = (run_dir / "ASSEMBLED_FROM.md").read_text(encoding="utf-8")
    assert "generation_failures" in text  # the disclosure names the ledger
    # The segmentation audit is the WHOLE source file, validation-side row
    # included (ruling 5's lazy-audit exemption).
    source_audit = (source.run_dir / "stage2_segments"
                    / "segmentation_audit.jsonl").read_bytes()
    assert (run_dir / "stage2_segments"
            / "segmentation_audit.jsonl").read_bytes() == source_audit
    # The identity walk upgraded the source: the same allowed diffs as
    # the v4-source assembly.
    assert summary["allowed_identity_diffs"] == [
        "config.mode", "config.phases.C.compare", "config.phases.C.delivery",
        "config.split", "mode", "split"]


def test_pending_members_of_a_v4_source_ride_along_disclosed(tmp_path):
    """A v4 source's final artifact may carry PENDING members (a learn
    phase's new skills the source never finished measuring). They are
    train-lineage material: they ride along into the store, the bank and
    the pre-seeded artifact (the assembled run's A/B phases will measure
    them), and the accepted-set invariant compares ACCEPTED rows only."""
    source = build_source_run(tmp_path)
    s_p = _skill(source.ids["subtask"]["st1w2"], "pending",
                 content="never finished measuring")
    Store(source.run_dir).save(s_p, "skills")
    rows = read_jsonl(source.bank_path) + [s_p.to_dict()]
    _write_source_bank(source.run_dir, source.config,
                       sorted(rows, key=lambda row: row["skill_id"]))
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    assert summary["counts"]["bank_rows"] == 3
    assert summary["counts"]["pending_bank_rows"] == 1
    artifact_rows = read_jsonl(run_dir / "phases" / "p08_L"
                               / "skill_bank.jsonl")
    assert [row["skill_id"] for row in artifact_rows] == [
        source.ids["m0"], source.ids["s_a"], s_p.skill_id]
    assert artifact_rows[-1]["status"] == "pending"
    text = (run_dir / "ASSEMBLED_FROM.md").read_text(encoding="utf-8")
    assert "待定成员" in text  # the disclosure names the pending material


# ---------------------------------------------------------------------------
# v1-object sources (M1's migration chain)
# ---------------------------------------------------------------------------

def _downgrade_source_records_to_v1(run_dir: Path, config) -> None:
    """Rewrite a freshly built source run's Skill/Exam records and bank
    rows to the pre-v2 shape (schema_version 1; no source_subtasks, no
    exam skill_id): a source run written by pre-M1 code."""
    for path in (run_dir / "stage3_generation" / "skills").glob("*.json"):
        row = read_json(path)
        del row["source_subtasks"]
        row["schema_version"] = 1
        write_json(path, row, indent=2)
    for path in (run_dir / "stage3_generation" / "exams").glob("*.json"):
        row = read_json(path)
        del row["skill_id"]
        row["schema_version"] = 1
        write_json(path, row, indent=2)
    rows = read_jsonl(_source_bank_path(run_dir, config))
    for row in rows:
        del row["source_subtasks"]
        row["schema_version"] = 1
    _write_source_bank(run_dir, config, rows)


def _add_nested_and_reverted_merges(source: SourceRun) -> None:
    """Extend a built source run with the curation shapes the flat-merge
    fixture lacks (the M1 acceptance ruling's I1 extension): a kept
    NESTED merge bank member (m_flat2 merges two fresh originals, then
    m_nested merges m_flat2 with a third) and a REVERTED train-side merge
    left on disk OUTSIDE the bank (regression_failed; its sources stay
    accepted -- K5 marks sources merged only on a kept merge). These are
    the records the v1 migration must resolve recursively, and the
    reverted merge is the exact record a byte-copied v1 assembled run
    crashed on in stage 5's _check_bank_not_stale."""
    store = Store(source.run_dir)
    fresh = {
        "st1w3": _subtask("t1", "weak", 2),
        "st1s4": _subtask("t1", "strong", 3),
        "st1w4": _subtask("t1", "weak", 3),
    }
    originals = {
        "s_f": _skill(fresh["st1w3"].subtask_id, "merged"),
        "s_g": _skill(fresh["st1s4"].subtask_id, "merged"),
        "s_h": _skill(fresh["st1w4"].subtask_id, "merged"),
    }
    for name, subtask in fresh.items():
        store.save(subtask, "subtasks")
        store.save(_exam(subtask.subtask_id), "exams")
        source.ids["subtask"][name] = subtask.subtask_id
    for name, skill in originals.items():
        store.save(skill, "skills")
        source.ids[name] = skill.skill_id
    st1s = source.ids["subtask"]["st1s"]
    # A reverted merge attempt on [s_a, s_f]: discarded on disk outside
    # the bank; its sources stay accepted and banked.
    m_revert = _merged_skill(
        2, st1s, [source.ids["s_a"], originals["s_f"].skill_id],
        status="discarded",
        source_subtasks=[st1s, fresh["st1w3"].subtask_id])
    m_revert.discard_reason = "regression_failed"
    # The nested tree: m_flat2 (kept, then consumed) over [s_f, s_g];
    # m_nested (kept, banked) over [m_flat2, s_h]. Anchors follow K5:
    # the merge carries its first source's subtask_id.
    m_flat2 = _merged_skill(
        3, fresh["st1w3"].subtask_id,
        [originals["s_f"].skill_id, originals["s_g"].skill_id],
        status="merged",
        source_subtasks=[fresh["st1w3"].subtask_id,
                         fresh["st1s4"].subtask_id])
    m_nested = _merged_skill(
        4, fresh["st1w3"].subtask_id,
        [m_flat2.skill_id, originals["s_h"].skill_id],
        status="accepted",
        source_subtasks=[fresh["st1w3"].subtask_id,
                         fresh["st1s4"].subtask_id,
                         fresh["st1w4"].subtask_id])
    for merge in (m_revert, m_flat2, m_nested):
        store.save(merge, "skills")
    source.ids["m_revert"] = m_revert.skill_id
    source.ids["m_flat2"] = m_flat2.skill_id
    source.ids["m_nested"] = m_nested.skill_id
    # The curated bank, stage-5 discipline: rows sorted by skill_id.
    bank = sorted(read_jsonl(source.bank_path) + [m_nested.to_dict()],
                  key=lambda row: str(row["skill_id"]))
    _write_source_bank(source.run_dir, source.config, bank)


def test_bank_level_assembly_of_a_v1_object_source_run(tmp_path):
    """I1 regression (M1 acceptance ruling 1): every formal source run
    predates schema v2, so a v1-object source (records and bank rows at
    schema_version 1, bank members including a flat AND a nested merge)
    must assemble at the bank level exactly like a current one -- no
    false "the source is corrupt" from the bank comparison -- and the
    product lands v2-NATIVE: skills, exams and bank rows all at
    schema_version 2 with the new fields derived, so every consumer's
    plain from_dict load works on the assembled run."""
    source = build_source_run(tmp_path)
    _add_nested_and_reverted_merges(source)
    _downgrade_source_records_to_v1(source.run_dir, source.config)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    # Same keep sets as the v2 happy path plus the extension's three
    # originals and three merges: the v1 decision (which records to
    # keep) must not move at all.
    counts = summary["counts"]
    assert counts["trajectories"] == 2
    assert counts["subtasks"] == 8
    assert counts["skills"] == 12
    assert counts["exams"] == 8
    assert counts["attempts"] == 4
    assert counts["bank_rows"] == 3
    bank_ids = [row["skill_id"]
                for row in read_jsonl(run_dir / "skill_bank.jsonl")]
    assert bank_ids == [source.ids["m0"], source.ids["m_nested"],
                        source.ids["s_a"]]
    # Every assembled object row is current-schema with the new fields
    # derived (the write-side migration: source_subtasks for v1 rows,
    # the empty summary for pre-v3 rows), and each loads through the
    # plain from_dict every consumer uses -- the product needs no
    # migration.
    for row in read_jsonl(run_dir / "skill_bank.jsonl"):
        assert row["schema_version"] == 3
        assert row["source_subtasks"]
        assert row["summary"] == ""
        path = (run_dir / "stage3_generation" / "skills"
                / (urllib.parse.quote(row["skill_id"], safe="") + ".json"))
        assert read_json(path) == row
        Skill.from_dict(row)
    for path in (run_dir / "stage3_generation" / "skills").glob("*.json"):
        row = read_json(path)
        assert row["schema_version"] == 3
        assert "source_subtasks" in row
        assert row["summary"] == ""
        Skill.from_dict(row)
    for path in (run_dir / "stage3_generation" / "exams").glob("*.json"):
        row = read_json(path)
        assert row["schema_version"] == 2
        assert "skill_id" in row
        Exam.from_dict(row)
    # The migration rebuilt the union values K5 would have written: the
    # nested merge unions its transitive sources in first-seen order,
    # and the reverted merge (stored, outside the bank) its own pair.
    assembled = {row["skill_id"]: row
                 for row in read_jsonl(run_dir / "skill_bank.jsonl")}
    assert assembled[source.ids["m_nested"]]["source_subtasks"] == [
        source.ids["subtask"]["st1w3"],
        source.ids["subtask"]["st1s4"],
        source.ids["subtask"]["st1w4"]]
    reverted = read_json(
        run_dir / "stage3_generation" / "skills"
        / (urllib.parse.quote(source.ids["m_revert"], safe="") + ".json"))
    assert reverted["schema_version"] == 3
    assert reverted["source_subtasks"] == [
        source.ids["subtask"]["st1s"],
        source.ids["subtask"]["st1w3"]]
    assert reverted["summary"] == ""
    # The phase-world consumers' enumeration loads every stored skill
    # (the A-phase report reader and the decision walk both go through
    # the candidate enumeration).
    store = Store(run_dir)
    candidates = stage3_candidates(store)
    assert [skill.skill_id for skill in candidates] == sorted(
        source.ids[name] for name in
        ("s_a", "s_b", "s_c", "s_d", "s_e", "s_f", "s_g", "s_h"))
    assert all(skill.schema_version == 3 for skill in candidates)


def test_refuses_a_source_whose_v1_lineage_cannot_migrate(tmp_path):
    """M1 acceptance ruling 5: a v1 row whose source_subtasks cannot be
    derived is a corrupt source and must refuse as ConfigError -- the
    family of every other source-integrity refusal -- not as the
    models-layer ValueError the migration itself raises."""
    source = build_source_run(tmp_path)
    _downgrade_source_records_to_v1(source.run_dir, source.config)
    # Corrupt the flat merge's lineage: a merged_from id nothing stores.
    m0_path = (source.run_dir / "stage3_generation" / "skills"
               / (urllib.parse.quote(source.ids["m0"], safe="") + ".json"))
    row = read_json(m0_path)
    row["merged_from"] = ["bank/merge/9999"]
    write_json(m0_path, row, indent=2)
    config_path = write_reuse_config(tmp_path)

    with pytest.raises(ConfigError, match="cannot migrate"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


# ---------------------------------------------------------------------------
# The v4 resume contract: the assembled run under the real phase pipeline
# ---------------------------------------------------------------------------

def test_assembled_run_resumes_under_the_v4_pipeline_with_zero_model_calls(
        tmp_path):
    """R10's acceptance: the assembled run loads and continues under the
    v4 gate. The REAL pipeline (cli.run_experiment, refusing fake
    clients -- any model call raises) runs freeze + D + every L/A up to
    the last learn phase over the assembled directory:

    * p00 freezes the manifest (the identity re-check of last resort);
    * the D phase skips every stored trajectory (the copied shortfall
      unit is an accepted hole, never re-dispatched);
    * the FIRST learn phase derives NOTHING: the weak arm fails the
      train task, but the pre-learn bank fallback reads the assembled
      run's run-level bank (the pre-seeded artifact) and its anchor
      covers the task, so no add row -- the frozen bank is the
      measurement object and the decision must not re-offer material
      for tasks it already covers;
    * the learn phases re-derive their decisions over the copied
      records and write their own artifacts -- the LAST one's curation
      short-circuits on the pre-seeded bank, zero calls, byte-identical;
    * a full replay of the same range completes again at zero calls
      (finished phases skip whole, stored units skip dispatch).
    """
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    assemble_reuse(source.run_dir, config_path, run_dir)
    artifact = run_dir / "phases" / "p08_L" / "skill_bank.jsonl"
    artifact_before = artifact.read_bytes()

    cfg = load_config(str(config_path))
    last_learn = max(index for index, name in enumerate(cfg.sequence)
                     if isinstance(cfg.phases[name], LearnPhaseSpec))
    stages = {}

    def refusing_client(model_name):
        return FakeClient([])  # any model call fails the run loudly

    result = run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=refusing_client, until_phase=last_learn,
        on_stage=lambda label, summary: stages.setdefault(label, summary))

    # Every phase through the last learn phase ran, in sequence order.
    assert list(stages) == ["p00:freeze", "p01:D", "p02:L", "p03:A",
                            "p04:L", "p05:A", "p06:L", "p07:A", "p08:L"]
    # The D phase: both bare arms are fully stored, so nothing dispatched
    # and the copied shortfall unit is an accepted hole.
    assert stages["p01:D"]["report_rows"] == 2
    assert stages["p01:D"]["infra_accepted"] == [source.ids["traj"]["t1s"]]
    # The first learn phase (the assembled-context seam): the weak arm
    # failed the train task, but the pre-learn bank fallback read the
    # pre-seeded run-level bank and its anchor task covers it, so the
    # baseline-gap path authorizes no add at all -- zero rows, zero
    # calls, and the bank the run was assembled to re-measure stays
    # frozen.
    assert stages["p02:L"]["task_list"] == {"superseded_rows": 0}
    assert stages["p02:L"]["outcomes"] == {}
    assert read_jsonl(run_dir / "phases" / "p02_L"
                     / "outcomes.jsonl") == []
    # The last learn phase: the curation short-circuited on the
    # pre-seeded artifact (the bank members are exactly the kept rows)
    # and the artifact is byte-identical to what the assembly wrote.
    assert result["bank"]["members"] == [source.ids["m0"],
                                         source.ids["s_a"]]
    assert artifact.read_bytes() == artifact_before
    # The manifest froze on the pipeline's first start, exactly what the
    # assembly's prospective walk diffed against the source.
    assert (run_dir / "manifest.json").is_file()

    # A full replay of the same range: every phase skips at zero cost
    # (the learn phases' stored summaries are their commit points, the
    # D phase's units are all stored) and the artifact still matches.
    run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=refusing_client, until_phase=last_learn)
    assert artifact.read_bytes() == artifact_before


def test_an_assembled_pending_member_that_passes_refreshes_the_seeded_artifact(
        tmp_path):
    """The Wave 3 review's blocker B1 (ruling 1's acceptance test): an
    assembled run whose pre-seeded bank carries a PENDING member has
    that member measured by its own A phase (real model calls -- the
    exam answering, the leak audit and the judging), accepted by the
    next learn phase's keep row, and the LAST learn phase's curation
    short-circuit must then complete the run by REFRESHING the
    pre-seeded artifact from the store (the pending row follows the
    store's accepted record; the source run's merged rows stay
    verbatim; zero curation model calls). Before the fix this path
    raised ConfigError ("skill bank is stale") -- any assembled run
    that ever accepted a member could not finish."""
    source = build_source_run(tmp_path)
    s_p = _skill(source.ids["subtask"]["st1w2"], "pending",
                 content="never finished measuring")
    Store(source.run_dir).save(s_p, "skills")
    rows = read_jsonl(source.bank_path) + [s_p.to_dict()]
    _write_source_bank(source.run_dir, source.config,
                       sorted(rows, key=lambda row: row["skill_id"]))
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    assemble_reuse(source.run_dir, config_path, run_dir)
    artifact = run_dir / "phases" / "p08_L" / "skill_bank.jsonl"
    seeded = read_jsonl(artifact)
    assert [row["skill_id"] for row in seeded] == [
        source.ids["m0"], source.ids["s_a"], s_p.skill_id]

    cfg = load_config(str(config_path))
    last_learn = max(index for index, name in enumerate(cfg.sequence)
                     if isinstance(cfg.phases[name], LearnPhaseSpec))
    exam_id = ids.make_exam_id(source.ids["subtask"]["st1w2"])
    stages = {}
    clients = {}

    # The A phase measures the pending member for real: the answerer
    # (the weak model) answers bare then with the skill, the judge
    # fails the bare and passes the skilled answer, the leak audit is
    # clean. Curation must make no call -- its short-circuit refreshes
    # the artifact programmatically.
    def scripted_client(model_name):
        if model_name == WEAK:
            clients[model_name] = FakeClient(["bare answer",
                                              "skilled answer"])
        else:
            clients[model_name] = FakeClient([
                json_script({"verdict": "incorrect",
                             "error_description": ""}),
                json_script({"leaked": False, "reason": "clean"}),
                json_script({"verdict": "correct",
                             "error_description": ""})])
        return clients[model_name]

    result = run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=scripted_client, until_phase=last_learn,
        on_stage=lambda label, summary: stages.setdefault(label, summary))

    # The A phase measured exactly the ride-along member; the next learn
    # phase accepted it (the keep row's receipt).
    assert stages["p03:A"]["measured"] == [exam_id]
    accepted_receipts = [
        row for row in read_jsonl(run_dir / "phases" / "p04_L"
                                 / "outcomes.jsonl")
        if row["skill_id"] == s_p.skill_id]
    assert [row["outcome"] for row in accepted_receipts] == ["accepted"]
    assert Store(run_dir).load("skills", s_p.skill_id, Skill).status \
        == "accepted"
    # Exactly the A phase's five calls, nothing else (the curation
    # short-circuit spent zero).
    assert len(clients[WEAK].calls) == 2
    assert len(clients[STRONG].calls) == 3

    # The run COMPLETED through the last learn phase (before the fix:
    # ConfigError "skill bank is stale"), and the last learn phase's
    # summary reports the refreshed artifact.
    assert result["bank"]["members"] == [source.ids["m0"],
                                         source.ids["s_a"], s_p.skill_id]
    refreshed = read_jsonl(artifact)
    # The source run's curation rows are untouched; the pending row
    # followed the store's accepted record.
    assert refreshed[:2] == seeded[:2]
    assert refreshed[2] == read_json(
        Store(run_dir).path_for("skills", s_p.skill_id))
    assert refreshed[2]["status"] == "accepted"
    # The refresh's replay idempotency (a repeated refresh rewrites
    # nothing) is pinned by test_w1f_phase_bank's refresh tests; a
    # full pipeline replay would re-enter the exam phase, which re-
    # answers its exams (K3 is not idempotent by design) -- out of this
    # test's scope.


# ---------------------------------------------------------------------------
# Identity whitelist diff (ruling 4)
# ---------------------------------------------------------------------------

def _assemble_with_config(tmp_path, config):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path, config)
    return source, config_path, tmp_path / "assembled"


def test_identity_diff_rejects_a_changed_method_key(tmp_path):
    config = reuse_config_dict()
    config["phases"]["L"]["evidence"]["K"] = 9
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError,
                       match=r"config\.phases\.L\.evidence\.K"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()  # refused before anything was written


def test_identity_diff_rejects_a_changed_model_name(tmp_path):
    config = reuse_config_dict()
    config["models"]["executor_strong"] = "other-strong"
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="model_roles.executor_strong"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_changed_rule_name(tmp_path):
    config = reuse_config_dict()
    config["rules"]["task_select"] = "any_weak_failed"
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="rules"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_changed_weak_profile(tmp_path):
    config = reuse_config_dict()
    config["weak_profile"] = "another profile"
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="weak_profile"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_changed_benchmark(tmp_path):
    config = reuse_config_dict()
    config["benchmark"] = "otherbench"
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="benchmark"):
        assemble_reuse(source.run_dir, config_path, run_dir)


def test_identity_diff_rejects_a_renamed_test_phase(tmp_path):
    """The final-test delivery/compare whitelist is keyed by the test
    phase's LOCAL NAME: a target that renames the phase (C -> FINAL)
    changes the sequence and the phase definitions, and the assembly
    refuses instead of guessing the correspondence."""
    config = reuse_config_dict()
    config["sequence"] = [name if name != "C" else "FINAL"
                          for name in config["sequence"]]
    config["phases"]["FINAL"] = config["phases"].pop("C")
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match=r"config\.(sequence|phases)"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()


def test_identity_diff_allows_the_whitelisted_keys_only(tmp_path):
    """The reverse of the refusals: with every other key aligned, the
    assembly goes through and the summary names EXACTLY the allowed
    sections -- the C delivery/compare walk shows up as such (never as
    a bare ``config.phases``), and a target spelling the source's own
    delivery (whole_bank) assembles with no delivery diff at all."""
    source = build_source_run(tmp_path)
    config = reuse_config_dict(c_delivery={"name": "whole_bank"})
    config_path = write_reuse_config(tmp_path, config)
    summary = assemble_reuse(
        source.run_dir, config_path, tmp_path / "assembled")
    assert summary["allowed_identity_diffs"] == [
        "config.mode", "config.phases.C.compare", "config.split",
        "mode", "split"]


def test_identity_diff_allows_the_paired_execution_name(tmp_path):
    """The folder play's forced twin: core's load-time pairing check
    (checklist item 8) makes the C execution name the delivery name's
    twin -- ``folder`` pairs with ``real_folder``, everything else with
    plain ``real`` -- so a folder-delivery target over a whole_bank
    source is allowed on the delivery AND the execution NAME leaves
    (a delivery-only whitelist would refuse every folder reuse for a
    knob the target cannot choose freely)."""
    source = build_source_run(tmp_path)
    config = reuse_config_dict(c_delivery={"name": "folder"})
    config["phases"]["C"]["execution"] = {"name": "real_folder"}
    config_path = write_reuse_config(tmp_path, config)

    summary = assemble_reuse(
        source.run_dir, config_path, tmp_path / "assembled")

    assert "config.phases.C.delivery" in summary["allowed_identity_diffs"]
    assert "config.phases.C.execution.name" in \
        summary["allowed_identity_diffs"]


def test_identity_diff_still_refuses_a_changed_execution_depth(tmp_path):
    """Only the NAME leaf joined the delivery whitelist: an execution
    key that changes what is measured (the sampling depth) stays a
    refusal even on the final-test phase."""
    source = build_source_run(tmp_path)
    config = reuse_config_dict()
    config["phases"]["C"]["execution"] = {"name": "real", "n_attempts": 1}
    config_path = write_reuse_config(tmp_path, config)
    with pytest.raises(
            ConfigError,
            match=r"config\.phases\.C\.execution\.n_attempts"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")
    assert not (tmp_path / "assembled").exists()


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
    config = reuse_config_dict()
    config["split"] = {"ratio": 0.34, "seed": 7}
    config_path = write_reuse_config(tmp_path, config)
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


def test_phase_tree_assertion_fires_on_a_dirty_phases_dir(tmp_path):
    # The phase-world twin of the dirty-run-dir refusal: a leftover
    # phases/ entry (a source phase record the copy was never allowed to
    # make) stops the assembly.
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    run_dir.mkdir()
    (run_dir / "phases" / "p01_D").mkdir(parents=True)
    (run_dir / "phases" / "p01_D" / "phase.json").write_text(
        "{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="phases/"):
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
    rows = read_jsonl(source.bank_path)
    rows.append({
        "skill_id": "traj/asmbench:nowhere/strong/00/seg00/skill",
        "content": "ghost", "subtask_id":
        "traj/asmbench:nowhere/strong/00/seg00", "version": 1,
        "status": "accepted", "discard_reason": None, "group": None,
        "rewrite_history": [], "merged_from": [],
        "schema_version": 2})
    _write_source_bank(source.run_dir, source.config, rows)
    with pytest.raises(ConfigError, match="does not store"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


def test_refuses_a_bank_row_that_differs_from_its_stored_skill(tmp_path):
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    rows = read_jsonl(source.bank_path)
    rows[0]["content"] = rows[0]["content"] + " (edited)"
    _write_source_bank(source.run_dir, source.config, rows)
    with pytest.raises(ConfigError, match="differs from the stored skill"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled")


def test_refuses_a_source_whose_accepted_set_exceeds_its_bank(tmp_path):
    # An accepted train skill missing from the bank would make the
    # assembled store's accepted set larger than the filtered bank --
    # exactly the staleness the integrity invariant refuses.
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path)
    rows = [row for row in read_jsonl(source.bank_path)
            if row["skill_id"] != source.ids["s_a"]]
    _write_source_bank(source.run_dir, source.config, rows)
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
                        content="c", subtask_id="traj/b:t1/strong/00/seg00",
                        source_subtasks=["traj/b:t1/strong/00/seg00"])
    original_t2 = Skill(skill_id="traj/b:t2/weak/00/seg00/skill",
                        content="c", subtask_id="traj/b:t2/weak/00/seg00",
                        source_subtasks=["traj/b:t2/weak/00/seg00"])
    merge = Skill(skill_id="bank/merge/0000", content="c",
                  subtask_id="traj/b:t1/strong/00/seg00",
                  source_subtasks=["traj/b:t1/strong/00/seg00",
                                   "traj/b:t2/weak/00/seg00"],
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
                   source_subtasks=["traj/b:t1/strong/00/seg00",
                                    "traj/b:t2/weak/00/seg00"],
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
                   content="c", subtask_id="traj/b:t9/strong/00/seg00",
                   source_subtasks=["traj/b:t9/strong/00/seg00"])
    with pytest.raises(ConfigError, match="anchor subtask"):
        skill_lineage_tasks(orphan, subtasks)


def test_lineage_of_a_merge_naming_a_missing_original_is_refused():
    subtasks, _, _, _ = _lineage_fixture()
    broken = Skill(skill_id="bank/merge/0002", content="c",
                   subtask_id="traj/b:t1/strong/00/seg00",
                   source_subtasks=["traj/b:t1/strong/00/seg00"],
                   merged_from=["traj/b:t1/strong/00/seg09/skill"])
    with pytest.raises(ConfigError, match="does not store"):
        skill_lineage_tasks(broken, subtasks)


# ---------------------------------------------------------------------------
# The baseline level (revision 3): the D phase's trajectories only
# ---------------------------------------------------------------------------

def test_baseline_level_copies_only_trajectories_and_shortfall(tmp_path):
    source = build_source_run(tmp_path)
    # The config differs from the source run in rule names, the phase
    # definitions, the curation method, K, the redispatch limit, budgets
    # and weak_profile; the baseline level must accept every one of those
    # (revision 3: they only act on the freshly re-run downstream
    # phases).
    config_path = write_reuse_config(tmp_path, baseline_config_dict())
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
        "train": 1, "source_tasks": 3}
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
    # exists, nor do the bank, the stage-2/3 ledgers, the phases/ tree or
    # anything on the shared negative list (the assembly asserted the
    # same inside).
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
    assert not (run_dir / "phases").exists()
    assert not (run_dir / "work").exists()
    assert not (run_dir / "llm_requests").exists()

    # The baseline provenance record: the fresh-run declaration, the
    # subset proof and the trajectory provenance (campaign seeding
    # included -- the trajectories are the one artifact this level
    # carries, so their origin is load-bearing).
    text = (run_dir / "ASSEMBLED_FROM.md").read_text(encoding="utf-8")
    assert "baseline 档位" in text
    assert "D 相位之外的全部相位将按当前协议全新执行" in text
    assert "⊆ 源任务集" in text
    assert "archived-campaign" in text
    assert "| trajectories | 2 |" in text


def test_baseline_refuses_a_changed_executor_model(tmp_path):
    config = baseline_config_dict()
    config["models"]["executor_weak"] = "other-weak"
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="executor_weak"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()  # refused before anything was written


def test_baseline_refuses_changed_n_attempts(tmp_path):
    config = baseline_config_dict()
    config["n_attempts_weak"] = 2
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="n_attempts_weak"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()


def test_baseline_refuses_a_changed_benchmark(tmp_path):
    config = baseline_config_dict()
    config["benchmark"] = "otherbench"
    source, config_path, run_dir = _assemble_with_config(tmp_path, config)
    with pytest.raises(ConfigError, match="otherbench"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()


def test_baseline_refuses_train_outside_the_source_task_set(tmp_path):
    # A source over a restricted task set, then a train list reaching
    # outside it: the trajectories to copy would not exist in the source
    # run, so the subset pin refuses before any copy.
    source = build_source_run(tmp_path, source_tasks=["t1", "t2"])
    config = baseline_config_dict()
    config["split"] = {"train": ["t3"], "test": ["t1"], "reserved": []}
    config_path = write_reuse_config(tmp_path, config)
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match="does not contain"):
        assemble_reuse(source.run_dir, config_path, run_dir,
                       level="baseline")
    assert not run_dir.exists()


def test_baseline_skips_a_missing_shortfall_file(tmp_path):
    source = build_source_run(tmp_path)
    (source.run_dir / "stage1_baseline"
     / "accepted_infra_shortfall.json").unlink()
    config_path = write_reuse_config(tmp_path, baseline_config_dict())
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
    # synthetic source run assembled into a fresh, trajectories-only run.
    from expedistill import cli

    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path, baseline_config_dict())
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

@pytest.mark.parametrize("live", [False, True], ids=["exam", "live_run"])
def test_baseline_level_allows_exam_and_live_targets(tmp_path, live):
    """The D trajectories are the only reused artifact, so the method
    shape (the sequence and the phase definitions) may differ from the
    source run in either direction: the pinned minimum at this level
    never walks it."""
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(
        tmp_path, baseline_config_dict(live=live))
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir,
                             level="baseline")

    assert summary["level"] == "baseline"
    assert summary["counts"] == {
        "trajectories": 2, "accepted_infra_shortfall_units": 1}


def test_bank_level_refuses_an_exam_to_live_target(tmp_path):
    """The bank's method identity cannot be swapped: the copied objects
    were produced under the source run's exam sequence, and a live
    target would re-freeze a different sequence over them."""
    source = build_source_run(tmp_path)
    config_path = write_reuse_config(tmp_path, reuse_config_dict(live=True))
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match=r"config\.(sequence|phases)"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()


def test_bank_level_refuses_a_live_to_exam_target(tmp_path):
    """The reverse direction is refused by the same identity walk: a
    live-method bank cannot be re-frozen as an exam-method one."""
    source = build_source_run(tmp_path, live=True)
    config_path = write_reuse_config(tmp_path)
    run_dir = tmp_path / "assembled"
    with pytest.raises(ConfigError, match=r"config\.(sequence|phases)"):
        assemble_reuse(source.run_dir, config_path, run_dir)
    assert not run_dir.exists()


def test_bank_level_live_to_live_reuse_discloses_no_live_digest_drift(
        tmp_path):
    """Same-method live->live reuse goes through, and the two G10/G11
    prompt digests are on BOTH sides (the v3-era full freeze), so the
    drift disclosure reports no "prompt set" drift -- without the full
    freeze it would misreport the whole live prompt pair as config
    drift."""
    source = build_source_run(tmp_path, live=True)
    config_path = write_reuse_config(
        tmp_path, reuse_config_dict(live=True))
    run_dir = tmp_path / "assembled"

    summary = assemble_reuse(source.run_dir, config_path, run_dir)

    assert summary["level"] == "bank"
    assert "LIVE_RUN_ATTRIBUTION" not in summary["prompt_digest_drift"]
    assert "LIVE_SKILL_REVISION" not in summary["prompt_digest_drift"]
    assert summary["prompt_digest_drift"] == []
    # The no-drift assertion is not vacuous: the source manifest really
    # froze the two live digests, so both sides of the comparison
    # carried them.
    source_manifest = read_json(source.run_dir / "manifest.json")
    from expedistill import prompts
    assert source_manifest["prompt_digests"][
        "LIVE_RUN_ATTRIBUTION"] == prompts.digest("LIVE_RUN_ATTRIBUTION")
    assert source_manifest["prompt_digests"][
        "LIVE_SKILL_REVISION"] == prompts.digest("LIVE_SKILL_REVISION")


def test_v2_source_manifest_is_upgraded_for_the_identity_walk(tmp_path):
    """The assembler's v2-source branch (reviewer C's precondition 3): a
    source run frozen under schema v2 -- the real TB runs are -- has its
    manifest's config upgraded through the full chain (v2 -> v3 -> v4)
    for the identity comparison, so a matching v4 target assembles
    instead of drowning in key-set diffs. The negative control: an
    UPGRADE-INCOMPATIBLE change (the model) is still refused after the
    upgrade."""
    source = build_v3_source_run(tmp_path)
    manifest_path = source.run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    # Freeze the source as v2: revert the config to its v2 spelling.
    v3_config = manifest["config"]
    v2_config = {key: value for key, value in v3_config.items()
                 if key not in ("methods", "schema_version")}
    v2_config.update({
        "K": 4, "segment_retry_limit": 2, "retry_limit": 2,
        "bank_token_budget": 4096, "curation_rounds": 3,
        "skill_delivery": {"mode": "whole_bank"},
    })
    v2_config["schema_version"] = 2
    manifest["config"] = v2_config
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    config_path = write_reuse_config(tmp_path)
    summary = assemble_reuse(
        source.run_dir, config_path, tmp_path / "assembled")
    assert summary["level"] == "bank"
    assert summary["source_schema"] == 2
    # And the upgrade does not weaken the walk: a changed executor model
    # on the SAME v2 source is still a refusal (one source, two
    # assemblies -- build_source_run registers its plugin).
    manifest["config"] = dict(
        v2_config,
        models=dict(v2_config["models"], executor_strong="other-strong"))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigError, match="executor_strong"):
        assemble_reuse(source.run_dir, config_path, tmp_path / "assembled2")
