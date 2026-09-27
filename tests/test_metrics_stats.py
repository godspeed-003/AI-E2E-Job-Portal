"""Hand-computed checks for :mod:`metrics.stats`.

Every coefficient the paper reports — Kendall's τ-b, Spearman's ρ, NDCG@10,
MRR, the bootstrap intervals — comes out of ``metrics/stats.py``, which is a
from-scratch implementation rather than a SciPy call. That was a deliberate
choice (see the module docstring there), and it carries a deliberate cost: the
formulas are ours, so the arithmetic is ours to prove.

So these tests do not check ``kendall_tau_b`` against another library. They
check it against numbers worked out by hand and written into the assertion with
the working shown in the comment, because "it agrees with SciPy" would only move
the question of correctness somewhere the reader of the paper cannot see.

Three things get particular attention:

* **Tie handling.** The rubric has 26 possible values and a cohort of 100, so
  ties are the normal case, not the edge case. τ-b's tie correction and
  Spearman's fractional ranks are each tested on a vector built to tie.
* **Degenerate input.** A constant vector returns 0.0 rather than raising or
  returning NaN, and a paper full of NaNs is how a harness fails quietly.
* **Bootstrap determinism.** The intervals are seeded so a reader reproduces the
  interval in the paper exactly. A test asserts that, because an unseeded
  bootstrap would still look completely fine in a single run.
"""

from __future__ import annotations

import math

import pytest

from metrics import stats


# --------------------------------------------------------------------------- #
# Kendall's tau-b
# --------------------------------------------------------------------------- #


def test_kendall_tau_b_is_one_for_identical_orderings():
    assert stats.kendall_tau_b([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0


def test_kendall_tau_b_is_minus_one_for_reversed_orderings():
    assert stats.kendall_tau_b([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0


def test_kendall_tau_b_matches_a_hand_counted_example():
    # x = 1,2,3,4,5  y = 2,1,4,3,5 — 10 pairs, of which exactly two are
    # discordant: (x0,x1) and (x2,x3), where y runs backwards. tau = (8-2)/10.
    assert stats.kendall_tau_b([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.6)


def test_kendall_tau_b_corrects_for_ties_rather_than_counting_them_against():
    # x = 1,2,3,4 against y = 1,1,2,2. Of the six pairs, four are concordant and
    # two are tied in y. tau-b = 4 / sqrt((6-0)(6-2)) = 4/sqrt(24) = 2/sqrt(6).
    tau_b = stats.kendall_tau_b([1, 2, 3, 4], [1, 1, 2, 2])
    assert tau_b == pytest.approx(2 / math.sqrt(6))

    # This is the whole reason tau-b is the variant in use: tau-a would put the
    # same four concordant pairs over the raw pair count and report 0.667,
    # charging the correlation for information y simply does not carry.
    tau_a = 4 / 6
    assert tau_b > tau_a


def test_kendall_tau_b_counts_a_doubly_tied_pair_on_both_sides():
    # Every pair is tied in both vectors, so both tie totals reach n0 and the
    # denominator collapses. The answer must be 0.0, not a ZeroDivisionError.
    assert stats.kendall_tau_b([1, 1, 1], [5, 5, 5]) == 0.0


def test_kendall_tau_b_returns_zero_for_a_constant_vector():
    # "No variation" is not "no relationship", but it is not evidence of one
    # either, and any non-zero answer here would be an invention.
    assert stats.kendall_tau_b([7, 7, 7, 7], [1, 2, 3, 4]) == 0.0


def test_kendall_tau_b_is_zero_on_a_single_observation():
    assert stats.kendall_tau_b([1], [1]) == 0.0


def test_kendall_tau_b_refuses_mismatched_lengths():
    with pytest.raises(ValueError):
        stats.kendall_tau_b([1, 2, 3], [1, 2])


def test_kendall_tau_b_is_symmetric_in_its_arguments():
    x = [3, 1, 4, 1, 5, 9, 2, 6]
    y = [2, 7, 1, 8, 2, 8, 1, 8]
    assert stats.kendall_tau_b(x, y) == pytest.approx(stats.kendall_tau_b(y, x))


# --------------------------------------------------------------------------- #
# Fractional ranks and Spearman's rho
# --------------------------------------------------------------------------- #


def test_ranks_with_ties_averages_across_a_tie_block():
    # 20 and 20 occupy positions 2 and 3, so both take rank 2.5 and 30 still
    # takes rank 4 — the convention Spearman assumes.
    assert stats._ranks_with_ties([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_ranks_with_ties_gives_every_member_of_a_total_tie_the_mean_rank():
    assert stats._ranks_with_ties([5, 5, 5]) == [2.0, 2.0, 2.0]


def test_ranks_with_ties_does_not_depend_on_input_order():
    assert stats._ranks_with_ties([30, 10, 20, 20]) == [4.0, 1.0, 2.5, 2.5]


def test_spearman_rho_is_minus_one_for_a_reversal():
    assert stats.spearman_rho([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_rho_matches_a_hand_computed_tied_example():
    # Ranks: x -> 1,2,3,4 and y -> 1.5,1.5,3.5,3.5.
    # Pearson of those: 4.0 / sqrt(5 * 4) = 4/sqrt(20) = 2/sqrt(5).
    assert stats.spearman_rho([1, 2, 3, 4], [1, 1, 2, 2]) == pytest.approx(
        2 / math.sqrt(5)
    )


def test_spearman_rho_sees_a_monotone_curve_that_pearson_understates():
    # The relationship is perfectly monotone but strongly convex. rho reports
    # 1.0 because it only reads order; r reports less because it reads spacing.
    # Relevant here: every score in this system is ordinal, which is why the
    # ranking claims are made with rho and tau rather than with r.
    x = [1, 2, 3, 4, 5]
    y = [1, 4, 9, 16, 25]
    assert stats.spearman_rho(x, y) == pytest.approx(1.0)
    assert stats.pearson_r(x, y) < 1.0


def test_spearman_rho_refuses_mismatched_lengths():
    with pytest.raises(ValueError):
        stats.spearman_rho([1, 2, 3], [1, 2])


# --------------------------------------------------------------------------- #
# Pearson's r
# --------------------------------------------------------------------------- #


def test_pearson_r_is_one_on_an_exact_linear_relationship():
    assert stats.pearson_r([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)


def test_pearson_r_matches_a_hand_computed_example():
    # dx = -2,-1,0,1,2 and dy = -2,0,1,0,1.
    # numerator 6; denominator sqrt(10 * 6) = sqrt(60); r = 6/sqrt(60).
    assert stats.pearson_r([1, 2, 3, 4, 5], [2, 4, 5, 4, 5]) == pytest.approx(
        6 / math.sqrt(60)
    )


def test_pearson_r_is_unaffected_by_a_linear_rescaling():
    x = [3, 1, 4, 1, 5, 9]
    y = [2, 7, 1, 8, 2, 8]
    rescaled = [100 * v + 7 for v in y]
    assert stats.pearson_r(x, rescaled) == pytest.approx(stats.pearson_r(x, y))


def test_pearson_r_returns_zero_rather_than_dividing_by_zero():
    assert stats.pearson_r([1, 1, 1], [1, 2, 3]) == 0.0


def test_pearson_r_refuses_mismatched_lengths():
    with pytest.raises(ValueError):
        stats.pearson_r([1, 2, 3], [1, 2])


# --------------------------------------------------------------------------- #
# Ranking quality
# --------------------------------------------------------------------------- #


def test_dcg_at_k_discounts_by_log2_of_position_plus_one():
    # A single relevant item in first place takes the log2(2) = 1 discount, so
    # its gain is undiscounted.
    assert stats.dcg_at_k([1, 0, 0], 3) == pytest.approx(1.0)
    # The same item in third place takes log2(4) = 2.
    assert stats.dcg_at_k([0, 0, 1], 3) == pytest.approx(0.5)


def test_dcg_at_k_matches_the_textbook_graded_example():
    # The standard worked example: relevances 3,2,3,0,1,2.
    # 3/1 + 2/log2(3) + 3/2 + 0 + 1/log2(6) + 2/log2(7)
    assert stats.dcg_at_k([3, 2, 3, 0, 1, 2], 6) == pytest.approx(6.861127, abs=1e-6)


def test_dcg_at_k_truncates_at_k():
    assert stats.dcg_at_k([3, 2, 3, 0, 1, 2], 2) == pytest.approx(
        3 + 2 / math.log2(3)
    )


def test_ndcg_at_k_is_one_when_the_system_produced_the_ideal_order():
    assert stats.ndcg_at_k([3, 2, 1, 0], 4) == pytest.approx(1.0)


def test_ndcg_at_k_normalises_against_the_best_ordering_of_the_same_pool():
    # One relevant item, placed third. Ideal DCG is 1.0, actual is 0.5.
    assert stats.ndcg_at_k([0, 0, 1], 3) == pytest.approx(0.5)


def test_ndcg_at_k_is_zero_when_nothing_in_the_list_is_relevant():
    # No relevant items means no ideal ranking exists, so the normaliser is
    # undefined. 0.0 is the reported value, never a NaN.
    assert stats.ndcg_at_k([0, 0, 0], 3) == 0.0


def test_precision_at_k_counts_only_the_top_k():
    assert stats.precision_at_k([True, False, True, False], 2) == pytest.approx(0.5)
    assert stats.precision_at_k([True, True, False, False], 2) == pytest.approx(1.0)


def test_precision_at_k_clamps_k_to_the_list_length():
    # Asking for P@10 of a 4-item list must not divide by 10 and report 0.2 for
    # a list that is half relevant.
    assert stats.precision_at_k([True, False, True, False], 10) == pytest.approx(0.5)


def test_precision_at_k_is_zero_on_an_empty_list():
    assert stats.precision_at_k([], 10) == 0.0


def test_recall_at_k_divides_by_the_pool_not_by_k():
    # Two relevant items visible in the top 2, but four exist in the pool.
    assert stats.recall_at_k([True, True, False], 2, total_relevant=4) == pytest.approx(
        0.5
    )


def test_recall_at_k_is_zero_when_nothing_is_relevant():
    assert stats.recall_at_k([False, False], 2, total_relevant=0) == 0.0


def test_mrr_is_the_reciprocal_of_the_first_hit():
    assert stats.mrr([True, False, False]) == pytest.approx(1.0)
    assert stats.mrr([False, False, True]) == pytest.approx(1 / 3)


def test_mrr_is_zero_when_no_relevant_item_was_retrieved():
    assert stats.mrr([False, False, False]) == 0.0


def test_average_precision_averages_the_precision_at_each_hit():
    # Hits at positions 1 and 3: (1/1 + 2/3) / 2.
    assert stats.average_precision([True, False, True]) == pytest.approx(
        (1.0 + 2 / 3) / 2
    )


def test_average_precision_is_zero_with_no_hits():
    assert stats.average_precision([False, False]) == 0.0


# --------------------------------------------------------------------------- #
# Bootstrap intervals
# --------------------------------------------------------------------------- #

_SAMPLE = [2.0, 3.0, 5.0, 7.0, 11.0, 13.0, 17.0, 19.0, 23.0, 29.0]


def test_bootstrap_ci_is_reproducible_across_calls():
    # The interval printed in the paper must be the interval a reader gets.
    # Without the fixed seed this assertion is the only thing that would fail;
    # every individual run would look entirely reasonable.
    first = stats.bootstrap_ci(_SAMPLE, iterations=2000)
    second = stats.bootstrap_ci(_SAMPLE, iterations=2000)
    assert first == second


def test_bootstrap_ci_actually_depends_on_the_seed():
    # The converse of the test above: confirm the reproducibility comes from
    # seeding rather than from the resampling having no effect at all.
    default = stats.bootstrap_ci(_SAMPLE, iterations=2000)
    other = stats.bootstrap_ci(_SAMPLE, iterations=2000, seed=1)
    assert default != other


def test_bootstrap_ci_brackets_the_sample_mean():
    low, high = stats.bootstrap_ci(_SAMPLE, iterations=2000)
    mean = sum(_SAMPLE) / len(_SAMPLE)
    assert low < mean < high


def test_bootstrap_ci_stays_inside_the_observed_range():
    # A resampled mean is an average of observed values, so it can never leave
    # the range of the sample. An interval that did would mean the resampling
    # has gone wrong.
    low, high = stats.bootstrap_ci(_SAMPLE, iterations=2000)
    assert min(_SAMPLE) <= low
    assert high <= max(_SAMPLE)


def test_a_higher_confidence_level_gives_a_wider_interval():
    narrow = stats.bootstrap_ci(_SAMPLE, confidence=0.80, iterations=4000)
    wide = stats.bootstrap_ci(_SAMPLE, confidence=0.99, iterations=4000)
    assert wide[0] <= narrow[0]
    assert narrow[1] <= wide[1]


def test_bootstrap_ci_of_a_constant_sample_has_zero_width():
    assert stats.bootstrap_ci([4.0] * 8, iterations=500) == (4.0, 4.0)


def test_bootstrap_ci_of_a_single_observation_is_that_observation():
    # One measurement carries no information about its own variability. The
    # degenerate interval says so honestly; a normal-theory interval would
    # return NaN or, worse, zero width with a confident-looking centre.
    assert stats.bootstrap_ci([42.0]) == (42.0, 42.0)


def test_bootstrap_ci_of_nothing_is_zero_width_at_zero():
    assert stats.bootstrap_ci([]) == (0.0, 0.0)


# --------------------------------------------------------------------------- #
# Percentiles and summaries
# --------------------------------------------------------------------------- #


def test_percentile_returns_exact_order_statistics_at_the_ends():
    assert stats.percentile([1, 2, 3, 4, 5], 0) == 1.0
    assert stats.percentile([1, 2, 3, 4, 5], 100) == 5.0


def test_percentile_hits_the_element_exactly_when_the_position_is_integral():
    assert stats.percentile([1, 2, 3, 4, 5], 50) == 3.0


def test_percentile_interpolates_between_neighbours():
    # p50 of four values sits at position 1.5, halfway between 2 and 3.
    assert stats.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)


def test_percentile_sorts_its_input():
    # Latency samples arrive in call order, not in value order, and forgetting
    # to sort is the classic way to publish a p95 that is not one.
    assert stats.percentile([5, 1, 3], 50) == 3.0


def test_percentile_of_an_empty_sample_is_zero():
    assert stats.percentile([], 95) == 0.0


def test_summarize_reports_the_five_numbers_the_paper_tables_use():
    summary = stats.summarize([1, 2, 3, 4, 5])
    assert summary["n"] == 5
    assert summary["mean"] == pytest.approx(3.0)
    assert summary["p50"] == pytest.approx(3.0)
    assert summary["min"] == 1
    assert summary["max"] == 5


def test_summarize_of_an_empty_sample_reports_n_zero_rather_than_raising():
    assert stats.summarize([])["n"] == 0


# --------------------------------------------------------------------------- #
# Confusion matrix
# --------------------------------------------------------------------------- #


def test_confusion_counts_a_hand_built_two_by_two():
    predicted = [True, True, False, False]
    actual = [True, False, True, False]
    matrix = stats.confusion(predicted, actual)
    assert (matrix["tp"], matrix["fp"], matrix["fn"], matrix["tn"]) == (1, 1, 1, 1)
    assert matrix["precision"] == pytest.approx(0.5)
    assert matrix["recall"] == pytest.approx(0.5)
    assert matrix["f1"] == pytest.approx(0.5)
    assert matrix["specificity"] == pytest.approx(0.5)
    assert matrix["accuracy"] == pytest.approx(0.5)


def test_confusion_reports_a_perfect_classifier_as_perfect():
    matrix = stats.confusion([True, False, True], [True, False, True])
    assert matrix["precision"] == 1.0
    assert matrix["recall"] == 1.0
    assert matrix["f1"] == 1.0
    assert matrix["fp"] == 0


def test_confusion_separates_precision_from_recall_on_the_guardrail_failure_mode():
    # The case the guardrail section cares about: the detector fires on
    # everything. Recall is perfect and precision is not, and a single
    # "accuracy" number would hide that three honest resumes were edited.
    predicted = [True] * 4
    actual = [True, False, False, False]
    matrix = stats.confusion(predicted, actual)
    assert matrix["recall"] == 1.0
    assert matrix["precision"] == pytest.approx(0.25)
    assert matrix["fp"] == 3
    assert matrix["specificity"] == 0.0


def test_confusion_does_not_divide_by_zero_when_nothing_was_predicted_positive():
    matrix = stats.confusion([False, False], [True, False])
    assert matrix["precision"] == 0.0
    assert matrix["f1"] == 0.0


def test_confusion_refuses_mismatched_lengths():
    with pytest.raises(ValueError):
        stats.confusion([True, False], [True])
