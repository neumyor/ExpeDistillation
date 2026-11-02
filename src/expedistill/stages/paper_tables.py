"""Paper tables: read finished runs, compare arms, render markdown and JSON
(layer 7).  Read-only on every run directory, never calls a model.

A paper arm is a finished run whose test phase measured the with-bank arm
beside a control (the bare weak model: the in-phase ``fresh_bare`` arm, the
reused baseline or the external campaign).  The table spec names, per
experiment and benchmark, which run is which arm::

    {"main": {"alfworld": {"Ours": "runs/a", "SkillOpt": "runs/b"},
              "searchqa": {...}},
     "teacher_student": {...}, "granularity": {...}, "trigger": {...},
     "references": {"main": "Ours"}}

``references`` is optional: it maps an experiment to the arm label the other
arms are also compared against (an arm that is not a reference gets a second
paired comparison against it).  Rows follow the spec's order; nothing is
bolded, ranked or picked from the results.

The metric definitions are the pre-registered ones of ``blocks/metrics.py``
(``composites/paired_stats.py`` applies them to per-task scores).
``load_arm`` re-derives the three numbers from the trial answer sheets and
refuses a run whose ``phase_summary.json`` disagrees, so the table can never
quietly use another definition than the run's own summary.
"""

import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from expedistill.composites import paired_stats, phase_bank, phase_report
from expedistill.core.config import phase_label as make_phase_label
from expedistill.core.vocabulary import RUN_FAILURE, RUN_SUCCESS
from expedistill.infra import manifest as manifest_io
from expedistill.infra.store import read_json, write_json

__all__ = [
    "METRICS",
    "build_results",
    "compare_arms",
    "load_arm",
    "load_table_spec",
    "render_markdown",
    "run",
    "write_markdown",
    "write_results_json",
]

#: The three pre-registered metrics, primary first.  The markdown renders one
#: of them per call (default: the primary); the JSON always carries all three.
METRICS = ("avg_at_n", "avg_at_n_conservative", "pass_at_n")

_ARMS = ("with_bank", "control")
_SUMMARY_NAME = "phase_summary.json"
_REFERENCES_KEY = "references"
#: Summary and recomputed numbers are the same divisions of the same
#: integers; anything above float rounding is a different definition.
_SUMMARY_TOLERANCE = 1e-12


# ---------------------------------------------------------------------------
# Reading one arm
# ---------------------------------------------------------------------------

def _test_phase(manifest: Mapping[str, Any], requested: Optional[str]
                ) -> Tuple[str, str]:
    """``(label, local name)`` of the run's test phase in its frozen sequence."""
    config = manifest["config"]
    found = [(make_phase_label(index, name), name)
             for index, name in enumerate(config["sequence"])
             if config["phases"][name].get("role") == "test"]
    labels = [label for label, _ in found]
    if requested is not None:
        for label, name in found:
            if label == requested:
                return label, name
        raise ValueError(
            f"phase {requested!r} is not a test phase of this run; "
            f"its test phases: {labels}")
    if len(found) != 1:
        raise ValueError(
            f"the run's sequence has {len(found)} test phases {labels}; "
            "name the one to read with phase_label")
    return found[0]


def _check_against_summary(own: Mapping[str, Any], summary: Mapping[str, Any],
                           *, arm: str, run_dir: Path) -> None:
    recorded = summary[arm]
    for key in ("tasks", "avg_at_n", "avg_at_n_conservative", "pass_at_n"):
        mine, theirs = own[key], recorded[key]
        same = (mine is None and theirs is None) or (
            mine is not None and theirs is not None
            and math.isclose(mine, theirs, rel_tol=0.0,
                             abs_tol=_SUMMARY_TOLERANCE))
        if not same:
            raise ValueError(
                f"{run_dir}: the {arm} arm's {key} is {mine!r} from the "
                f"trial answer sheets but {theirs!r} in {_SUMMARY_NAME}; "
                "the two disagree, so neither can be used")


def load_arm(run_dir: Any, phase_label: Optional[str] = None
             ) -> Dict[str, Any]:
    """One finished run's test-phase scores, read-only.

    Returns ``{"with_bank": TaskScores, "control": TaskScores, "meta": {...}}``.
    A trial score is 1.0 (verdict success) or 0.0 (failure); a trial with
    any other verdict (invalid, never graded, missing, accepted hole) is
    NOT scored 0: it is left out of its task's list and named under
    ``meta["unscored"]``.  A task left with no scored trial keeps an empty
    list, so the conditional discipline can drop it and the conservative
    discipline can count it as 0, exactly as ``blocks/metrics.summarize``.
    The recomputed avg@N / conservative avg@N / pass@N must equal
    ``phase_summary.json`` or ValueError is raised.
    """
    run_dir = Path(run_dir)
    try:
        manifest = manifest_io.load(run_dir)
    except FileNotFoundError:
        raise ValueError(f"{run_dir} is not a run directory: no "
                         f"{manifest_io.MANIFEST_NAME}") from None
    label, name = _test_phase(manifest, phase_label)
    phase_dir = phase_bank.phase_bank_dir(run_dir, label)
    summary_path = phase_dir / _SUMMARY_NAME
    if not summary_path.exists():
        raise ValueError(
            f"{run_dir}: the test phase {label} has no {_SUMMARY_NAME}; "
            "it has not finished")
    summary = read_json(summary_path)
    report = phase_report.read_phase_report(
        phase_dir, phase_kind="test", learning_usable=False)
    if report is None:
        raise ValueError(
            f"{run_dir}: the test phase {label} has no trial-level "
            "answer sheets")

    sheets_by_unit: Dict[str, List[Any]] = {}
    for sheet in report.sheets:
        sheets_by_unit.setdefault(sheet.coverage.unit_id, []).append(sheet)
    scores: Dict[str, Dict[str, List[float]]] = {arm: {} for arm in _ARMS}
    unscored: Dict[str, List[Dict[str, Any]]] = {arm: [] for arm in _ARMS}
    expected: Dict[str, int] = {}
    roles = set()
    for unit in report.units:
        if unit.arm not in scores:
            raise ValueError(
                f"{run_dir}: unit {unit.unit_id} has arm {unit.arm!r}; a "
                f"test phase has only {list(_ARMS)}")
        if unit.task_ref in scores[unit.arm]:
            raise ValueError(
                f"{run_dir}: two {unit.arm} units for task {unit.task_ref}")
        trials: List[float] = []
        ordered = sorted(sheets_by_unit.get(unit.unit_id, []),
                         key=lambda sheet: sheet.coverage.attempt_index)
        for sheet in ordered:
            verdict = sheet.grading.verdict if sheet.grading else None
            if verdict == RUN_SUCCESS:
                trials.append(1.0)
            elif verdict == RUN_FAILURE:
                trials.append(0.0)
            else:
                unscored[unit.arm].append({
                    "task": unit.task_ref,
                    "attempt": sheet.coverage.attempt_index,
                    "reason": (sheet.coverage.cause or verdict
                               or "ungraded")})
        scores[unit.arm][unit.task_ref] = trials
        expected[unit.arm] = max(expected.get(unit.arm, 0),
                                 unit.expected_attempts)
        roles.add(unit.role)
    if set(scores["with_bank"]) != set(scores["control"]):
        raise ValueError(
            f"{run_dir}: the arms cover different tasks (with-bank "
            f"{len(scores['with_bank'])}, control {len(scores['control'])})")
    if not scores["with_bank"]:
        raise ValueError(f"{run_dir}: the test phase measured no task")
    if len(roles) != 1:
        raise ValueError(f"{run_dir}: the test phase mixes roles {roles}")
    for arm in _ARMS:
        _check_against_summary(paired_stats.arm_metrics(scores[arm]),
                               summary, arm=arm, run_dir=run_dir)

    role = roles.pop()
    config = manifest["config"]
    return {
        "with_bank": scores["with_bank"],
        "control": scores["control"],
        "meta": {
            "run_dir": str(run_dir),
            "phase_label": label,
            "benchmark": manifest["benchmark"],
            "mode": manifest["mode"],
            "role": role,
            "model": config["models"].get(f"executor_{role}"),
            "delivery": config["phases"][name]["delivery"].get("name"),
            "control_source": summary.get("control_source"),
            "n_tasks": len(scores["with_bank"]),
            "n_trials": {arm: sum(len(trials)
                                  for trials in scores[arm].values())
                         for arm in _ARMS},
            "expected_attempts": expected,
            "unscored": unscored,
            "infra_missing": summary.get("infra_missing", {}),
            "infra_accepted": summary.get("infra_accepted", {}),
            "complete": bool(summary.get("complete")),
        },
    }


# ---------------------------------------------------------------------------
# Comparing arms
# ---------------------------------------------------------------------------

def _paired_all(a: paired_stats.TaskScores, b: paired_stats.TaskScores, *,
                n_boot: int, seed: int, level: float) -> Dict[str, Any]:
    """The three metrics' paired statistics for ``a`` against ``b``."""
    common = dict(n_boot=n_boot, seed=seed, level=level)
    return {
        "avg_at_n": paired_stats.paired_mean_difference(
            a, b, unscored="exclude", **common),
        "avg_at_n_conservative": paired_stats.paired_mean_difference(
            a, b, unscored="zero", **common),
        "pass_at_n": paired_stats.pass_at_n_difference(a, b, **common),
    }


def compare_arms(arms: Mapping[str, Mapping[str, Any]], *,
                 reference: Optional[str] = None, n_boot: int = 10000,
                 seed: int = 0, level: float = 0.95,
                 n_resample: int = 2000) -> Dict[str, Any]:
    """Each arm's with-bank scores against its own control and against the
    ``reference`` arm's with-bank scores (None: no reference comparison).

    ``arms`` maps a label to a :func:`load_arm` result.  Per arm the result
    holds ``summary`` (the three metrics of both arms), ``vs_control`` and
    ``vs_reference`` (None for the reference itself or without one), each
    keyed by metric, and the control's ``noise_floor`` (None when no task has
    two scored control trials).  The reference arm's own control is not
    involved in ``vs_reference``: two arms are compared on their with-bank
    scores over the tasks both measured.
    """
    if reference is not None and reference not in arms:
        raise ValueError(
            f"reference arm {reference!r} is not among the arms "
            f"{list(arms)}")
    out: Dict[str, Any] = {}
    for label, arm in arms.items():
        control = arm["control"]
        splittable = any(len(trials) >= 2 for trials in control.values())
        out[label] = {
            "summary": {key: paired_stats.arm_metrics(arm[key])
                        for key in _ARMS},
            "vs_control": _paired_all(arm["with_bank"], control,
                                      n_boot=n_boot, seed=seed, level=level),
            "noise_floor": (paired_stats.noise_floor(
                control, n_resample=n_resample, seed=seed, level=level)
                if splittable else None),
            "vs_reference": (
                _paired_all(arm["with_bank"], arms[reference]["with_bank"],
                            n_boot=n_boot, seed=seed, level=level)
                if reference is not None and label != reference else None),
        }
    return {"reference": reference, "arms": out}


# ---------------------------------------------------------------------------
# The table spec and the whole pipeline
# ---------------------------------------------------------------------------

Spec = Dict[str, Dict[str, Dict[str, str]]]


def _parse_spec(raw: Any) -> Tuple[Spec, Dict[str, str]]:
    """Validate the spec; returns ``(experiments, references)`` in spec order."""
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("the table spec must be a non-empty JSON object")
    references = raw.get(_REFERENCES_KEY, {})
    if not isinstance(references, Mapping):
        raise ValueError(f"'{_REFERENCES_KEY}' must map experiment -> arm label")
    experiments: Spec = {}
    for experiment, benchmarks in raw.items():
        if experiment == _REFERENCES_KEY:
            continue
        if not isinstance(benchmarks, Mapping) or not benchmarks:
            raise ValueError(
                f"experiment {experiment!r} must map benchmark -> arms")
        experiments[experiment] = {}
        for benchmark, arms in benchmarks.items():
            if not isinstance(arms, Mapping) or not arms:
                raise ValueError(
                    f"{experiment}/{benchmark} must map arm label -> run dir")
            for label, run_dir in arms.items():
                if not isinstance(run_dir, str) or not run_dir:
                    raise ValueError(
                        f"{experiment}/{benchmark}/{label}: the run "
                        "directory must be a non-empty string")
            experiments[experiment][benchmark] = dict(arms)
    if not experiments:
        raise ValueError("the table spec names no experiment")
    for experiment, label in references.items():
        if experiment not in experiments:
            raise ValueError(
                f"references names experiment {experiment!r}, which the "
                f"spec does not have (experiments: {sorted(experiments)})")
        for benchmark, arms in experiments[experiment].items():
            if label not in arms:
                raise ValueError(
                    f"references[{experiment!r}] = {label!r} is not an arm "
                    f"of {experiment}/{benchmark} (arms: {sorted(arms)}); "
                    "the reference must be present in every benchmark of "
                    "its experiment")
    return experiments, dict(references)


def load_table_spec(path: Any) -> Dict[str, Any]:
    """The spec file's JSON, validated; relative run directories in it are
    relative to the working directory."""
    raw = read_json(Path(path))
    _parse_spec(raw)
    return raw


def _spec_run_dirs(raw: Any) -> List[Path]:
    experiments, _ = _parse_spec(raw)
    return [Path(run_dir)
            for benchmarks in experiments.values()
            for arms in benchmarks.values()
            for run_dir in arms.values()]


def build_results(table_spec: Mapping[str, Any], *, n_boot: int = 10000,
                  seed: int = 0, level: float = 0.95, n_resample: int = 2000
                  ) -> Dict[str, Any]:
    """Load every arm of the spec and compare them (nothing is written).

    A run directory used by several tables is read once.  The benchmark key
    of the spec must equal the run's own frozen benchmark, so a run placed
    under the wrong benchmark fails here instead of producing a table.
    """
    experiments, references = _parse_spec(table_spec)
    cache: Dict[Path, Dict[str, Any]] = {}
    out: Dict[str, Any] = {
        "settings": {"n_boot": n_boot, "seed": seed, "level": level,
                     "n_resample": n_resample},
        "experiments": {}}
    for experiment, benchmarks in experiments.items():
        reference = references.get(experiment)
        out["experiments"][experiment] = {
            "reference": reference, "benchmarks": {}}
        for benchmark, arms in benchmarks.items():
            loaded: Dict[str, Dict[str, Any]] = {}
            for label, run_dir in arms.items():
                key = Path(run_dir).resolve()
                if key not in cache:
                    cache[key] = load_arm(run_dir)
                arm = cache[key]
                if arm["meta"]["benchmark"] != benchmark:
                    raise ValueError(
                        f"{experiment}/{benchmark}/{label}: run {run_dir} "
                        f"is a {arm['meta']['benchmark']!r} run")
                loaded[label] = arm
            try:
                compared = compare_arms(
                    loaded, reference=reference, n_boot=n_boot, seed=seed,
                    level=level, n_resample=n_resample)
            except ValueError as exc:
                raise ValueError(f"{experiment}/{benchmark}: {exc}") from None
            out["experiments"][experiment]["benchmarks"][benchmark] = {
                "arms": {label: {
                    "run_dir": str(arms[label]),
                    "meta": loaded[label]["meta"],
                    "scores": {key: loaded[label][key] for key in _ARMS},
                    **compared["arms"][label]} for label in arms}}
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_METRIC_NAME = {"avg_at_n": "avg@N",
                "avg_at_n_conservative": "avg@N (conservative)",
                "pass_at_n": "pass@N"}

_METRIC_DEFINITION = {
    "avg_at_n": (
        "avg@N: mean over tasks of the per-task mean of scored trials "
        "(conditional: a task with no scored trial is left out of that "
        "comparison)"),
    "avg_at_n_conservative": (
        "avg@N (conservative): as avg@N, but every task counts and a task "
        "with no scored trial scores 0"),
    "pass_at_n": (
        "pass@N: share of tasks with at least one successful trial (a task "
        "with no scored trial counts and cannot pass)"),
}


def _number(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value + 0.0:.3f}"


def _signed(value: float) -> str:
    # "+ 0.0" turns a rounded -0.0 into 0.0 so no cell reads "-0.000".
    return f"{round(value, 3) + 0.0:+.3f}"


def _wlt(stat: Mapping[str, Any]) -> str:
    return f"{stat['wins']}/{stat['losses']}/{stat['ties']}"


def _interval(stat: Mapping[str, Any]) -> str:
    return (f"{_signed(stat['delta'])} "
            f"[{_signed(stat['ci_low'])}, {_signed(stat['ci_high'])}]")


def _noise_cell(arm: Mapping[str, Any], metric: str) -> str:
    if metric == "pass_at_n":
        return f"{arm['vs_control']['pass_at_n']['mcnemar_p']:.3f}"
    noise = arm["noise_floor"]
    return "n/a" if noise is None else f"{noise['sd']:.3f}"


def _ci_name(settings: Mapping[str, Any]) -> str:
    return f"{settings['level'] * 100:g}% CI"


def _footer(metric: str, reference: Optional[str],
            settings: Mapping[str, Any]) -> str:
    against = " and against " + reference if reference else ""
    tail = ("McNemar p: exact two-sided test on the tasks that pass in only "
            "one of the two arms."
            if metric == "pass_at_n" else
            "Control noise SD: SD of the difference between two random "
            f"halves of the control's own trials ({settings['n_resample']} "
            "resamples); an effect inside it cannot be told from rerun "
            "noise.")
    return (f"Metrics: {_METRIC_DEFINITION[metric]}. Delta: paired "
            f"difference of per-task scores against the control of the "
            f"same run{against}; [{_ci_name(settings)}]: percentile bootstrap "
            f"over tasks "
            f"({settings['n_boot']} resamples, seed {settings['seed']}). "
            f"W/L/T: tasks where the arm scores higher / lower / exactly "
            f"equal. {tail}")


def _notes(arms: Mapping[str, Mapping[str, Any]], metric: str,
           reference: Optional[str]) -> List[str]:
    unscored = [
        f"{label} {len(arm['meta']['unscored']['with_bank'])} with-bank, "
        f"{len(arm['meta']['unscored']['control'])} control"
        for label, arm in arms.items()
        if arm["meta"]["unscored"]["with_bank"]
        or arm["meta"]["unscored"]["control"]]
    dropped = []
    for label, arm in arms.items():
        counts = [("control", arm["vs_control"][metric])]
        if arm["vs_reference"] is not None:
            counts.append((str(reference), arm["vs_reference"][metric]))
        parts = [f"{len(stat['excluded_unscored'])} vs {against}"
                 for against, stat in counts if stat["excluded_unscored"]]
        if parts:
            dropped.append(f"{label} " + ", ".join(parts))
    unpaired = [
        f"{label} {len(arm['vs_reference'][metric]['only_a'])} only in the "
        f"arm, {len(arm['vs_reference'][metric]['only_b'])} only in "
        f"{reference}"
        for label, arm in arms.items()
        if arm["vs_reference"] is not None
        and (arm["vs_reference"][metric]["only_a"]
             or arm["vs_reference"][metric]["only_b"])]
    mismatched = [
        f"{label} ({arm['meta']['model']})" for label, arm in arms.items()
        if reference is not None and label != reference
        and arm["meta"]["model"] != arms[reference]["meta"]["model"]]
    notes = []
    if unscored:
        notes.append("Unscored trials (left out, never scored 0): "
                     + "; ".join(unscored) + ".")
    if dropped:
        notes.append("Tasks with no scored trial in one of the two compared "
                     "arms are left out of that delta: "
                     + "; ".join(dropped) + ".")
    if unpaired:
        notes.append("Tasks outside the common set of the reference "
                     "comparison: " + "; ".join(unpaired) + ".")
    if mismatched:
        notes.append(
            f"These arms ran another executor model than {reference} "
            f"({arms[reference]['meta']['model']}), so their delta vs "
            f"{reference} compares different models: "
            + "; ".join(mismatched) + ".")
    return notes


def _table(experiment: str, benchmark: str, spec_arms: Iterable[str],
           block: Mapping[str, Any], reference: Optional[str],
           metric: str, settings: Mapping[str, Any]) -> List[str]:
    arms = {label: block["arms"][label] for label in spec_arms}
    name = _METRIC_NAME[metric]
    ci = _ci_name(settings)
    header = [
        "Arm", f"{name} (delta vs control [{ci}])", "W/L/T vs control",
        f"control {name}",
        "McNemar p vs control" if metric == "pass_at_n"
        else "control noise SD"]
    if reference is not None:
        header += [f"delta vs {reference} [{ci}]", f"W/L/T vs {reference}"]
    lines = [f"### {experiment} / {benchmark}", "",
             "| " + " | ".join(header) + " |",
             "|" + "---|" * len(header)]
    for label, arm in arms.items():
        stat = arm["vs_control"][metric]
        row = [label,
               f"{_number(arm['summary']['with_bank'][metric])} "
               f"({_interval(stat)})",
               _wlt(stat),
               _number(arm["summary"]["control"][metric]),
               _noise_cell(arm, metric)]
        if reference is not None:
            ref_stat = (arm["vs_reference"] or {}).get(metric)
            if label == reference:
                row += ["-", "-"]
            elif ref_stat is None:
                row += ["n/a", "n/a"]
            else:
                row += [_interval(ref_stat), _wlt(ref_stat)]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", _footer(metric, reference, settings)]
    for note in _notes(arms, metric, reference):
        lines += ["", note]
    return lines


def render_markdown(table_spec: Mapping[str, Any],
                    results: Mapping[str, Any], *,
                    metric: str = "avg_at_n") -> str:
    """One markdown table per experiment and benchmark.

    Experiments, benchmarks and rows follow the spec's order.  ``metric`` is
    one of :data:`METRICS` (default the primary, avg@N conditional); the
    footer of every table names the definitions in force.
    """
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {METRICS}, got {metric!r}")
    experiments, _ = _parse_spec(table_spec)
    settings = results["settings"]
    parts: List[str] = []
    for experiment, benchmarks in experiments.items():
        block = results["experiments"][experiment]
        parts += [f"## {experiment}", ""]
        for benchmark, spec_arms in benchmarks.items():
            parts += _table(experiment, benchmark, spec_arms,
                            block["benchmarks"][benchmark],
                            block["reference"], metric, settings)
            parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _refuse_inside_runs(path: Path, run_dirs: Iterable[Any]) -> Path:
    target = Path(path).resolve()
    for run_dir in run_dirs:
        root = Path(run_dir).resolve()
        if target == root or root in target.parents:
            raise ValueError(
                f"refusing to write {path}: it is inside the run directory "
                f"{run_dir}; analysis output never goes into a run")
    return target


def write_results_json(results: Mapping[str, Any], path: Any, *,
                       run_dirs: Iterable[Any]) -> None:
    """Write ``results`` as JSON; a path under any of ``run_dirs`` is refused."""
    target = _refuse_inside_runs(Path(path), run_dirs)
    write_json(target, results, indent=2)


def write_markdown(text: str, path: Any, *, run_dirs: Iterable[Any]) -> None:
    """Write the markdown tables; a path under any of ``run_dirs`` is refused."""
    target = _refuse_inside_runs(Path(path), run_dirs)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)


def run(spec_path: Any, *, out_md: Any = None, out_json: Any = None,
        n_boot: int = 10000, seed: int = 0, metric: str = "avg_at_n"
        ) -> str:
    """The whole pipeline: spec file -> markdown (returned) and, when given,
    the markdown and JSON files.  Output paths are checked against every run
    directory in the spec BEFORE any run is read."""
    spec = load_table_spec(spec_path)
    run_dirs = _spec_run_dirs(spec)
    for path in (out_md, out_json):
        if path is not None:
            _refuse_inside_runs(Path(path), run_dirs)
    results = build_results(spec, n_boot=n_boot, seed=seed)
    text = render_markdown(spec, results, metric=metric)
    if out_md is not None:
        write_markdown(text, out_md, run_dirs=run_dirs)
    if out_json is not None:
        write_results_json(results, out_json, run_dirs=run_dirs)
    return text
