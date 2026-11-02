#!/usr/bin/env python
"""Generate the paper's experiment configs, one YAML per (arm, benchmark).

Purpose: the four paper experiments (docs/decisions/paper_experiments_code_
20261030.md) are a matrix of arms that must differ in exactly one factor.
Writing them by hand invites drift, so the matrix lives here as data and the
YAMLs are rendered from it. The script builds dicts and dumps them with
``yaml.safe_dump(sort_keys=False)`` in the key order of
``tb21_p02p08_exam_small20_20261030.yaml``; it holds no method logic.

Inputs
    configs/experiments/ source configs (frozen records, never modified):
      tb21     tb21_p02p08_exam_small20_20261030.yaml   split, tasks, control
      alfworld alfworld_split_live_run_20261020.yaml    split
      searchqa searchqa_split_live_run_20261020_v4.yaml split
    Gateway model ids per benchmark and the student id are parameters
    (--d-model BENCH=ID, --q-model ID) with the source configs' values as
    defaults; endpoints and keys never enter a config.

Outputs
    configs/experiments/<bench>_split_paper_<exp>_<arm>.yaml, bench in
    {tb21, alfworld, searchqa}. No new top-level files.

Usage
    .venv/bin/python scripts/run/build_paper_configs.py            # write
    .venv/bin/python scripts/run/build_paper_configs.py --check    # verify
    .venv/bin/python scripts/run/build_paper_configs.py --print-index

``--check`` writes nothing and exits 1 when a file is missing, differs from
the render, or is stale (matches ``*_split_paper_*.yaml`` but is no longer
produced). A config whose formal run has started must never be regenerated
with different parameters (AGENTS section 6): change the script, then give
the new arm a new name.

Common to every config: schema 7, mode split, D = deepseek-v4-flash, Q =
qwen3.6-flash-distill, 3 attempts per arm, ``use_weak_profile: false`` and no
profile, one token-budget block on every benchmark (author and rewriter
input budget 524288 when D fills them, as trial_level requires). The input
budget follows the MODEL, not the role: 524288 for D and 262144 for Q, so a
teacher Q is never declared a context it does not have. The reference method
(``e1_ours``) is the sequence baseline, learn_add, exam, learn, exam,
learn_trial, test with cluster_by_trigger authoring, agentbrew curation,
K = 5, teacher D and student Q. TB tests use the external campaign control
(model_key qwen for student Q, dpsk for student D); ALFWorld and SearchQA
use fresh_bare.

Arm index (experiment, arm, what differs from its reference; ``--print-index``
names the reference of every row and computes the difference)
    e1  ours                the reference method (reference of e1, e2, e3,
                            e4_a0, e4_a3_joint, e4_joint9)
    e1  <baseline>_folder   test only, folder + real_folder, for baseline in
                            skillopt, evoskill, trace2skill, gepa, expel,
                            agentbrew; run on a run directory prepared by
                            import-bank. The bank is not in the config: the
                            six baselines differ only in file name, hence in
                            the run directory.
    e1  <baseline>_whole    test only, whole_bank (verbatim: the imported
                            documents are injected exactly as stored) + real
    e1  bare                SearchQA only: test only, whole_bank + real +
                            fresh_bare, run on a FRESH run directory (empty
                            bank, no import-bank), so both arms are bare.
                            ``delivery: none`` is not usable in a test
                            phase: its with-bank unit ids collide with the
                            fresh_bare control's and the phase stops with a
                            duplicate-unit-id error. TB bare is the external
                            campaign control, so no TB file.
    e1  bare_q/bare_d/bare_x  ALFWorld only: one bare run per student model
                            (empty bank, whole_bank), the run that freezes
                            that model's control file. Every with-bank
                            ALFWorld arm reads its test control from its
                            student's frozen file (external_campaign), so a
                            model is bare-run ONCE and every comparison
                            reuses it. Generate these three first; the other
                            arms refuse to generate until their controls
                            exist.
    e2  d2d d2q q2q q2d     teacher -> student. The teacher fills
                            executor_strong, author, segmenter, curator,
                            rewriter; the student fills executor_weak and
                            answerer; examiner, judge, auditor stay D. Sequence
                            baseline, learn_add, exam, test; learn_add:
                            write_lessons false, accept_new true, curate none;
                            the exam only measures; TB control model_key
                            follows the student.
    e2  x2q                 ALFWorld only: teacher X (qwen3.8-27b) ->
                            student Q; same recipe as the four cells above
    e3  k1                  learn_add review K = 1 (K = 5 is e1_ours)
    e4  a0                  no evolution: baseline, learn_add, test;
                            learn_add accept_new true, curate none
    e4  a3_joint            scope ablation, joint arm: new_skills false in
                            learn and learn_trial; revise_scope left at its
                            default both (so it differs from e1_ours in
                            new_skills only). Reference of a1 and a2.
    e4  a1_body             vs a3_joint: revise_scope body in learn and
                            learn_trial (new_skills false as in a3)
    e4  a2_trigger          vs a3_joint: revise_scope trigger in learn and
                            learn_trial (new_skills false as in a3)
    e4  joint9              nine-phase sequence with a third trial_level
                            definition learn_final; revise_scope both in all
                            three learn definitions (new_skills stays true)
    e4  staged9             vs joint9: learn (P04) body and learn_trial (P06)
                            trigger; learn_final (P08, last learn phase, no
                            rewrite left) stays both

Every ALFWorld config is named ``alfworld_split_paper_v2_*``: the 2026-11-02
unified-protocol alignment invalidated the earlier ALFWorld runs, and the
old ``alfworld_split_paper_*`` files stay frozen as that record. Every
with-bank ALFWorld arm's control is the frozen bare file of its student
model (``BARE_CONTROL_FILES``), built from the ``e1_bare_q/d/x`` runs and
pinned by sha256: each model is bare-run once and every comparison reuses
that measurement.

new_skills false matters for the scope arms: with it on, a new skill gets a
fresh trigger written from weak-model failure evidence, so "trigger frozen"
(a1) would not be frozen and "body frozen" (a2) would still gain new bodies.
"""

import argparse
import copy
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"

BENCHMARKS = ("tb21", "alfworld", "searchqa")
LOCAL_BENCHMARKS = ("alfworld", "searchqa")
#: Benchmark prefix in the file name -> the plugin name in the config.
BENCHMARK_NAMES = {"tb21": "tb", "alfworld": "alfworld",
                   "searchqa": "searchqa"}

#: The source configs the splits (and TB's control file) are copied from.
SOURCE_CONFIGS = {
    "tb21": "tb21_p02p08_exam_small20_20261030.yaml",
    "alfworld": "alfworld_split_live_run_20261020.yaml",
    "searchqa": "searchqa_split_live_run_20261020_v4.yaml",
}

#: Gateway ids of D differ per benchmark config today; Q is one id.
DEFAULT_D_MODELS = {
    "tb21": "deepseek-v4-flash-0731--ali",
    "alfworld": "deepseek-v4-flash-0731-tencent",
    "searchqa": "deepseek-v4-flash-0731-tencent",
}
DEFAULT_Q_MODEL = "qwen3.6-flash-distill"
#: The experiment-2 comparator teacher (2026-11-02 user request): bare D is
#: expected to beat bare X, while X is the better teacher for Q.
DEFAULT_X_MODEL = "qwen3.8-27b"

#: The TB external campaign's column per student model.
CAMPAIGN_KEYS = {"d": "dpsk", "q": "qwen"}

#: Placeholder control for the three bare ALFWorld arms. A bare arm runs the
#: empty bank, so executing a second bare control would repeat the same
#: measurement; this pinned file keeps the control arm at zero execution and
#: the reported number is the with-bank arm. It is NOT a control for any
#: with-bank arm (those use the per-model frozen files below).
POOLED_CONTROL = {
    "path": "../seed/alfworld_bare_q_pooled_20261102.json",
    "sha256": "79d8ced4e55e94a8539437733baf4ea4484ef085f87d0c928942536f40061b5f",
    "model_key": "qwen3.6-flash-distill",
}

#: One frozen bare run per student model, made under the unified protocol
#: (2026-11-02). Every with-bank ALFWorld arm reads its test control from the
#: file of ITS student model: a model is bare-run once, and every comparison
#: reuses that measurement instead of re-running a fresh control per arm.
#: The files are built by scripts/run/build_bare_control.py after the bare
#: runs finish; the sha256 is read from disk at generation time and pinned.
SEED_DIR = REPO_ROOT / "configs" / "seed"
BARE_CONTROL_FILES = {
    "q": "alfworld_bare_q_20261102.json",
    "d": "alfworld_bare_d_20261102.json",
    "x": "alfworld_bare_x_20261102.json",
}

BASELINES = ("skillopt", "evoskill", "trace2skill", "gepa", "expel",
             "agentbrew")

FILE_GLOB = "*_split_paper_*.yaml"
HEADER = ("# Generated by scripts/run/build_paper_configs.py -- edit the "
          "script, not this file.")

DESCRIPTION_MODE = "summary_then_when_to_use"

ROLES = ("answerer", "auditor", "author", "curator", "examiner",
         "executor_strong", "executor_weak", "judge", "rewriter",
         "segmenter")
#: Roles the student fills.
STUDENT_ROLES = ("answerer", "executor_weak")
#: Roles the teacher fills; the rest (examiner, judge, auditor) stay D.
TEACHER_ROLES = ("author", "curator", "executor_strong", "rewriter",
                 "segmenter")

#: Input budget per model: D (reasoning, 512K context) and Q (256K).
D_INPUT_TOKENS = 524288
Q_INPUT_TOKENS = 262144

RULES = {
    "segment_select": "strong_success_weak_failed",
    "task_select": "any_weak_failed",
    "trajectory_select": "strong1_weak1",
}

SEQUENCE_FULL = ["baseline", "learn_add", "exam", "learn", "exam",
                 "learn_trial", "test"]
SEQUENCE_NINE = ["baseline", "learn_add", "exam", "learn", "exam",
                 "learn_trial", "exam", "learn_final", "test"]


# ---------------------------------------------------------------------------
# Phase definitions (key order of the TB example)
# ---------------------------------------------------------------------------

def _cluster_author() -> Dict[str, Any]:
    return {"name": "cluster_by_trigger", "max_cards_per_prompt": 40,
            "max_chars_per_card": 1200, "min_cluster_size": 1,
            "classify_retry_limit": 2, "cluster_retry_limit": 2,
            "skill_retry_limit": 2}


def _agentbrew() -> Dict[str, Any]:
    return {"drop_low_value": False, "grouping_input": "full",
            "merge_check": "none", "name": "agentbrew", "rounds": 3,
            "similarity_input": "summary", "token_budget": None}


def _no_curation() -> Dict[str, Any]:
    return {"name": "none", "token_budget": None}


def _review(decision: str, *, k: int = 5, write_lessons: bool = True,
            revise_scope: Optional[str] = None,
            new_skills: Optional[bool] = None,
            accept_new: bool = False) -> Dict[str, Any]:
    """A review block; a switch is spelled only when the arm sets it, so
    the reference method carries none of them."""
    block: Dict[str, Any] = {
        "name": "review", "decision": decision,
        "unread_in_won_group": "delete", "before_steps": 10,
        "after_steps": 10, "K": k, "retry_limit": 2,
        "write_lessons": write_lessons}
    if revise_scope is not None:
        block["revise_scope"] = revise_scope
    if new_skills is not None:
        block["new_skills"] = new_skills
    if accept_new:
        block["accept_new"] = True
    return block


def _learn_phase(review: Dict[str, Any],
                 curate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"author": _cluster_author(),
            "curate": curate if curate is not None else _agentbrew(),
            "review": review, "kind": "learn", "role": "learn"}


def _baseline_phase() -> Dict[str, Any]:
    return {"acceptance": None, "compare": None,
            "delivery": {"name": "none"},
            "execution": {"arms": ["executor_strong", "executor_weak"],
                          "name": "real"},
            "kind": "execute", "role": "baseline", "tasks": "train"}


def _exam_phase() -> Dict[str, Any]:
    return {"acceptance": None, "compare": None,
            "delivery": {"description_mode": DESCRIPTION_MODE,
                         "name": "exam_folder"},
            "execution": {"bare_control": False, "leak_audit": False,
                          "max_skill_reads": 3, "name": "exam"},
            "kind": "execute", "role": "exam", "tasks": "train"}


def _final_test_phase(kind: str, compare: Dict[str, Any]) -> Dict[str, Any]:
    """The test phase: ``folder`` pairs with real_folder, ``whole`` is the
    whole-bank injection under plain real (core.config checklist item 8),
    ``whole_verbatim`` the same with every skill text injected exactly as
    stored (imported baseline documents must not be flattened). The sampling
    depth is the global n_attempts_weak."""
    if kind == "folder":
        delivery = {"description_mode": DESCRIPTION_MODE, "name": "folder"}
        execution = {"name": "real_folder"}
    elif kind in ("whole", "whole_verbatim"):
        delivery = {"name": "whole_bank"}
        if kind == "whole_verbatim":
            delivery["verbatim"] = True
        execution = {"name": "real"}
    else:
        raise ValueError(f"unknown test delivery kind {kind!r}")
    return {"acceptance": None, "compare": compare, "delivery": delivery,
            "execution": execution, "kind": "execute", "role": "test",
            "tasks": "test"}


# ---------------------------------------------------------------------------
# Method recipes: test phase -> (sequence, phases)
# ---------------------------------------------------------------------------

Recipe = Callable[[Dict[str, Any]], Tuple[List[str], Dict[str, Any]]]


def full_method(test: Dict[str, Any], *, k: int = 5,
                scopes: Optional[Dict[str, str]] = None,
                new_skills: Optional[bool] = None,
                nine: bool = False) -> Tuple[List[str], Dict[str, Any]]:
    """exam + trial_level. ``scopes`` maps a trial_level learn definition to
    its ``revise_scope``; ``new_skills`` is spelled in every trial_level
    definition when given; ``nine`` adds the third definition
    ``learn_final`` so P04, P06 and P08 can carry different scopes."""
    scopes = scopes or {}

    def trial(name: str) -> Dict[str, Any]:
        return _learn_phase(_review("trial_level",
                                    revise_scope=scopes.get(name),
                                    new_skills=new_skills))

    phases = {
        "baseline": _baseline_phase(),
        "learn_add": _learn_phase(_review("skill_level", k=k)),
        "exam": _exam_phase(),
        "learn": trial("learn"),
        "learn_trial": trial("learn_trial"),
    }
    if nine:
        phases["learn_final"] = trial("learn_final")
    phases["test"] = test
    return list(SEQUENCE_NINE if nine else SEQUENCE_FULL), phases


def test_only(test: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """A run directory prepared by import-bank carries the bank; only the
    test phase runs."""
    return ["test"], {"test": test}


def teacher_student(test: Dict[str, Any]
                    ) -> Tuple[List[str], Dict[str, Any]]:
    """Experiment 2: the add phase accepts what it writes and the exam only
    measures, since no learn phase follows it."""
    phases = {
        "baseline": _baseline_phase(),
        "learn_add": _learn_phase(
            _review("skill_level", write_lessons=False, accept_new=True),
            curate=_no_curation()),
        "exam": _exam_phase(),
        "test": test,
    }
    return ["baseline", "learn_add", "exam", "test"], phases


def no_evolution(test: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """Experiment 4 arm A0: the P02 bank is the final bank."""
    phases = {
        "baseline": _baseline_phase(),
        "learn_add": _learn_phase(_review("skill_level", accept_new=True),
                                  curate=_no_curation()),
        "test": test,
    }
    return ["baseline", "learn_add", "test"], phases


# ---------------------------------------------------------------------------
# The arm matrix
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arm:
    stem: str
    exp: str
    summary: str
    recipe: Recipe
    benchmarks: Tuple[str, ...] = BENCHMARKS
    teacher: str = "d"
    student: str = "q"
    test: str = "folder"
    #: The stem ``--print-index`` diffs this arm against.
    reference: str = "e1_ours"


def arms() -> List[Arm]:
    out = [Arm("e1_ours", "e1",
               "reference: exam + trial_level, K=5, teacher D, student Q",
               full_method)]
    for baseline in BASELINES:
        out.append(Arm(
            f"e1_{baseline}_folder", "e1",
            f"{baseline} bank, test only, folder + real_folder",
            test_only))
        out.append(Arm(
            f"e1_{baseline}_whole", "e1",
            f"{baseline} bank, test only, whole_bank (verbatim) + real",
            test_only, test="whole_verbatim"))
    out.append(Arm(
        "e1_bare", "e1",
        "bare: test only, whole_bank on an empty bank + fresh_bare",
        test_only, benchmarks=("searchqa",), test="whole"))
    for student in ("q", "d", "x"):
        out.append(Arm(
            f"e1_bare_{student}", "e1",
            f"bare {student.upper()}: test only, whole_bank on an empty "
            "bank; the run that freezes that model's control file",
            test_only, benchmarks=("alfworld",), student=student, test="whole"))
    for teacher in ("d", "q"):
        for student in ("d", "q"):
            out.append(Arm(
                f"e2_{teacher}2{student}", "e2",
                f"teacher {teacher.upper()} -> student {student.upper()}",
                teacher_student, teacher=teacher, student=student))
    out.append(Arm(
        "e2_x2q", "e2",
        "teacher X (qwen3.8-27b) -> student Q",
        teacher_student, teacher="x", student="q",
        benchmarks=("alfworld",)))
    out.append(Arm("e3_k1", "e3", "learn_add review K=1",
                   partial(full_method, k=1)))
    out.append(Arm("e4_a0", "e4",
                   "A0: baseline, learn_add (accept_new, no curation), test",
                   no_evolution))
    out.append(Arm(
        "e4_a3_joint", "e4",
        "A3: scope ablation joint arm, new_skills false in learn and "
        "learn_trial",
        partial(full_method, new_skills=False)))
    out.append(Arm(
        "e4_a1_body", "e4",
        "A1: revise_scope body, new_skills false, in learn and learn_trial",
        partial(full_method, new_skills=False,
                scopes={"learn": "body", "learn_trial": "body"}),
        reference="e4_a3_joint"))
    out.append(Arm(
        "e4_a2_trigger", "e4",
        "A2: revise_scope trigger, new_skills false, in learn and "
        "learn_trial",
        partial(full_method, new_skills=False,
                scopes={"learn": "trigger", "learn_trial": "trigger"}),
        reference="e4_a3_joint"))
    out.append(Arm(
        "e4_joint9", "e4",
        "nine-phase reference: revise_scope both in all three learn "
        "definitions",
        partial(full_method, nine=True,
                scopes={"learn": "both", "learn_trial": "both",
                        "learn_final": "both"})))
    out.append(Arm(
        "e4_staged9", "e4",
        "nine-phase staged: learn body, learn_trial trigger, "
        "learn_final both",
        partial(full_method, nine=True,
                scopes={"learn": "body", "learn_trial": "trigger",
                        "learn_final": "both"}),
        reference="e4_joint9"))
    return out


# ---------------------------------------------------------------------------
# Config assembly
# ---------------------------------------------------------------------------

class Source(NamedTuple):
    split: Dict[str, List[str]]
    tasks: Optional[List[str]]
    external: Optional[Dict[str, str]]


class Generated(NamedTuple):
    arm: Arm
    bench: str
    config: Dict[str, Any]


def load_source(bench: str, source_dir: Path) -> Source:
    """Copy the split (and TB's task list and control file identity) from
    the benchmark's source config, read as plain YAML."""
    path = Path(source_dir) / SOURCE_CONFIGS[bench]
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    split = raw["split"]
    for key in ("train", "test"):
        if not isinstance(split.get(key), list) or not split[key]:
            raise ValueError(f"{path}: split.{key} must be a non-empty list")
    external = None
    if bench == "tb21":
        pinned = raw["phases"]["test"]["compare"]["external"]
        external = {"path": pinned["path"], "sha256": pinned["sha256"]}
    return Source(split=copy.deepcopy(split),
                  tasks=copy.deepcopy(raw.get("tasks")), external=external)


def _models(teacher: str, student: str, d_model: str,
            q_model: str, x_model: str) -> Dict[str, str]:
    by_letter = {"d": d_model, "q": q_model, "x": x_model}
    models = {}
    for role in ROLES:
        if role in TEACHER_ROLES:
            models[role] = by_letter[teacher]
        elif role in STUDENT_ROLES:
            models[role] = by_letter[student]
        else:
            models[role] = d_model
    return models


def _token_budgets(models: Dict[str, str], d_model: str, q_model: str,
                   x_model: str) -> Dict[str, Any]:
    """The input budget follows the model the role runs, not the role."""
    budgets = {d_model: D_INPUT_TOKENS, q_model: Q_INPUT_TOKENS,
               x_model: Q_INPUT_TOKENS}
    roles = {
        role: {"max_output_tokens": 65536,
               "max_input_tokens": budgets[models[role]]}
        for role in ROLES}
    return {"default_max_output_tokens": 65536, "max_input_tokens": None,
            "roles": roles}


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bare_control(student: str, d_model: str, q_model: str,
                  x_model: str) -> Dict[str, Any]:
    """The frozen bare control of one student model, pinned by sha256."""
    filename = BARE_CONTROL_FILES[student]
    path = SEED_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"the frozen bare control {path} does not exist yet; run the "
            f"e1_bare_{student} arm first, then "
            "scripts/run/build_bare_control.py")
    model = {"q": q_model, "d": d_model, "x": x_model}[student]
    return {"model_key": model, "path": f"../seed/{filename}",
            "sha256": _sha256(path)}


def _test_compare(source: Source, bench: str, arm: Arm, d_model: str,
                  q_model: str, x_model: str) -> Dict[str, Any]:
    if bench == "alfworld":
        if arm.stem.startswith("e1_bare_"):
            # The bare arm is the measurement: the zero-execution placeholder
            # keeps its control arm from re-running the same bare model.
            return {"against": "external_campaign",
                    "external": dict(POOLED_CONTROL), "rule": None}
        return {"against": "external_campaign",
                "external": _bare_control(arm.student, d_model, q_model,
                                          x_model),
                "rule": None}
    if source.external is None:
        return {"against": "fresh_bare", "rule": None}
    return {"against": "external_campaign",
            "external": {"model_key": CAMPAIGN_KEYS[arm.student],
                         "path": source.external["path"],
                         "sha256": source.external["sha256"]},
            "rule": None}


def build_config(source: Source, bench: str, arm: Arm, d_model: str,
                 q_model: str, x_model: str) -> Dict[str, Any]:
    compare = _test_compare(source, bench, arm, d_model, q_model, x_model)
    sequence, phases = arm.recipe(_final_test_phase(arm.test, compare))
    unused = set(phases) - set(sequence)
    undefined = set(sequence) - set(phases)
    if unused or undefined:
        raise ValueError(
            f"{arm.stem}: sequence and phase definitions disagree "
            f"(unused {sorted(unused)}, undefined {sorted(undefined)})")
    models = _models(arm.teacher, arm.student, d_model, q_model, x_model)
    config: Dict[str, Any] = {
        "benchmark": BENCHMARK_NAMES[bench],
        "invalid_redispatch_limit": 1,
        "mode": "split",
        "model_options": {d_model: {"reasoning_effort": "max"}},
        "models": models,
        "n_attempts_strong": 3,
        "n_attempts_weak": 3,
        "phases": {name: phases[name] for name in dict.fromkeys(sequence)},
        "rules": dict(RULES),
        "schema_version": 7,
        "sequence": sequence,
        "split": copy.deepcopy(source.split),
    }
    if source.tasks is not None:
        config["tasks"] = list(source.tasks)
    config["token_budgets"] = _token_budgets(models, d_model, q_model,
                                             x_model)
    config["use_weak_profile"] = False
    return config


def file_name(bench: str, arm: Arm) -> str:
    # The ALFWorld configs carry a v2 marker: the 2026-11-02 protocol
    # alignment changed execution, invalidated every earlier ALFWorld run,
    # and AGENTS section 6 forbids rewriting a config that a formal run
    # already started. The old ``alfworld_split_paper_*.yaml`` files stay
    # frozen as the record of the earlier protocol.
    if bench == "alfworld":
        return f"alfworld_split_paper_v2_{arm.stem}.yaml"
    return f"{bench}_split_paper_{arm.stem}.yaml"


def generate(source_dir: Path, d_models: Dict[str, str],
             q_model: str,
             x_model: str = DEFAULT_X_MODEL,
             stems: Optional[Sequence[str]] = None) -> Dict[str, Generated]:
    """Configs keyed by file name, in matrix order.

    ``stems`` restricts generation to the named arms (the bare arms are
    generated alone first: the with-bank arms' controls do not exist until
    those runs finish).
    """
    wanted = set(stems) if stems else None
    sources = {bench: load_source(bench, source_dir) for bench in BENCHMARKS}
    out: Dict[str, Generated] = {}
    for bench in BENCHMARKS:
        for arm in arms():
            if bench not in arm.benchmarks:
                continue
            if wanted is not None and arm.stem not in wanted:
                continue
            out[file_name(bench, arm)] = Generated(
                arm, bench,
                build_config(sources[bench], bench, arm, d_models[bench],
                             q_model, x_model))
    return out


def render(item: Generated) -> str:
    body = yaml.safe_dump(item.config, sort_keys=False, allow_unicode=True,
                          width=1_000_000)
    return f"{HEADER}\n# {item.arm.stem}: {item.arm.summary}\n{body}"


# ---------------------------------------------------------------------------
# Diff, check, index
# ---------------------------------------------------------------------------

def diff_paths(a: Any, b: Any, prefix: str = "",
               leaves: Sequence[str] = ()) -> List[str]:
    """Dotted paths at which two config dicts differ. A key present on one
    side only is reported as that key; a list is a leaf; a dict under a key
    named in ``leaves`` is compared whole instead of recursed into."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: List[str] = []
        for key in dict.fromkeys([*a, *b]):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in a or key not in b:
                out.append(path)
            elif key in leaves:
                if a[key] != b[key]:
                    out.append(path)
            else:
                out.extend(diff_paths(a[key], b[key], path, leaves))
        return out
    return [prefix] if a != b else []


def _in_generated_family(name: str) -> bool:
    """Whether a config file belongs to the CURRENT generation family.

    ``alfworld_split_paper_*.yaml`` without ``_v2_`` is the frozen record of
    the pre-unified-protocol runs; it is never regenerated and never stale.
    """
    return name.startswith(("tb21_split_paper_", "searchqa_split_paper_",
                            "alfworld_split_paper_v2_"))


def check(out_dir: Path, rendered: Dict[str, str]) -> List[str]:
    problems = []
    for name, text in rendered.items():
        path = out_dir / name
        if not path.exists():
            problems.append(f"missing: {name}")
        elif path.read_bytes() != text.encode("utf-8"):
            problems.append(f"differs: {name}")
    for path in sorted(out_dir.glob(FILE_GLOB)):
        if _in_generated_family(path.name) and path.name not in rendered:
            problems.append(f"stale: {path.name}")
    return problems


def index(generated: Dict[str, Generated]) -> str:
    """One markdown row per file: the arm's reference stem and the computed
    diff against that reference on the same benchmark (a ``curate`` block
    counts as one difference). ``e1_ours`` is the reference of itself."""
    configs = {(item.bench, item.arm.stem): item.config
               for item in generated.values()}
    lines = ["| experiment | arm | benchmark | file | diffed against | "
             "differs |",
             "|---|---|---|---|---|---|"]
    for name, item in generated.items():
        if item.arm.stem == item.arm.reference:
            reference, differs = "-", "reference"
        elif (item.bench, item.arm.reference) not in configs:
            reference, differs = item.arm.reference, "(not generated)"
        else:
            reference = item.arm.reference
            differs = ", ".join(diff_paths(
                item.config, configs[(item.bench, reference)],
                leaves=("curate",))) or "(identical)"
        arm = item.arm.stem.split("_", 1)[1]
        lines.append(f"| {item.arm.exp} | {arm} | {item.bench} | {name} | "
                     f"{reference} | {differs} |")
    return "\n".join(lines)


def _parse_d_models(values: Sequence[str]) -> Dict[str, str]:
    d_models = dict(DEFAULT_D_MODELS)
    for value in values:
        bench, sep, model = value.partition("=")
        if not sep or bench not in BENCHMARKS or not model:
            raise ValueError(
                f"--d-model expects BENCH=ID with BENCH in "
                f"{list(BENCHMARKS)}, got {value!r}")
        d_models[bench] = model
    return d_models


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the paper's experiment configs.")
    parser.add_argument("--out-dir", default=str(CONFIG_DIR),
                        help="where the YAMLs are written / checked")
    parser.add_argument("--source-dir", default=str(CONFIG_DIR),
                        help="where the source configs live")
    parser.add_argument("--d-model", action="append", default=[],
                        metavar="BENCH=ID",
                        help="gateway id of D for one benchmark")
    parser.add_argument("--q-model", default=DEFAULT_Q_MODEL)
    parser.add_argument("--x-model", default=DEFAULT_X_MODEL)
    parser.add_argument("--stems", default="",
                        help="comma-separated arm stems to generate (default: "
                             "all); the bare ALFWorld arms are generated "
                             "first because their runs produce the frozen "
                             "controls the other arms pin")
    parser.add_argument("--check", action="store_true",
                        help="verify the files; write nothing")
    parser.add_argument("--print-index", action="store_true",
                        help="print the arm index and exit")
    args = parser.parse_args(argv)
    stems = [stem.strip() for stem in args.stems.split(",") if stem.strip()]

    try:
        generated = generate(Path(args.source_dir),
                             _parse_d_models(args.d_model), args.q_model,
                             args.x_model, stems=stems or None)
    except (OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.print_index:
        print(index(generated))
        return 0

    rendered = {name: render(item) for name, item in generated.items()}
    out_dir = Path(args.out_dir)
    if args.check:
        problems = check(out_dir, rendered)
        for problem in problems:
            print(problem)
        print(f"{len(rendered)} configs, {len(problems)} problem(s)")
        return 1 if problems else 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (out_dir / name).write_bytes(text.encode("utf-8"))
    if not stems:
        # A partial generation (--stems) is not evidence that the other
        # arms are stale.
        for path in sorted(out_dir.glob(FILE_GLOB)):
            if _in_generated_family(path.name) and path.name not in rendered:
                print(f"warning: stale file not produced any more: "
                      f"{path.name}", file=sys.stderr)
    print(f"wrote {len(rendered)} configs to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
