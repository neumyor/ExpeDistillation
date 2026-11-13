#!/usr/bin/env python
"""Generate the paper's experiment configs, one YAML per (arm, benchmark).

Purpose: the paper's main table (experiment e1, docs/decisions/
evidence_driven_evolution_20261113.md section 6) is a set of arms that must
differ in exactly the intended factors. Writing them by hand invites drift,
so the arms live here as data and the YAMLs are rendered from them. The
script builds dicts and dumps them with ``yaml.safe_dump(sort_keys=False)``;
it holds no method logic.

Inputs
    scripts/run/paper_splits.py: each benchmark's train / val / test
    (/ reserved) split, TB's task list and TB's external campaign control
    file. Gateway model ids per benchmark and the student id are parameters
    (--d-model BENCH=ID, --q-model ID); endpoints and keys never enter a
    config.

Outputs
    configs/experiments/<bench>_paper_<exp>_<arm>.yaml, bench in
    {tb21, alfworld, searchqa}.

Usage
    .venv/bin/python scripts/run/build_paper_configs.py            # write
    .venv/bin/python scripts/run/build_paper_configs.py --check    # verify
    .venv/bin/python scripts/run/build_paper_configs.py --print-index

``--check`` writes nothing and exits 1 when a file is missing, differs from
the render, or is stale (matches ``*_paper_*.yaml`` but is no longer
produced). A config whose formal run has started must never be regenerated
with different parameters (AGENTS section 6): change the script, then give
the new arm a new name.

Common to every config: schema 9, D = deepseek-v4-flash, Q =
qwen3.6-flash-distill, 3 attempts per baseline arm, one token-budget block
on every benchmark. The input budget follows the MODEL, not the role: 524288
for D and 262144 for Q, so a role Q fills is never declared a context it
does not have. The role table is executor_strong, executor_weak, segmenter,
author, rewriter and judge; the teacher (D) fills every role but
executor_weak, which the student fills.

The reference method (``e1_ours``) is the sequence baseline, learn,
practice, learn, practice, learn, test: one ``learn`` definition (review K
= 5, retry_limit 2, write_lessons; the ``subtask_cards`` author with its
default guards), a ``practice`` definition on the train list with the
``folder`` delivery and ``real_folder`` execution (``attempts`` ALFWorld 3,
TB 2, SearchQA 1) and the evolved playbook, and the folder test with the
evolved playbook (the test control never sees it). The test-only arms
(imported external banks and bare runs) have no learn phase, so they run
with the playbook off. TB tests use the external campaign control (model_key
qwen for student Q); ALFWorld tests use the frozen bare-run file of the
student model (``BARE_CONTROL_FILES``) as an external_campaign control;
SearchQA uses fresh_bare.

Arm index (``--print-index`` names the reference of every row and computes
the difference)
    e1  ours                the reference method
    e1  ours_val            ALFWorld only: practice on the val list (39
                            valid_seen games) instead of train
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
    e1  bare_q/bare_d       ALFWorld only: one bare run per student model
                            (empty bank, whole_bank), the run that freezes
                            that model's control file. Every with-bank
                            ALFWorld arm reads its test control from its
                            student's frozen file (external_campaign), so a
                            model is bare-run ONCE and every comparison
                            reuses it. Generate these first; the other arms
                            refuse to generate until their controls exist.
    e1  bare_x              the same config shape for X; the user stopped
                            after bare D (2026-11-02), so it is not run and
                            no control file is built for it

The ablation arms (e2 teacher -> student, e3 segmentation budget, e4
revision scope and no_skills, e5 coverage) are defined in the decision
document's section 6 and come back here when the ablations run.
"""

import argparse
import copy
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import yaml

# Loaded by file path in the tests, so the sibling data module is not on
# sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_splits  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "experiments"

BENCHMARKS = ("tb21", "alfworld", "searchqa")
LOCAL_BENCHMARKS = ("alfworld", "searchqa")
#: Benchmark prefix in the file name -> the plugin name in the config.
BENCHMARK_NAMES = {"tb21": "tb", "alfworld": "alfworld",
                   "searchqa": "searchqa"}

#: Gateway ids of D differ per benchmark; Q is one id.
DEFAULT_D_MODELS = {
    "tb21": "deepseek-v4-flash-0731--ali",
    "alfworld": "deepseek-v4-flash-0731-tencent",
    "searchqa": "deepseek-v4-flash-0731-tencent",
}
DEFAULT_Q_MODEL = "qwen3.6-flash-distill"
#: The third ALFWorld student a bare arm exists for.
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

#: The frozen bare run of each student model a with-bank ALFWorld arm has,
#: made under the unified protocol (2026-11-02). Every with-bank ALFWorld
#: arm reads its test control from the file of ITS student model: a model is
#: bare-run once, and every comparison reuses that measurement instead of
#: re-running a fresh control per arm. The files are built by
#: scripts/run/build_bare_control.py after the bare runs finish; the sha256
#: is read from disk at generation time and pinned.
SEED_DIR = REPO_ROOT / "configs" / "seed"
BARE_CONTROL_FILES = {
    "q": "alfworld_bare_q_20261102.json",
}

BASELINES = ("skillopt", "evoskill", "trace2skill", "gepa", "expel",
             "agentbrew")

FILE_GLOB = "*_paper_*.yaml"
HEADER = ("# Generated by scripts/run/build_paper_configs.py -- edit the "
          "script, not this file.")

DESCRIPTION_MODE = "summary_then_when_to_use"

ROLES = ("executor_strong", "executor_weak", "segmenter", "author",
         "rewriter", "judge")
#: The role the student fills; the teacher fills every other role.
STUDENT_ROLES = ("executor_weak",)

#: Input budget per model: D (reasoning, 512K context) and Q (256K).
D_INPUT_TOKENS = 524288
Q_INPUT_TOKENS = 262144

RULES = {
    "segment_select": "strong_success_weak_failed",
    "trajectory_select": "strong1_weak1",
}

#: The reference method's sequence (design spec 2.4).
SEQUENCE_OURS = ["baseline", "learn", "practice", "learn", "practice",
                 "learn", "test"]

#: Weak-model samples per task in each practice phase.
PRACTICE_ATTEMPTS = {"tb21": 2, "alfworld": 3, "searchqa": 1}


# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

def _learn_phase() -> Dict[str, Any]:
    """The one learn definition: the review parameters and the
    ``subtask_cards`` author with its default guards spelled out."""
    return {"author": {"name": "subtask_cards", "typing_chunk_size": 100,
                       "max_segments_per_card": 8,
                       "max_chars_per_segment": 1200,
                       "write_retry_limit": 2},
            "review": {"name": "review", "K": 5, "retry_limit": 2,
                       "write_lessons": True},
            "kind": "learn", "role": "learn"}


def _baseline_phase() -> Dict[str, Any]:
    return {"acceptance": None,
            "delivery": {"name": "none"},
            "execution": {"arms": ["executor_strong", "executor_weak"],
                          "name": "real"},
            "kind": "execute", "role": "baseline", "tasks": "train"}


def _practice_phase(tasks: str, attempts: int) -> Dict[str, Any]:
    """The practice phase: the final test's folder presentation on the
    train or val list, the weak executor only."""
    return {"acceptance": None,
            "delivery": {"description_mode": DESCRIPTION_MODE,
                         "name": "folder"},
            "execution": {"attempts": attempts, "name": "real_folder"},
            "kind": "execute", "playbook": "evolved", "role": "practice",
            "tasks": tasks}


def _final_test_phase(kind: str, compare: Dict[str, Any],
                      playbook: str) -> Dict[str, Any]:
    """The test phase: ``folder`` pairs with real_folder, ``whole`` is the
    whole-bank injection under plain real, ``whole_verbatim`` the same with
    every skill text injected exactly as stored (imported baseline
    documents must not be flattened). The sampling depth is the global
    n_attempts_weak."""
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
    phase = {"acceptance": None, "compare": compare, "delivery": delivery,
             "execution": execution, "kind": "execute", "role": "test",
             "tasks": "test"}
    if playbook != "off":
        phase["playbook"] = playbook
    return phase


# ---------------------------------------------------------------------------
# The arms
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arm:
    stem: str
    exp: str
    summary: str
    #: True: the reference method's sequence; False: the test phase only.
    method: bool = True
    benchmarks: Tuple[str, ...] = BENCHMARKS
    student: str = "q"
    test: str = "folder"
    #: The list every practice phase runs (``train`` or ``val``).
    practice_tasks: str = "train"
    #: The stem ``--print-index`` diffs this arm against.
    reference: str = "e1_ours"


def arms() -> List[Arm]:
    out = [
        Arm("e1_ours", "e1",
            "reference: baseline, learn, practice, learn, practice, learn, "
            "test; teacher D, student Q"),
        Arm("e1_ours_val", "e1",
            "the reference method practising on the val list",
            benchmarks=("alfworld",), practice_tasks="val"),
    ]
    for baseline in BASELINES:
        out.append(Arm(
            f"e1_{baseline}_folder", "e1",
            f"{baseline} bank, test only, folder + real_folder",
            method=False))
        out.append(Arm(
            f"e1_{baseline}_whole", "e1",
            f"{baseline} bank, test only, whole_bank (verbatim) + real",
            method=False, test="whole_verbatim"))
    out.append(Arm(
        "e1_bare", "e1",
        "bare: test only, whole_bank on an empty bank + fresh_bare",
        method=False, benchmarks=("searchqa",), test="whole"))
    for student in ("q", "d", "x"):
        out.append(Arm(
            f"e1_bare_{student}", "e1",
            f"bare {student.upper()}: test only, whole_bank on an empty "
            "bank; the run that freezes that model's control file",
            method=False, benchmarks=("alfworld",), student=student,
            test="whole"))
    return out


def phases_of(arm: Arm, bench: str, test: Dict[str, Any]
              ) -> Tuple[List[str], Dict[str, Any]]:
    """The arm's sequence and phase definitions around its test phase."""
    if not arm.method:
        return ["test"], {"test": test}
    phases = {
        "baseline": _baseline_phase(),
        "learn": _learn_phase(),
        "practice": _practice_phase(arm.practice_tasks,
                                    PRACTICE_ATTEMPTS[bench]),
        "test": test,
    }
    return list(SEQUENCE_OURS), phases


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


def load_source(bench: str) -> Source:
    """The benchmark's split (and TB's task list and control file identity)
    from paper_splits."""
    tasks = paper_splits.TASKS.get(bench)
    return Source(
        split=copy.deepcopy(paper_splits.SPLITS[bench]),
        tasks=list(tasks) if tasks is not None else None,
        external=(dict(paper_splits.TB21_EXTERNAL) if bench == "tb21"
                  else None))


def _models(student: str, d_model: str, q_model: str,
            x_model: str) -> Dict[str, str]:
    by_letter = {"d": d_model, "q": q_model, "x": x_model}
    return {role: by_letter[student] if role in STUDENT_ROLES else d_model
            for role in ROLES}


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
                    "external": dict(POOLED_CONTROL)}
        return {"against": "external_campaign",
                "external": _bare_control(arm.student, d_model, q_model,
                                          x_model)}
    if source.external is None:
        return {"against": "fresh_bare"}
    return {"against": "external_campaign",
            "external": {"model_key": CAMPAIGN_KEYS[arm.student],
                         "path": source.external["path"],
                         "sha256": source.external["sha256"]}}


def build_config(source: Source, bench: str, arm: Arm, d_model: str,
                 q_model: str, x_model: str,
                 explicit_cache_models: Sequence[str] = (),
                 cache_prefix_tokens: Optional[int] = None) -> Dict[str, Any]:
    compare = _test_compare(source, bench, arm, d_model, q_model, x_model)
    test = _final_test_phase(arm.test, compare,
                             "evolved" if arm.method else "off")
    sequence, phases = phases_of(arm, bench, test)
    models = _models(arm.student, d_model, q_model, x_model)
    model_options: Dict[str, Dict[str, Any]] = {
        d_model: {"reasoning_effort": "max"}}
    for model_name in explicit_cache_models:
        options = model_options.setdefault(model_name, {})
        options["cache_mode"] = "explicit"
        if cache_prefix_tokens is not None:
            options["cache_prefix_tokens"] = cache_prefix_tokens
    config: Dict[str, Any] = {
        "benchmark": BENCHMARK_NAMES[bench],
        "invalid_redispatch_limit": 1,
        "model_options": model_options,
        "models": models,
        "n_attempts_strong": 3,
        "n_attempts_weak": 3,
        "phases": {name: phases[name] for name in dict.fromkeys(sequence)},
        "rules": dict(RULES),
        "schema_version": 9,
        "sequence": sequence,
        "split": copy.deepcopy(source.split),
    }
    if source.tasks is not None:
        config["tasks"] = list(source.tasks)
    config["token_budgets"] = _token_budgets(models, d_model, q_model,
                                             x_model)
    return config


def file_name(bench: str, arm: Arm) -> str:
    return f"{bench}_paper_{arm.stem}.yaml"


def generate(d_models: Dict[str, str],
             q_model: str,
             x_model: str = DEFAULT_X_MODEL,
             stems: Optional[Sequence[str]] = None,
             explicit_cache_models: Sequence[str] = (),
             cache_prefix_tokens: Optional[int] = None
             ) -> Dict[str, Generated]:
    """Configs keyed by file name, in matrix order.

    ``stems`` restricts generation to the named arms (the bare arms are
    generated alone first: the with-bank arms' controls do not exist until
    those runs finish).
    """
    wanted = set(stems) if stems else None
    sources = {bench: load_source(bench) for bench in BENCHMARKS}
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
                             q_model, x_model,
                             explicit_cache_models=explicit_cache_models,
                             cache_prefix_tokens=cache_prefix_tokens))
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


def _stale(out_dir: Path, rendered: Dict[str, str]) -> List[str]:
    return [path.name for path in sorted(out_dir.glob(FILE_GLOB))
            if path.name not in rendered]


def check(out_dir: Path, rendered: Dict[str, str]) -> List[str]:
    problems = []
    for name, text in rendered.items():
        path = out_dir / name
        if not path.exists():
            problems.append(f"missing: {name}")
        elif path.read_bytes() != text.encode("utf-8"):
            problems.append(f"differs: {name}")
    problems.extend(f"stale: {name}" for name in _stale(out_dir, rendered))
    return problems


def index(generated: Dict[str, Generated]) -> str:
    """One markdown row per file: the arm's reference stem and the computed
    diff against that reference on the same benchmark. ``e1_ours`` is the
    reference of itself."""
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
                item.config, configs[(item.bench, reference)])) \
                or "(identical)"
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
    parser.add_argument("--d-model", action="append", default=[],
                        metavar="BENCH=ID",
                        help="gateway id of D for one benchmark")
    parser.add_argument("--q-model", default=DEFAULT_Q_MODEL)
    parser.add_argument("--x-model", default=DEFAULT_X_MODEL)
    parser.add_argument("--explicit-cache-model", action="append", default=[],
                        metavar="ID",
                        help="model id that gets the generic client's "
                             "explicit cache markers (cache_mode: explicit); "
                             "repeatable. Default: implicit cache for every "
                             "model. Combine with --stems so a new campaign "
                             "does not rewrite frozen configs")
    parser.add_argument("--cache-prefix-tokens", type=int, default=None,
                        help="marked stable-prefix budget for the explicit "
                             "cache models (client default: 1200)")
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
        generated = generate(_parse_d_models(args.d_model), args.q_model,
                             args.x_model, stems=stems or None,
                             explicit_cache_models=args.explicit_cache_model,
                             cache_prefix_tokens=args.cache_prefix_tokens)
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
        for name in _stale(out_dir, rendered):
            print(f"warning: stale file not produced any more: {name}",
                  file=sys.stderr)
    print(f"wrote {len(rendered)} configs to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
