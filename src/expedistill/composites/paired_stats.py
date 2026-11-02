"""Paired statistics for comparing two arms over the same tasks (layer 5, pure).

Standard library only, deterministic for a fixed seed, never reads a run.
The pre-registered metric definitions are the ones ``blocks/metrics.py``
freezes (README 5.5); this module applies them to per-task trial scores:

* ``avg@N`` -- per task, the mean of that task's SCORED trials, then the
  mean over tasks.  A task with no scored trial has no mean: the
  conditional discipline (``unscored="exclude"``) drops it, the
  conservative discipline (``unscored="zero"``) scores the task 0.
* ``pass@N`` -- the share of tasks with at least one full-credit trial
  (score 1).  A task with no scored trial counts in the denominator and
  can never pass, exactly as ``blocks/metrics.summarize`` does.

The comparison unit is the TASK, never the trial: trials of one task are
not independent draws of the benchmark, so the confidence interval
bootstraps over tasks (resample tasks with replacement, recompute the mean
paired difference, take percentiles).  Wins / losses / ties compare the
two per-task means exactly (``fractions.Fraction``); scores are read as the
decimal numbers they print as, so 0.1 + 0.2 ties with 0.3.

``noise_floor`` estimates how far two independent runs of the SAME arm
differ by chance, which is the smallest effect a comparison can resolve.
"""

import math
import random
import statistics
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "TaskScores",
    "arm_metrics",
    "mcnemar_exact",
    "noise_floor",
    "paired_mean_difference",
    "pass_at_n_difference",
]

#: One arm: task id -> that task's scored trials (each in [0, 1]).  An empty
#: list is a task that was expected but has no scored trial.
TaskScores = Mapping[str, Sequence[float]]

UNSCORED_POLICIES = ("exclude", "zero")


def _fraction(score: Any) -> Fraction:
    if isinstance(score, bool) or not isinstance(score, (int, float, Fraction)):
        raise ValueError(f"a trial score must be a number, got {score!r}")
    # repr(float) is the shortest decimal that round-trips, so a score
    # typed as 0.1 is the fraction 1/10, not its binary approximation.
    value = Fraction(repr(score)) if isinstance(score, float) else Fraction(score)
    if not 0 <= value <= 1:
        raise ValueError(f"a trial score must lie in [0, 1], got {score!r}")
    return value


def _task_mean(trials: Sequence[float]) -> Optional[Fraction]:
    if not trials:
        return None
    return sum((_fraction(score) for score in trials), Fraction(0)) / len(trials)


def _task_pass(trials: Sequence[float]) -> Fraction:
    return Fraction(int(any(_fraction(score) == 1 for score in trials)))


def _check_policy(unscored: str) -> None:
    if unscored not in UNSCORED_POLICIES:
        raise ValueError(
            f"unscored must be one of {UNSCORED_POLICIES}, got {unscored!r}")


def arm_metrics(scores: TaskScores) -> Dict[str, Any]:
    """One arm's three pre-registered numbers, same keys as the test
    phase summary: ``avg_at_n`` (conditional, None with no scored task),
    ``avg_at_n_conservative`` and ``pass_at_n``."""
    n_tasks = len(scores)
    means = [mean for mean in map(_task_mean, scores.values())
             if mean is not None]
    return {
        "tasks": n_tasks,
        "scored_tasks": len(means),
        "avg_at_n": float(sum(means) / len(means)) if means else None,
        "avg_at_n_conservative": (float(sum(means) / n_tasks)
                                  if n_tasks else 0.0),
        "pass_at_n": (float(sum(_task_pass(trials)
                                for trials in scores.values()) / n_tasks)
                      if n_tasks else 0.0),
    }


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (position q * (n - 1) of the sorted
    values), the usual default of numerical packages."""
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return (sorted_values[low]
            + (sorted_values[high] - sorted_values[low]) * (position - low))


def _interval(values: Sequence[float], level: float) -> Tuple[float, float]:
    ordered = sorted(values)
    tail = (1.0 - level) / 2.0
    return _quantile(ordered, tail), _quantile(ordered, 1.0 - tail)


def _check_level(level: float) -> None:
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must lie in (0, 1), got {level!r}")


def _bootstrap_interval(diffs: Sequence[float], *, n_boot: int, seed: int,
                        level: float) -> Tuple[float, float]:
    """Percentile interval of the mean of ``diffs`` resampled over tasks."""
    if n_boot < 1:
        raise ValueError(f"n_boot must be at least 1, got {n_boot!r}")
    _check_level(level)
    rng = random.Random(seed)
    n = len(diffs)
    means = [sum(rng.choices(diffs, k=n)) / n for _ in range(n_boot)]
    return _interval(means, level)


def _paired_result(pairs: Sequence[Tuple[str, Fraction, Fraction]], *,
                   n_boot: int, seed: int, level: float,
                   only_a: Sequence[str], only_b: Sequence[str],
                   excluded_unscored: Sequence[str]) -> Dict[str, Any]:
    n = len(pairs)
    mean_a = sum((a for _, a, _ in pairs), Fraction(0)) / n
    mean_b = sum((b for _, _, b in pairs), Fraction(0)) / n
    low, high = _bootstrap_interval(
        [float(a - b) for _, a, b in pairs],
        n_boot=n_boot, seed=seed, level=level)
    return {
        "n_tasks": n,
        "mean_a": float(mean_a),
        "mean_b": float(mean_b),
        "delta": float(mean_a - mean_b),
        "ci_low": low,
        "ci_high": high,
        "wins": sum(1 for _, a, b in pairs if a > b),
        "losses": sum(1 for _, a, b in pairs if a < b),
        "ties": sum(1 for _, a, b in pairs if a == b),
        "only_a": list(only_a),
        "only_b": list(only_b),
        "excluded_unscored": list(excluded_unscored),
    }


def _split_tasks(a: TaskScores, b: TaskScores) -> Tuple[List[str], List[str],
                                                        List[str]]:
    return (sorted(set(a) & set(b)), sorted(set(a) - set(b)),
            sorted(set(b) - set(a)))


def paired_mean_difference(
        a: TaskScores, b: TaskScores, *, n_boot: int = 10000, seed: int = 0,
        level: float = 0.95, unscored: str = "exclude") -> Dict[str, Any]:
    """Mean over tasks of (per-task mean of ``a`` - per-task mean of ``b``).

    Only tasks present in BOTH arms enter; tasks in one arm only are listed
    under ``only_a`` / ``only_b``.  A task with no scored trial in either
    arm is dropped and listed under ``excluded_unscored``
    (``unscored="exclude"``, the conditional discipline) or scored 0 in that
    arm (``"zero"``, the conservative discipline).  ``mean_a`` / ``mean_b``
    are the arm means over the paired tasks, so ``delta = mean_a - mean_b``.
    ``wins`` / ``losses`` / ``ties`` count tasks whose ``a`` mean is higher /
    lower / exactly equal.  ``ci_low`` / ``ci_high`` are the percentile
    bootstrap interval of ``delta`` over tasks.  Empty pairing raises
    ValueError.
    """
    _check_policy(unscored)
    common, only_a, only_b = _split_tasks(a, b)
    pairs: List[Tuple[str, Fraction, Fraction]] = []
    excluded: List[str] = []
    for task in common:
        mean_a, mean_b = _task_mean(a[task]), _task_mean(b[task])
        if mean_a is None or mean_b is None:
            if unscored == "exclude":
                excluded.append(task)
                continue
            mean_a = Fraction(0) if mean_a is None else mean_a
            mean_b = Fraction(0) if mean_b is None else mean_b
        pairs.append((task, mean_a, mean_b))
    if not pairs:
        raise ValueError(
            "the two arms share no scored task "
            f"(common {len(common)}, unscored {len(excluded)}, "
            f"only in a {len(only_a)}, only in b {len(only_b)})")
    return _paired_result(pairs, n_boot=n_boot, seed=seed, level=level,
                          only_a=only_a, only_b=only_b,
                          excluded_unscored=excluded)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts.

    ``b`` tasks pass only in arm A, ``c`` only in arm B.  Under the null the
    ``n = b + c`` discordant tasks split Binomial(n, 1/2), so

        p = min(1, 2 * sum_{i=0}^{min(b, c)} C(n, i) / 2^n)

    and p = 1 when there is no discordant task.
    """
    if b < 0 or c < 0:
        raise ValueError(f"discordant counts must be >= 0, got {b}, {c}")
    n = b + c
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return min(1.0, 2 * tail / 2 ** n)


def pass_at_n_difference(
        a: TaskScores, b: TaskScores, *, n_boot: int = 10000, seed: int = 0,
        level: float = 0.95) -> Dict[str, Any]:
    """The paired difference of the per-task indicator "at least one
    success", same layout as :func:`paired_mean_difference`.

    A task with no scored trial counts as not passed (the summary's
    denominator), so no task is dropped for lack of scores.  ``wins`` /
    ``losses`` are the discordant tasks (``a_only`` / ``b_only``) and
    ``mcnemar_p`` is :func:`mcnemar_exact` over them.
    """
    common, only_a, only_b = _split_tasks(a, b)
    pairs = [(task, _task_pass(a[task]), _task_pass(b[task]))
             for task in common]
    if not pairs:
        raise ValueError(
            "the two arms share no task "
            f"(only in a {len(only_a)}, only in b {len(only_b)})")
    result = _paired_result(pairs, n_boot=n_boot, seed=seed, level=level,
                            only_a=only_a, only_b=only_b,
                            excluded_unscored=[])
    result["a_only"] = result["wins"]
    result["b_only"] = result["losses"]
    result["mcnemar_p"] = mcnemar_exact(result["wins"], result["losses"])
    return result


def noise_floor(scores: TaskScores, *, n_resample: int = 2000, seed: int = 0,
                level: float = 0.95) -> Dict[str, Any]:
    """How far two independent runs of one arm differ by chance.

    Each resample splits every task's k trials at random into two halves of
    ``k // 2`` trials (an odd k leaves one trial out), takes each half's
    arm mean over tasks and records half A minus half B.  ``sd`` and the
    percentile interval ``ci_low`` / ``ci_high`` describe those differences:
    an observed arm difference inside that range cannot be told apart from
    rerun noise.  The halves hold fewer trials than the arm, so the raw SD
    overstates the noise of two full-depth arms; ``sd_at_full_depth``
    rescales it by ``sqrt(half_size / k)`` (independent trials) and is None
    when tasks have different depths.  Tasks with fewer than 2 scored trials
    cannot be split and are listed under ``excluded_tasks``; no splittable
    task raises ValueError.
    """
    if n_resample < 2:
        raise ValueError(f"n_resample must be at least 2, got {n_resample!r}")
    _check_level(level)
    trials: Dict[str, List[float]] = {}
    excluded: List[str] = []
    for task in sorted(scores):
        values = [float(_fraction(score)) for score in scores[task]]
        if len(values) < 2:
            excluded.append(task)
        else:
            trials[task] = values
    if not trials:
        raise ValueError("no task has at least 2 scored trials to split")
    rng = random.Random(seed)
    halves = {task: len(values) // 2 for task, values in trials.items()}
    diffs = []
    for _ in range(n_resample):
        total = 0.0
        for task, values in trials.items():
            half = halves[task]
            pick = rng.sample(values, 2 * half)
            total += sum(pick[:half]) / half - sum(pick[half:]) / half
        diffs.append(total / len(trials))
    low, high = _interval(diffs, level)
    depths = {len(values) for values in trials.values()}
    sd = statistics.stdev(diffs)
    return {
        "n_tasks": len(trials),
        "excluded_tasks": excluded,
        "trials_per_task": {"min": min(depths), "max": max(depths)},
        "sd": sd,
        "ci_low": low,
        "ci_high": high,
        "sd_at_full_depth": (sd * math.sqrt((min(depths) // 2) / min(depths))
                             if len(depths) == 1 else None),
    }
