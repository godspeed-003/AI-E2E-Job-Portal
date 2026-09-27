"""Rank-correlation and ranking-quality statistics, implemented here on purpose.

SciPy would give us ``kendalltau`` and ``spearmanr`` in one line. It is not a
dependency of this project and adding a 40 MB scientific stack so that a paper
can quote two coefficients is a bad trade — but that is the smaller reason.

The larger one: every number in the evaluation has to be checkable by whoever
reads the paper. A coefficient that comes out of a black box is exactly as
trustworthy as the sentence "we used SciPy", and the tie-handling convention
(tau-a? tau-b? tau-c?) changes the third decimal place of a result that gets
reported to three decimal places. So the formulas are here, named, with their
conventions stated, and :mod:`tests.test_metrics_stats` checks them against
hand-computed values.

Everything here is pure: lists in, floats out, no I/O, no global state.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


# --------------------------------------------------------------------------- #
# Rank correlation
# --------------------------------------------------------------------------- #


def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall's τ-b — the tie-corrected variant.

    τ-b is the right choice here because scores in this system tie constantly:
    the screening rubric has 26 possible values (0–25) and a role can easily
    have three candidates on 18. τ-a would treat those ties as disagreements
    and understate every correlation; τ-b divides by the geometric mean of the
    tie-adjusted pair counts.

        τ-b = (C − D) / sqrt((n₀ − n₁)(n₀ − n₂))

    where n₀ = n(n−1)/2, n₁ and n₂ are the tied-pair counts in x and y, C is
    concordant pairs and D discordant. Returns 0.0 when either vector is
    constant, because "no variation" is not "no relationship" and any other
    value would be an invention.
    """
    n = len(x)
    if n != len(y):
        raise ValueError("kendall_tau_b needs equal-length sequences")
    if n < 2:
        return 0.0

    concordant = discordant = tied_x = tied_y = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            product = dx * dy
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
            else:
                # At least one side is tied. A pair tied on both sides counts
                # toward both tie totals, which is what τ-b's denominator wants.
                if dx == 0:
                    tied_x += 1
                if dy == 0:
                    tied_y += 1

    n0 = n * (n - 1) / 2
    denominator = math.sqrt((n0 - tied_x) * (n0 - tied_y))
    if denominator == 0:
        return 0.0
    return (concordant - discordant) / denominator


def _ranks_with_ties(values: Sequence[float]) -> list[float]:
    """Average ("fractional") ranks — the convention Spearman's ρ assumes."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        shared = (position + end) / 2 + 1  # 1-based, averaged over the tie block
        for k in range(position, end + 1):
            ranks[order[k]] = shared
        position = end + 1
    return ranks


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman's ρ = Pearson correlation of the fractional ranks."""
    if len(x) != len(y):
        raise ValueError("spearman_rho needs equal-length sequences")
    if len(x) < 2:
        return 0.0
    return pearson_r(_ranks_with_ties(x), _ranks_with_ties(y))


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson product-moment correlation. 0.0 if either side is constant."""
    n = len(x)
    if n != len(y):
        raise ValueError("pearson_r needs equal-length sequences")
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    dx = [v - mean_x for v in x]
    dy = [v - mean_y for v in y]
    numerator = sum(a * b for a, b in zip(dx, dy))
    denominator = math.sqrt(sum(a * a for a in dx) * sum(b * b for b in dy))
    return 0.0 if denominator == 0 else numerator / denominator


# --------------------------------------------------------------------------- #
# Ranking quality against a ground truth
# --------------------------------------------------------------------------- #


def dcg_at_k(relevances: Sequence[float], k: int) -> float:
    """Discounted cumulative gain with the standard log₂(i+1) discount."""
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def ndcg_at_k(ranked_relevances: Sequence[float], k: int) -> float:
    """NDCG@k. Returns 0.0 when nothing in the list is relevant.

    ``ranked_relevances`` is the true relevance of each item *in the order the
    system produced*. The ideal list is the same relevances sorted descending,
    so this is normalised against the best achievable ordering of the same pool
    — not against a hypothetical perfect pool.
    """
    ideal = dcg_at_k(sorted(ranked_relevances, reverse=True), k)
    return 0.0 if ideal == 0 else dcg_at_k(ranked_relevances, k) / ideal


def precision_at_k(hits: Sequence[bool], k: int) -> float:
    """Share of the top k that are relevant. k is clamped to the list length."""
    k = min(k, len(hits))
    return 0.0 if k == 0 else sum(1 for h in hits[:k] if h) / k


def recall_at_k(hits: Sequence[bool], k: int, total_relevant: int) -> float:
    if total_relevant <= 0:
        return 0.0
    return sum(1 for h in hits[:k] if h) / total_relevant


def mrr(hits: Sequence[bool]) -> float:
    """Reciprocal rank of the first relevant item; 0.0 if there is none."""
    for i, hit in enumerate(hits):
        if hit:
            return 1.0 / (i + 1)
    return 0.0


def average_precision(hits: Sequence[bool]) -> float:
    relevant = 0
    total = 0.0
    for i, hit in enumerate(hits):
        if hit:
            relevant += 1
            total += relevant / (i + 1)
    return 0.0 if relevant == 0 else total / relevant


# --------------------------------------------------------------------------- #
# Uncertainty
# --------------------------------------------------------------------------- #


def bootstrap_ci(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
    iterations: int = 10_000,
    seed: int = 20260923,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean.

    Seeded, so the interval in the paper is the interval a reader reproduces.
    A percentile bootstrap is used rather than a t-interval because none of the
    quantities measured here (rank shifts, per-stage latencies, integrity
    deductions) is remotely normal, and small-n normality assumptions are how
    confidence intervals end up narrower than the truth.
    """
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lower = (1.0 - confidence) / 2
    return (
        means[int(lower * iterations)],
        means[min(iterations - 1, int((1 - lower) * iterations))],
    )


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile; ``p`` in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (p / 100) * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(values: Sequence[float]) -> dict[str, float]:
    """The five numbers every latency and score table in the paper reports."""
    if not values:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "min": min(values),
        "max": max(values),
    }


# --------------------------------------------------------------------------- #
# Classification, for the guardrail measurements
# --------------------------------------------------------------------------- #


def confusion(
    predicted: Sequence[bool], actual: Sequence[bool]
) -> dict[str, float]:
    """TP/FP/FN/TN plus precision, recall, F1 and specificity.

    ``predicted``/``actual`` are "is this hostile", so a false positive is a
    benign resume the guardrail stripped — the error that matters most here,
    because it silently edits an honest candidate's document.
    """
    if len(predicted) != len(actual):
        raise ValueError("confusion needs equal-length sequences")
    tp = sum(1 for p, a in zip(predicted, actual) if p and a)
    fp = sum(1 for p, a in zip(predicted, actual) if p and not a)
    fn = sum(1 for p, a in zip(predicted, actual) if not p and a)
    tn = sum(1 for p, a in zip(predicted, actual) if not p and not a)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": tn / (tn + fp) if (tn + fp) else 0.0,
        "accuracy": (tp + tn) / len(actual) if actual else 0.0,
    }
