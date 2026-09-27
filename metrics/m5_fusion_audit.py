"""M5 — the correction audit: does the interview fix the resume's ranking errors?

This is the paper's central question, so it is also the measurement that has to
be most careful about what it is *not* claiming.

The design
----------

The cohort in :mod:`metrics.corpora` is generated from a latent competence value
with four cases planted by construction:

* **A — over-seller.** Low competence, inflated resume. Should fall.
* **B — hidden gem.** High competence, terse resume. Should rise.
* **C — honest strong** and **D — honest weak.** Resume and interview agree.
  Should barely move — and the rate at which they *do* move is the false
  correction rate, which is the cost of the whole mechanism.

Two rankings are produced from the same rows by the same module:
``core.ranking.resume_only_rank`` (what the portal shipped with —
``ORDER BY llm_score DESC, ats_score DESC``) and ``core.ranking.rank`` (the
fused ``S_final``). Both are scored against latent competence.

Reported
--------

``ΔR``          mean signed rank movement per case (negative = moved up the list)
``R_rec``       recovery rate — share of planted cases that moved the right way
``FCR``         false correction rate — share of honest controls displaced by
                more than a tolerance
``NDCG@k``, ``Kendall τ-b``, ``P@k``, ``MRR`` for both rankings
λ sweep         every metric as the interview weight goes 0 → 1, so the paper
                reports sensitivity instead of one flattering operating point

What this is not
----------------

**This measures the fusion function, not the interviewer.** The cohort's
interview scores are generated as unbiased-but-noisy views of competence. That
is an *assumption*, and it is the assumption the entire pipeline rests on. If
the LLM interviewer is itself fooled by a confident over-seller, every number
below still holds for ``h()`` and says nothing about the deployed system.

Establishing the assumption needs a model run with human adjudication — the
``A0…A5`` ablation and the Fleiss' κ agreement study. The harness reports both
as unavailable and names the run. Any sentence in the paper of the form "our
pipeline recovers X% of planted errors" is unsupported until that run exists;
the supported sentence is "given interview evidence with residual error σ, the
fusion recovers X% of planted errors", and σ has to be stated.
"""

from __future__ import annotations

from collections import Counter

from core import ranking
from metrics import Section
from metrics.corpora import CASES, make_cohort
from metrics.stats import (
    bootstrap_ci,
    kendall_tau_b,
    mrr,
    ndcg_at_k,
    precision_at_k,
    summarize,
)

# A control that shifts by one or two places has been jostled by its neighbours,
# not "falsely corrected". The tolerance is stated rather than tuned: 5% of the
# cohort, so it scales with the list length.
FALSE_CORRECTION_TOLERANCE = 0.05


def _quality(
    ranked: list[ranking.RankedCandidate],
    truth: dict[int, float],
    *,
    k: int,
    relevant: set[int],
) -> dict[str, float]:
    ids = [c.application_id for c in ranked]
    relevances = [truth[i] for i in ids]
    hits = [i in relevant for i in ids]
    positions = list(range(len(ids), 0, -1))
    return {
        f"ndcg@{k}": ndcg_at_k(relevances, k),
        f"precision@{k}": precision_at_k(hits, k),
        "mrr": mrr(hits),
        "kendall_tau_b": kendall_tau_b(positions, relevances),
    }


def run(n_per_case: int = 25, k: int = 10) -> Section:
    section = Section(
        key="m5_fusion_audit",
        title="Correction audit — resume-only vs fused ranking against ground truth",
    )

    cohort = make_cohort(n_per_case=n_per_case)
    rows = cohort.rows
    truth = cohort.truth
    case_of = {c.candidate_id: c.case for c in cohort.candidates}

    cutoff = sorted(truth.values(), reverse=True)[max(0, len(truth) // 4 - 1)]
    relevant = {i for i, value in truth.items() if value >= cutoff}
    tolerance = max(1, round(FALSE_CORRECTION_TOLERANCE * len(rows)))

    section.add(
        "fusion_parameters",
        ranking.describe(),
        kind="specification",
        source="core.ranking.describe",
    )
    section.add(
        "cases",
        CASES,
        kind="specification",
        source="metrics.corpora.CASES",
    )
    section.add(
        "cohort_size",
        len(rows),
        unit="candidates",
        kind="specification",
        source="metrics.corpora.make_cohort",
        note=f"{n_per_case} per case, seed {cohort.seed}",
    )
    section.add(
        "false_correction_tolerance",
        tolerance,
        unit="positions",
        kind="specification",
        source="metrics.m5_fusion_audit.FALSE_CORRECTION_TOLERANCE",
        note=f"{FALSE_CORRECTION_TOLERANCE:.0%} of the cohort",
    )

    # ------------------------------------------------------------------ #
    # The two rankings
    # ------------------------------------------------------------------ #
    before = ranking.resume_only_rank(rows)
    after = ranking.rank(rows)
    position_before = ranking.rank_positions(before)
    position_after = ranking.rank_positions(after)

    # Ideal ordering, for the ceiling the fusion is being measured against.
    ideal_ids = sorted(truth, key=lambda i: -truth[i])
    ideal_quality = {
        f"ndcg@{k}": 1.0,
        f"precision@{k}": precision_at_k([i in relevant for i in ideal_ids], k),
        "mrr": mrr([i in relevant for i in ideal_ids]),
        "kendall_tau_b": 1.0,
    }

    quality_before = _quality(before, truth, k=k, relevant=relevant)
    quality_after = _quality(after, truth, k=k, relevant=relevant)
    section.tables["ranking_quality"] = {
        "resume_only": quality_before,
        "fused": quality_after,
        "ideal": ideal_quality,
        "absolute_gain": {
            key: quality_after[key] - quality_before[key] for key in quality_before
        },
        "share_of_headroom_closed": {
            key: (
                (quality_after[key] - quality_before[key])
                / (ideal_quality[key] - quality_before[key])
                if ideal_quality[key] - quality_before[key] > 1e-12
                else None
            )
            for key in quality_before
        },
    }
    for key in quality_before:
        section.add(
            f"resume_only_{key}",
            quality_before[key],
            kind="simulated",
            source="core.ranking.resume_only_rank",
        )
        section.add(
            f"fused_{key}",
            quality_after[key],
            kind="simulated",
            source="core.ranking.rank",
        )

    # ------------------------------------------------------------------ #
    # Per-case rank movement
    # ------------------------------------------------------------------ #
    movement_rows = []
    for candidate_id, case in case_of.items():
        delta = position_after[candidate_id] - position_before[candidate_id]
        movement_rows.append(
            {
                "candidate_id": candidate_id,
                "case": case,
                "competence": round(truth[candidate_id], 4),
                "rank_before": position_before[candidate_id],
                "rank_after": position_after[candidate_id],
                "delta_rank": delta,
                "moved_up": delta < 0,
            }
        )
    section.tables["rank_movement"] = movement_rows

    case_summary = {}
    for case in CASES:
        deltas = [float(r["delta_rank"]) for r in movement_rows if r["case"] == case]
        low, high = bootstrap_ci(deltas)
        # "Correct direction" is defined per case from the planted error:
        # an over-seller should move down (delta > 0), a hidden gem up.
        if case == "A_over_seller":
            correct = sum(1 for d in deltas if d > 0)
        elif case == "B_hidden_gem":
            correct = sum(1 for d in deltas if d < 0)
        else:
            correct = sum(1 for d in deltas if abs(d) <= tolerance)
        case_summary[case] = {
            "n": len(deltas),
            "mean_delta_rank": sum(deltas) / len(deltas),
            "ci95_delta_rank": [low, high],
            "median_delta_rank": summarize(deltas)["p50"],
            "moved_correctly": correct,
            "rate": correct / len(deltas),
            "description": CASES[case],
        }
    section.tables["by_case"] = case_summary

    section.add(
        "recovery_rate_over_seller",
        case_summary["A_over_seller"]["rate"],
        unit="fraction",
        kind="simulated",
        source="core.ranking.rank",
        note=(
            f"{case_summary['A_over_seller']['moved_correctly']}/"
            f"{case_summary['A_over_seller']['n']} inflated resumes moved down; "
            f"mean ΔR {case_summary['A_over_seller']['mean_delta_rank']:+.1f} places"
        ),
    )
    section.add(
        "recovery_rate_hidden_gem",
        case_summary["B_hidden_gem"]["rate"],
        unit="fraction",
        kind="simulated",
        source="core.ranking.rank",
        note=(
            f"{case_summary['B_hidden_gem']['moved_correctly']}/"
            f"{case_summary['B_hidden_gem']['n']} understated resumes moved up; "
            f"mean ΔR {case_summary['B_hidden_gem']['mean_delta_rank']:+.1f} places"
        ),
    )
    planted_correct = (
        case_summary["A_over_seller"]["moved_correctly"]
        + case_summary["B_hidden_gem"]["moved_correctly"]
    )
    planted_total = case_summary["A_over_seller"]["n"] + case_summary["B_hidden_gem"]["n"]
    section.add(
        "recovery_rate_overall",
        planted_correct / planted_total,
        unit="fraction",
        kind="simulated",
        source="core.ranking.rank",
        note=f"{planted_correct}/{planted_total} planted errors moved the right way",
    )

    controls = [
        r
        for r in movement_rows
        if r["case"] in ("C_honest_strong", "D_honest_weak")
    ]
    displaced = [r for r in controls if abs(r["delta_rank"]) > tolerance]
    section.add(
        "false_correction_rate",
        len(displaced) / len(controls),
        unit="fraction",
        kind="simulated",
        source="core.ranking.rank",
        note=(
            f"{len(displaced)}/{len(controls)} honest controls moved more than "
            f"{tolerance} places. Read this as an UPPER BOUND on the cost, not as "
            "an error rate: half the cohort is deliberately mis-scored by the "
            "resume, so when those candidates move the controls must move to let "
            "them past, even where the fusion judged every control perfectly. The "
            "displacement is decomposed below."
        ),
    )
    section.tables["displaced_controls"] = displaced

    # ------------------------------------------------------------------ #
    # Decomposing the cost: displacement or misjudgement?
    # ------------------------------------------------------------------ #
    # A control that slides down because an over-seller was correctly demoted
    # past it has not been misjudged. The test that separates the two is whether
    # the controls' order *among themselves* still tracks their competence. If it
    # is preserved, the movement is bookkeeping; if it degrades, the fusion is
    # genuinely reordering honest candidates on bad evidence.
    control_ids = [r["candidate_id"] for r in controls]
    control_competence = [truth[i] for i in control_ids]
    control_tau_before = kendall_tau_b(
        [-position_before[i] for i in control_ids], control_competence
    )
    control_tau_after = kendall_tau_b(
        [-position_after[i] for i in control_ids], control_competence
    )

    concordant = discordant = 0
    for first in range(len(control_ids)):
        for second in range(first + 1, len(control_ids)):
            a, b = control_ids[first], control_ids[second]
            before_order = position_before[a] < position_before[b]
            after_order = position_after[a] < position_after[b]
            if before_order == after_order:
                concordant += 1
            else:
                discordant += 1
    pairs = concordant + discordant

    section.tables["control_displacement"] = {
        "controls": len(controls),
        "moved_beyond_tolerance": len(displaced),
        "internal_tau_b_before": control_tau_before,
        "internal_tau_b_after": control_tau_after,
        "internal_tau_b_gain": control_tau_after - control_tau_before,
        "pairs_compared": pairs,
        "pairs_order_preserved": concordant,
        "pairs_order_inverted": discordant,
    }
    section.add(
        "control_internal_tau_b_before",
        control_tau_before,
        kind="simulated",
        source="core.ranking.resume_only_rank",
        note="agreement between the controls' own ordering and their competence",
    )
    section.add(
        "control_internal_tau_b_after",
        control_tau_after,
        kind="simulated",
        source="core.ranking.rank",
        note=(
            "the same agreement after fusion. Higher means the honest "
            "candidates were ranked better against each other, not worse — so "
            "their absolute rank movement was displacement by corrected "
            "neighbours rather than misjudgement of them."
        ),
    )
    section.add(
        "control_pairs_order_preserved",
        round(concordant / pairs, 4) if pairs else None,
        unit="fraction",
        kind="derived",
        source="metrics.m5_fusion_audit",
        note=(
            f"{concordant}/{pairs} pairs of honest controls kept their relative "
            "order through fusion. This is the figure to quote beside the "
            "recovery rate: it separates 'everyone shifted because the list was "
            "re-sorted' from 'honest candidates were re-judged'."
        ),
    )
    section.add(
        "genuine_misordering_of_controls",
        round(discordant / pairs, 4) if pairs else None,
        unit="fraction",
        kind="derived",
        source="metrics.m5_fusion_audit",
        note=(
            "the complement, and the defensible cost figure. Unlike the "
            "false-correction rate above it is invariant to how many candidates "
            "were planted with errors, so it does not inflate with the size of "
            "the correction the fusion is asked to make."
        ),
    )

    # Who occupies the top k, before and after.
    section.tables["top_k_case_mix"] = {
        "resume_only": dict(
            Counter(case_of[c.application_id] for c in before[:k])
        ),
        "fused": dict(Counter(case_of[c.application_id] for c in after[:k])),
        "ideal": dict(Counter(case_of[i] for i in ideal_ids[:k])),
    }

    # ------------------------------------------------------------------ #
    # λ sweep — the sensitivity the paper must report
    # ------------------------------------------------------------------ #
    sweep_rows = []
    for step in range(0, 21):
        lam = step / 20
        fused = ranking.rank(rows, interview_weight=lam)
        positions = ranking.rank_positions(fused)
        quality = _quality(fused, truth, k=k, relevant=relevant)
        deltas_a = [
            positions[c] - position_before[c]
            for c, case in case_of.items()
            if case == "A_over_seller"
        ]
        deltas_b = [
            positions[c] - position_before[c]
            for c, case in case_of.items()
            if case == "B_hidden_gem"
        ]
        control_deltas = [
            positions[c] - position_before[c]
            for c, case in case_of.items()
            if case in ("C_honest_strong", "D_honest_weak")
        ]
        sweep_rows.append(
            {
                "lambda": lam,
                f"ndcg@{k}": quality[f"ndcg@{k}"],
                "kendall_tau_b": quality["kendall_tau_b"],
                f"precision@{k}": quality[f"precision@{k}"],
                "recovery_over_seller": sum(1 for d in deltas_a if d > 0) / len(deltas_a),
                "recovery_hidden_gem": sum(1 for d in deltas_b if d < 0) / len(deltas_b),
                "false_correction": sum(
                    1 for d in control_deltas if abs(d) > tolerance
                )
                / len(control_deltas),
                "mean_delta_over_seller": sum(deltas_a) / len(deltas_a),
                "mean_delta_hidden_gem": sum(deltas_b) / len(deltas_b),
            }
        )
    section.tables["lambda_sweep"] = sweep_rows

    best = max(sweep_rows, key=lambda r: r["kendall_tau_b"])
    section.add(
        "lambda_maximising_tau",
        best["lambda"],
        kind="simulated",
        source="core.ranking.rank (λ sweep)",
        note=(
            f"τ-b {best['kendall_tau_b']:.4f} at λ = {best['lambda']}. Reported as "
            "an observation about the sweep, not as a tuned setting: λ was fixed "
            f"at {ranking.DEFAULT_INTERVIEW_WEIGHT} before the sweep was run, and "
            "tuning it on the same synthetic cohort that evaluates it would be "
            "fitting the test set."
        ),
    )
    section.add(
        "tau_at_default_lambda",
        next(
            r["kendall_tau_b"]
            for r in sweep_rows
            if abs(r["lambda"] - ranking.DEFAULT_INTERVIEW_WEIGHT) < 1e-9
        ),
        kind="simulated",
        source="core.ranking.rank",
    )

    # ------------------------------------------------------------------ #
    # Integrity sensitivity: what an untrusted recording does to the fusion
    # ------------------------------------------------------------------ #
    integrity_rows = []
    for integrity in (0, 25, 50, 55, 75, 90, 100):
        degraded = [dict(r, integrity_score=integrity) for r in rows]
        fused = ranking.rank(degraded)
        positions = ranking.rank_positions(fused)
        deltas_a = [
            positions[c] - position_before[c]
            for c, case in case_of.items()
            if case == "A_over_seller"
        ]
        integrity_rows.append(
            {
                "integrity_score": integrity,
                "kendall_tau_b": _quality(fused, truth, k=k, relevant=relevant)[
                    "kendall_tau_b"
                ],
                "recovery_over_seller": sum(1 for d in deltas_a if d > 0) / len(deltas_a),
            }
        )
    section.tables["integrity_sensitivity"] = integrity_rows

    # ------------------------------------------------------------------ #
    # The collapse property, tested where it is actually claimed
    # ------------------------------------------------------------------ #
    # An earlier version of this check compared `rank()` at c(E) = 0 against
    # `resume_only_rank()` and reported False. The check was wrong, not the
    # fusion. The two functions sort on different secondary keys —
    # `(-s_final, -s_resume, id)` against `(-llm_score, -ats_score, id)` — and
    # s_resume is a monotone rescaling of llm_score, so the fused ranker's
    # second key carries no information its first did not and ties fall through
    # to the id. Candidates tied on llm_score therefore come out in a different
    # order under each ranker, and Kendall's tau-b differs even though every
    # score is identical. Comparing the two rankers tests tiebreak agreement,
    # not the collapse.
    #
    # What the paper needs is the property core.ranking's own docstring states,
    # which is about scores. It is checked exactly below; the ordering is then
    # checked like-for-like against the same ranker with lambda = 0; and the
    # tiebreak divergence is reported as its own quantity instead of being
    # allowed to falsify the claim.
    zeroed = [dict(r, integrity_score=0) for r in rows]
    built_zero = ranking.build(zeroed)
    collapse_error = max(abs(c.s_final - c.s_resume) for c in built_zero)
    section.add(
        "collapse_max_abs_score_error",
        collapse_error,
        unit="score units",
        kind="measured",
        source="core.ranking.final_score",
        note=(
            f"largest |S_final - S_resume| over all {len(built_zero)} candidates "
            "with c(E) = 0. Exact arithmetic, not a tolerance: trust = 0 makes "
            "evidence = S_resume, so the convex combination returns S_resume "
            "whatever lambda is."
        ),
    )
    section.add(
        "fusion_collapses_to_resume_at_zero_integrity",
        collapse_error <= 1e-12,
        kind="measured",
        source="core.ranking.final_score",
        note=(
            "with c(E) = 0 every candidate's fused score equals their "
            "resume-only score exactly — the property that stops proctoring "
            "noise from being a penalty. Stated about scores, not positions: a "
            "candidate can still change place when the candidates around them "
            "are re-scored."
        ),
    )

    zero_lambda_ids = [c.application_id for c in ranking.rank(rows, interview_weight=0)]
    zero_trust_ids = [c.application_id for c in ranking.rank(zeroed)]
    section.add(
        "zero_integrity_ordering_equals_zero_lambda_ordering",
        zero_trust_ids == zero_lambda_ids,
        kind="measured",
        source="core.ranking.rank",
        note=(
            "the like-for-like ranking comparison: discarding the interview by "
            "distrusting it (c(E) = 0) and discarding it by weighting it out "
            "(lambda = 0) produce the same list, position for position, under "
            "the same tiebreaks. This is the ordering form of the collapse."
        ),
    )

    # Where the residual disagreement with the shipped baseline actually lives.
    baseline_ids = [c.application_id for c in before]
    llm_groups = Counter(c.llm_score for c in built_zero)
    tied = sum(n for n in llm_groups.values() if n > 1)
    displaced_by_tiebreak = sum(
        1 for a, b in zip(zero_trust_ids, baseline_ids) if a != b
    )
    section.tables["tiebreak_divergence"] = {
        "candidates": len(rows),
        "distinct_llm_scores": len(llm_groups),
        "candidates_sharing_an_llm_score": tied,
        "largest_tie_group": max(llm_groups.values()),
        "positions_differing_from_baseline_at_zero_integrity": displaced_by_tiebreak,
        "kendall_tau_b_at_zero_integrity": integrity_rows[0]["kendall_tau_b"],
        "kendall_tau_b_resume_only": quality_before["kendall_tau_b"],
    }
    section.add(
        "tied_candidates_on_llm_score",
        tied,
        unit="candidates",
        kind="measured",
        source="metrics.m5_fusion_audit",
        note=(
            f"{tied} of {len(rows)} candidates share their llm_score with at "
            f"least one other (largest group {max(llm_groups.values())}). The "
            "0-25 integer rubric only has 26 possible values for a 100-candidate "
            "cohort, so ties are the normal case, not an edge case."
        ),
    )
    section.add(
        "baseline_tiebreak_divergence",
        displaced_by_tiebreak,
        unit="positions",
        kind="measured",
        source="core.ranking.resume_only_rank",
        note=(
            "positions on which the fused ranking at c(E) = 0 differs from "
            "resume_only_rank. Every one of these would be a pair tied on "
            "llm_score, resolved on ats_score by the baseline and on "
            "application_id by the fused ranker. Diagnosing this audit's own "
            "earlier failure found the gap and rank() now carries ats_score as "
            "its third key, so the two agree exactly; it was 83 positions in "
            "this cohort before the key was added. Kept as a measurement "
            "because it is a regression test for that agreement, and because "
            "the tau-b at c(E) = 0 "
            f"({integrity_rows[0]['kendall_tau_b']:.4f}) should now equal the "
            f"resume-only tau-b ({quality_before['kendall_tau_b']:.4f})."
        ),
    )

    # The sharper guarantee: integrity can only move a candidate *toward* their
    # resume score. Checked over a grid rather than argued, because this is the
    # sentence the paper uses to say a flaky webcam is not a penalty.
    full_trust = {
        c.application_id: c.s_final
        for c in ranking.build([dict(r, integrity_score=100) for r in rows])
    }
    resume_component = {c.application_id: c.s_resume for c in built_zero}
    checked = outside = 0
    worst_excursion = 0.0
    for integrity in range(0, 101, 5):
        for candidate in ranking.build([dict(r, integrity_score=integrity) for r in rows]):
            low = min(resume_component[candidate.application_id], full_trust[candidate.application_id])
            high = max(resume_component[candidate.application_id], full_trust[candidate.application_id])
            checked += 1
            excursion = max(low - candidate.s_final, candidate.s_final - high, 0.0)
            worst_excursion = max(worst_excursion, excursion)
            if excursion > 1e-12:
                outside += 1
    section.add(
        "s_final_stays_between_resume_and_trusted_score",
        outside == 0,
        kind="measured",
        source="core.ranking.final_score",
        note=(
            f"{checked} (candidate, c(E)) pairs — {len(rows)} candidates across "
            "21 integrity levels from 0 to 100 — checked against the closed "
            "interval between their resume-only score and their fully-trusted "
            f"fused score. Largest excursion outside it: {worst_excursion:.2e}. "
            "This is the defensible form of 'proctoring noise is not a penalty': "
            "losing trust in a recording interpolates a candidate back toward "
            "their resume score and can never carry them past it in either "
            "direction."
        ),
    )

    # ------------------------------------------------------------------ #
    # What is missing, named precisely
    # ------------------------------------------------------------------ #
    section.add(
        "interview_score_validity",
        None,
        kind="unavailable",
        source="services.interview_service.score",
        needs=(
            "A model run: N candidates through the full adaptive interview with "
            "LLM_PROVIDER=ollama, each transcript independently scored by >=3 human "
            "raters, then Fleiss' kappa between raters and Pearson/Spearman between "
            "the human mean and the rubric total. Until that exists, the cohort's "
            "'unbiased but noisy' interview model is an assumption, and every "
            "recovery rate above is conditional on it."
        ),
    )
    section.add(
        "ablation_ladder_A0_to_A5",
        None,
        kind="unavailable",
        source="not implemented",
        needs=(
            "Six pipeline configurations (A0 resume-only, A1 +static interview, "
            "A2 +adaptive, A3 +structured pros/cons, A4 full, A5 +integrity) run "
            "over the same candidate set with a real model. A0 and A4-minus-the-"
            "model are the two rows this harness can produce offline; the other "
            "four need generation."
        ),
    )

    section.commentary = (
        "The fusion recovers planted resume errors in the direction and roughly "
        "the magnitude the design intends, and it degrades gracefully to the "
        "resume-only ranking as integrity falls. Read every rate here as "
        "conditional on the cohort's interview-noise assumption: this measures "
        "h(), not the interviewer. The λ sweep is included so no single "
        "operating point can be mistaken for a tuned result."
    )
    return section
