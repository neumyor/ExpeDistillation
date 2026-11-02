"""Paired statistics: every function against a hand-computed small case.

The bootstrap cases are built so the interval is fixed by the data rather
than by the seed (a 4-task difference vector with two zeros and two equal
positive values: the resampled mean hits both extremes in 1/16 of the
draws, far above a 2.5% tail).  Seed determinism is tested separately.
"""

import math
import random

import pytest

from expedistill.composites import paired_stats as ps


# ---------------------------------------------------------------------------
# arm_metrics
# ---------------------------------------------------------------------------

def test_arm_metrics_follow_the_summary_definitions():
    scores = {"t1": [1, 0, 0], "t2": [], "t3": [1]}

    metrics = ps.arm_metrics(scores)

    # t1 = 1/3, t3 = 1; t2 has no scored trial.
    assert metrics["tasks"] == 3
    assert metrics["scored_tasks"] == 2
    assert metrics["avg_at_n"] == pytest.approx((1 / 3 + 1) / 2)
    assert metrics["avg_at_n_conservative"] == pytest.approx((1 / 3 + 1) / 3)
    assert metrics["pass_at_n"] == pytest.approx(2 / 3)


def test_arm_metrics_with_no_scored_task_has_no_conditional_average():
    metrics = ps.arm_metrics({"t1": [], "t2": []})

    assert metrics["avg_at_n"] is None
    assert metrics["avg_at_n_conservative"] == 0.0
    assert metrics["pass_at_n"] == 0.0


def test_arm_metrics_pass_needs_full_credit():
    assert ps.arm_metrics({"t1": [0.5, 0.75]})["pass_at_n"] == 0.0
    assert ps.arm_metrics({"t1": [0.5, 1.0]})["pass_at_n"] == 1.0


# ---------------------------------------------------------------------------
# paired_mean_difference
# ---------------------------------------------------------------------------

A = {"t1": [1, 1], "t2": [1, 0], "t3": [0, 0], "t4": [1, 1]}
B = {"t1": [1, 0], "t2": [0, 0], "t3": [0, 0], "t4": [1, 1]}


def test_paired_mean_difference_hand_computed():
    result = ps.paired_mean_difference(A, B, n_boot=2000, seed=0)

    # per-task means a: 1, .5, 0, 1; b: .5, 0, 0, 1; differences .5, .5, 0, 0
    assert result["n_tasks"] == 4
    assert result["mean_a"] == 0.625
    assert result["mean_b"] == 0.375
    assert result["delta"] == 0.25
    assert (result["wins"], result["losses"], result["ties"]) == (2, 0, 2)
    assert result["ci_low"] == 0.0
    assert result["ci_high"] == 0.5
    assert result["only_a"] == [] and result["only_b"] == []
    assert result["excluded_unscored"] == []


def test_swapping_the_arms_negates_the_difference():
    result = ps.paired_mean_difference(B, A, n_boot=2000, seed=0)

    assert result["delta"] == -0.25
    assert (result["wins"], result["losses"], result["ties"]) == (0, 2, 2)
    assert result["ci_low"] == -0.5
    assert result["ci_high"] == 0.0


def test_the_unit_of_resampling_is_the_task_not_the_trial():
    # One task with 100 trials and one with 1: a trial bootstrap would be
    # dominated by the first task; the task bootstrap sees two differences.
    a = {"big": [1] * 100, "small": [0]}
    b = {"big": [0] * 100, "small": [0]}

    result = ps.paired_mean_difference(a, b, n_boot=2000, seed=0)

    assert result["n_tasks"] == 2
    assert result["delta"] == 0.5
    assert (result["ci_low"], result["ci_high"]) == (0.0, 1.0)


def test_the_same_seed_gives_the_same_result_and_another_seed_another_interval():
    rng = random.Random(7)
    a = {f"t{i}": [round(rng.random(), 2) for _ in range(3)]
         for i in range(30)}
    b = {f"t{i}": [round(rng.random(), 2) for _ in range(3)]
         for i in range(30)}

    first = ps.paired_mean_difference(a, b, n_boot=500, seed=1)
    again = ps.paired_mean_difference(a, b, n_boot=500, seed=1)
    other = ps.paired_mean_difference(a, b, n_boot=500, seed=2)

    assert first == again
    assert (first["ci_low"], first["ci_high"]) \
        != (other["ci_low"], other["ci_high"])
    assert first["delta"] == other["delta"]
    assert first["ci_low"] < first["delta"] < first["ci_high"]


def test_ties_compare_exact_fractions_not_floats():
    # 0.1 + 0.2 != 0.3 in binary floats; the decimals they print as tie.
    a = {"t1": [0.1, 0.2], "t2": [0.1, 0.2]}
    b = {"t1": [0.15], "t2": [0.3, 0.0]}

    result = ps.paired_mean_difference(a, b, n_boot=100)

    assert (result["wins"], result["losses"], result["ties"]) == (0, 0, 2)
    assert result["delta"] == 0.0


def test_fractional_rewards_are_averaged_as_given():
    a = {"t1": [0.5, 0.25], "t2": [1.0]}
    b = {"t1": [0.0], "t2": [0.5, 0.5]}

    result = ps.paired_mean_difference(a, b, n_boot=100)

    # t1: .375 - 0 ; t2: 1 - .5
    assert result["delta"] == pytest.approx((0.375 + 0.5) / 2)
    assert (result["wins"], result["losses"], result["ties"]) == (2, 0, 0)


def test_tasks_in_one_arm_only_are_listed_and_excluded():
    a = {"t1": [1], "t2": [0], "only_a": [1]}
    b = {"t1": [0], "t2": [0], "only_b": [1]}

    result = ps.paired_mean_difference(a, b, n_boot=100)

    assert result["n_tasks"] == 2
    assert result["only_a"] == ["only_a"]
    assert result["only_b"] == ["only_b"]
    assert result["delta"] == 0.5


def test_no_common_task_raises():
    with pytest.raises(ValueError, match="share no scored task"):
        ps.paired_mean_difference({"t1": [1]}, {"t2": [1]})
    with pytest.raises(ValueError, match="share no scored task"):
        ps.paired_mean_difference({}, {})


def test_conditional_discipline_drops_unscored_tasks_and_lists_them():
    a = {"t1": [1], "t2": []}
    b = {"t1": [0], "t2": [1]}

    result = ps.paired_mean_difference(a, b, n_boot=100, unscored="exclude")

    assert result["n_tasks"] == 1
    assert result["excluded_unscored"] == ["t2"]
    assert result["delta"] == 1.0


def test_conservative_discipline_scores_an_unscored_task_zero():
    a = {"t1": [1], "t2": []}
    b = {"t1": [0], "t2": [1]}

    result = ps.paired_mean_difference(a, b, n_boot=100, unscored="zero")

    # t1: 1 - 0 ; t2: 0 - 1
    assert result["n_tasks"] == 2
    assert result["excluded_unscored"] == []
    assert result["delta"] == 0.0
    assert (result["wins"], result["losses"], result["ties"]) == (1, 1, 0)


def test_all_tasks_unscored_in_the_conditional_discipline_raises():
    with pytest.raises(ValueError, match="share no scored task"):
        ps.paired_mean_difference({"t1": []}, {"t1": [1]},
                                  unscored="exclude")


@pytest.mark.parametrize("bad", [1.5, -0.1, True, "1", None])
def test_a_trial_score_outside_zero_one_or_not_a_number_raises(bad):
    with pytest.raises(ValueError, match="trial score"):
        ps.paired_mean_difference({"t1": [bad]}, {"t1": [0]}, n_boot=10)


def test_argument_checks():
    with pytest.raises(ValueError, match="unscored"):
        ps.paired_mean_difference(A, B, unscored="maybe")
    with pytest.raises(ValueError, match="n_boot"):
        ps.paired_mean_difference(A, B, n_boot=0)
    with pytest.raises(ValueError, match="level"):
        ps.paired_mean_difference(A, B, level=1.0)


# ---------------------------------------------------------------------------
# pass_at_n_difference and mcnemar_exact
# ---------------------------------------------------------------------------

def test_mcnemar_exact_matches_the_binomial_formula():
    assert ps.mcnemar_exact(0, 0) == 1.0
    assert ps.mcnemar_exact(1, 1) == 1.0
    assert ps.mcnemar_exact(5, 5) == 1.0
    # n = 5, one-sided tail C(5,0)/32, doubled
    assert ps.mcnemar_exact(5, 0) == pytest.approx(2 / 32)
    assert ps.mcnemar_exact(0, 5) == ps.mcnemar_exact(5, 0)
    # n = 3, tail (1 + 3) / 8, doubled and capped at 1
    assert ps.mcnemar_exact(1, 2) == 1.0
    assert ps.mcnemar_exact(0, 3) == pytest.approx(2 / 8)
    # n = 10, tail (1 + 10 + 45) / 1024, doubled
    assert ps.mcnemar_exact(2, 8) == pytest.approx(112 / 1024)
    # large n stays exact
    assert ps.mcnemar_exact(0, 60) == pytest.approx(2 / 2 ** 60)
    # 60 heads in 100 fair flips: the textbook two-sided p of 0.0569
    assert ps.mcnemar_exact(40, 60) == pytest.approx(0.0568879, abs=1e-6)


def test_mcnemar_exact_rejects_negative_counts():
    with pytest.raises(ValueError, match=">= 0"):
        ps.mcnemar_exact(-1, 2)


def test_pass_at_n_difference_hand_computed():
    a = {"t1": [0, 0], "t2": [1, 0], "t3": [0, 0], "t4": [1], "t5": [0, 1]}
    b = {"t1": [0, 1], "t2": [0, 0], "t3": [0, 0], "t4": [1], "t5": [0, 0]}

    result = ps.pass_at_n_difference(a, b, n_boot=500, seed=0)

    # pass a: 0 1 0 1 1 ; pass b: 1 0 0 1 0
    assert result["n_tasks"] == 5
    assert result["mean_a"] == pytest.approx(3 / 5)
    assert result["mean_b"] == pytest.approx(2 / 5)
    assert result["delta"] == pytest.approx(1 / 5)
    assert (result["wins"], result["losses"], result["ties"]) == (2, 1, 2)
    assert (result["a_only"], result["b_only"]) == (2, 1)
    # n = 3 discordant, one-sided tail (1 + 3) / 8, doubled and capped
    assert result["mcnemar_p"] == 1.0


def test_pass_at_n_counts_an_unscored_task_as_not_passed():
    a = {"t1": [], "t2": [1]}
    b = {"t1": [1], "t2": [1]}

    result = ps.pass_at_n_difference(a, b, n_boot=100)

    assert result["n_tasks"] == 2
    assert result["excluded_unscored"] == []
    assert (result["wins"], result["losses"], result["ties"]) == (0, 1, 1)


def test_pass_at_n_with_fractional_rewards_needs_full_credit():
    a = {"t1": [0.9, 0.9], "t2": [1.0]}
    b = {"t1": [1.0], "t2": [0.0]}

    result = ps.pass_at_n_difference(a, b, n_boot=100)

    # a passes only t2, b passes only t1
    assert (result["a_only"], result["b_only"]) == (1, 1)


def test_pass_at_n_with_no_common_task_raises():
    with pytest.raises(ValueError, match="share no task"):
        ps.pass_at_n_difference({"t1": [1]}, {"t2": [1]})


# ---------------------------------------------------------------------------
# noise_floor
# ---------------------------------------------------------------------------

def test_noise_floor_of_one_two_trial_task_is_plus_minus_one():
    # Halves of size 1: the difference is +1 or -1 with equal chance.
    result = ps.noise_floor({"t1": [1, 0]}, n_resample=2000, seed=0)

    assert result["n_tasks"] == 1
    assert result["sd"] == pytest.approx(1.0, abs=0.02)
    assert (result["ci_low"], result["ci_high"]) == (-1.0, 1.0)
    assert result["trials_per_task"] == {"min": 2, "max": 2}
    # two full-depth arms of 2 trials: the half-split SD scaled by sqrt(1/2)
    assert result["sd_at_full_depth"] == pytest.approx(
        result["sd"] * math.sqrt(0.5))


def test_noise_floor_averages_independent_tasks():
    # Two such tasks: the mean of two independent +-1 draws has SD sqrt(1/2).
    result = ps.noise_floor({"t1": [1, 0], "t2": [0, 1]},
                            n_resample=4000, seed=0)

    assert result["sd"] == pytest.approx(math.sqrt(0.5), abs=0.03)


def test_noise_floor_of_constant_scores_is_zero():
    result = ps.noise_floor({"t1": [1, 1, 1], "t2": [0, 0, 0]},
                            n_resample=200, seed=0)

    assert result["sd"] == 0.0
    assert (result["ci_low"], result["ci_high"]) == (0.0, 0.0)


def test_noise_floor_is_deterministic_and_seed_dependent():
    scores = {f"t{i}": [i % 2, 1, 0, i % 3 == 0] for i in range(10)}
    scores = {task: [float(v) for v in trials]
              for task, trials in scores.items()}

    first = ps.noise_floor(scores, n_resample=300, seed=3)

    assert first == ps.noise_floor(scores, n_resample=300, seed=3)
    assert first["sd"] != ps.noise_floor(scores, n_resample=300, seed=4)["sd"]


def test_noise_floor_leaves_one_trial_out_of_an_odd_depth():
    # Three trials: halves of one trial each, the third is dropped.
    result = ps.noise_floor({"t1": [1, 0, 0]}, n_resample=2000, seed=0)

    assert result["trials_per_task"] == {"min": 3, "max": 3}
    # The only possible differences are 0 (both halves 0) and +-1.
    assert result["ci_low"] == -1.0 and result["ci_high"] == 1.0


def test_noise_floor_lists_tasks_it_cannot_split():
    result = ps.noise_floor({"t1": [1, 0], "t2": [1], "t3": []},
                            n_resample=100)

    assert result["n_tasks"] == 1
    assert result["excluded_tasks"] == ["t2", "t3"]


def test_noise_floor_without_a_splittable_task_raises():
    with pytest.raises(ValueError, match="at least 2 scored trials"):
        ps.noise_floor({"t1": [1], "t2": []})


def test_noise_floor_depth_scaling_is_none_for_mixed_depths():
    result = ps.noise_floor({"t1": [1, 0], "t2": [1, 0, 1, 0]},
                            n_resample=100)

    assert result["trials_per_task"] == {"min": 2, "max": 4}
    assert result["sd_at_full_depth"] is None
