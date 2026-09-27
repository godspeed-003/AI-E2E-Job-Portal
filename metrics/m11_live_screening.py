"""M11 — what does a real model actually do with an inflated resume?

Every ranking result in this harness rests on one assumption, stated in
:func:`metrics.corpora.make_cohort` and never tested: that a resume screener
reads an inflated resume and is *fooled by it* — that the screening score tracks
what the resume claims rather than what the candidate can do. The whole
over-seller / hidden-gem design is downstream of that. If a real model saw
through inflation on its own there would be nothing for the interview stage to
correct, and the paper's central argument would be about a problem that does not
exist.

So this module stops simulating the screener. It drives the shipped
``services.application_service.screen`` path against a real model, on real rows,
in the throwaway database, using the production prompt template and the
production schema, and asks three questions:

**1. Is the model fooled?** Case A (inflated resume, low competence) and case D
(honest resume, low competence) contain candidates of the *same* underlying
competence. Only the resume differs. If the model scores A above D, inflation
works on it, and the assumption holds — measured, not assumed.

**2. Does the real score track the resume or the competence?** Spearman's ρ of
the real 0–25 score against the generator's resume view, and against the hidden
ground truth. The gap between those two correlations *is* the screening error
the pipeline exists to correct, and it is now a real number rather than a
parameter we chose.

**3. Was ``resume_llm_simulated`` a fair stand-in?** M4 reports a baseline built
from the generator's synthetic ``llm_score``. Correlating it against the real
model's score on the same candidates says whether that baseline was
representative. A low correlation would not invalidate M4's arithmetic, but it
would mean the baseline is a statement about the generator and must be relabelled
in the paper.

Design decisions worth arguing with
-----------------------------------

*The case label is removed from the resume text.* ``make_resume_text`` normally
plants "A Over Seller candidate." somewhere in the document — fine for lexical
baselines that cannot read it, fatal here. ``reveal_case=False`` is passed, and
the candidate name sent to the model is a neutral placeholder rather than the
cohort's ``A_over_seller#3``. Without both, this module would measure whether
Gemini can read English labels.

*Screening is forced past the ATS floor.* ``force=True`` on every candidate, so
a real score exists for all of them. The floor's behaviour is already measured
exactly in :mod:`metrics.m3_ats`; letting it truncate the cohort here would
measure the filter and report it as the model.

*It is opt-in and cached.* Same contract as :mod:`metrics.m9_provider_contract`:
nothing leaves the host unless ``METRICS_LIVE_PROVIDER`` says so, and a completed
run leaves its rows on disk so the offline pass can still report the numbers with
the date they were measured.

What this still is not
----------------------

A real *screener* measured against a *synthetic* cohort. The resumes are
generated, so this validates the generator's assumption about screening; it does
not establish that the model screens real resumes well. And it says nothing at
all about the interview stage, which remains the largest gap in the paper and
needs human adjudication. Every quantity here is labelled ``measured`` where it
describes the model's behaviour and ``simulated`` where the cohort supplies the
ground truth it is scored against — the distinction is in the label on every row.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from metrics import RESULTS_DIR, Section, write_json
from metrics.corpora import CASES, SEED, make_cohort, make_resume_text
from metrics.stats import kendall_tau_b, ndcg_at_k, precision_at_k, spearman_rho, summarize

LIVE_ENV_VAR = "METRICS_LIVE_PROVIDER"
LIVE_CACHE = RESULTS_DIR / "live_screening.json"

# Candidates per case. 10 x 4 = 40 model calls, which is a real sample for a
# rank correlation and stays well inside a free-tier daily allowance. The number
# is here rather than buried in run() because it is the first thing a reader
# should be able to check against the confidence intervals.
PER_CASE = 10

# Minimum seconds between the *starts* of two model calls, not between the end
# of one and the start of the next. The free tier allows roughly 10 requests per
# minute and a screening call takes several seconds on its own, so a fixed sleep
# between calls would pace correctly when the API is slow and overrun it when the
# API is fast — exactly backwards. 6.5s between starts holds the run at about
# 9 RPM whatever the API does. It matters because a 429 mid-cohort leaves a
# partial sample, and a partial sample of a stratified design is not a smaller
# version of the same measurement: it is the last cases missing.
MIN_SECONDS_BETWEEN_CALLS = 6.5

# One retry after a rate-limit or transient failure, after this pause. A second
# failure records the row as an error rather than aborting: 39 of 40 candidates
# is still a usable measurement provided the failure is reported, which it is.
RETRY_BACKOFF_SECONDS = 25.0


def _stratified(cohort, per_case: int, requirements: list[str]):
    """``per_case`` candidates from each case, taken in id order.

    In id order rather than sampled: the cohort is already the output of a
    seeded generator, so re-sampling it would add a second layer of randomness
    for no gain, and "the first ten of each case" is a rule a reader can apply
    themselves.

    Candidates whose resume does not clear the portal's own 40-word intake floor
    are passed over here rather than discovered mid-run. Dropping the case-label
    sentence (``reveal_case=False``, which this module requires — see the module
    docstring) costs every resume a few words, and the shortest resumes belong to
    the lowest-competence candidates, who claim the fewest requirements. So the
    floor bites unevenly across cases, and the count of what it skipped is
    reported alongside the results rather than left implicit: a subsample that
    silently excludes the weakest candidates would overstate the screener.

    Returns ``(chosen, skipped_per_case)``.
    """
    from core.resume import MIN_RESUME_WORDS

    picked, skipped = [], {case: 0 for case in CASES}
    for case in CASES:
        taken = 0
        for candidate in sorted(cohort.by_case(case), key=lambda c: c.candidate_id):
            if taken >= per_case:
                break
            text, _ = make_resume_text(candidate, requirements, reveal_case=False)
            if len(text.split()) < MIN_RESUME_WORDS:
                skipped[case] += 1
                continue
            picked.append(candidate)
            taken += 1
    return picked, skipped
    picked = []
    for case in CASES:
        picked.extend(sorted(cohort.by_case(case), key=lambda c: c.candidate_id)[:per_case])
    return picked


def _run_live(per_case: int) -> dict[str, Any]:
    """Screen a stratified subsample with the real model. Returns cache-shaped rows."""
    from core import db
    from llm import build_provider, set_provider
    from services import application_service as applications
    from services import auth_service as auth
    from services import catalog_service as catalog
    from core import resume as resume_core

    provider = build_provider("gemini")
    set_provider(provider)

    db.init_db()
    catalog.seed_from_json()
    roles = catalog.list_roles()
    if not roles:
        raise RuntimeError("no roles seeded; catalog.seed_from_json() found nothing")
    # The role with the most requirements gives the generator the widest range of
    # claim ratios to express, so the A/D contrast is as visible as it can be.
    role = max(roles, key=lambda r: len(r.requirements))
    requirements = list(role.requirements)

    cohort = make_cohort(seed=SEED)
    chosen, skipped = _stratified(cohort, per_case, requirements)
    if any(skipped.values()):
        print(
            "      skipped below the 40-word intake floor: "
            + ", ".join(f"{c}={n}" for c, n in skipped.items() if n),
            flush=True,
        )

    rows: list[dict[str, Any]] = []
    next_call_at = 0.0
    for index, candidate in enumerate(chosen):
        # Setup is inside the try for the same reason the model call is: a single
        # candidate the portal refuses to admit must cost one row, not the run.
        # The first version of this loop let a ResumeError propagate and lost 26
        # of 40 already-paid-for calls.
        try:
            text, claimed = make_resume_text(candidate, requirements, reveal_case=False)
            resume = resume_core.from_text(text)
            user = auth.register(
                f"m11-{candidate.candidate_id}@metrics.local",
                "metrics-harness-1234",
                full_name="Candidate",  # never the case label
                is_sandbox=True,
            )
            application = applications.apply(user, role.id, resume, is_sandbox=True)
        except Exception as exc:  # noqa: BLE001 — recorded as a row, not raised
            print(
                f"      {index + 1:>2}/{len(chosen)} {candidate.case:<16} "
                f"-> SETUP FAILED ({type(exc).__name__})",
                flush=True,
            )
            rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "case": candidate.case,
                    "competence": round(candidate.competence, 4),
                    "simulated_llm_score": candidate.llm_score,
                    "real_llm_score": None,
                    "ats_score": None,
                    "would_have_failed_ats_floor": None,
                    "requirements_claimed": None,
                    "requirements_available": len(requirements),
                    "resume_chars": None,
                    "seconds": 0.0,
                    "criteria": {},
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
            )
            continue

        real_score: int | None = None
        criteria: dict[str, Any] = {}
        error = ""
        started = time.time()
        for attempt in (1, 2):
            wait = next_call_at - time.time()
            if wait > 0:
                time.sleep(wait)
            next_call_at = time.time() + MIN_SECONDS_BETWEEN_CALLS
            started = time.time()
            try:
                # force=True: a real score for every candidate. The ATS floor is
                # measured exactly in m3; letting it drop the weak half here would
                # report the filter's behaviour as the model's.
                screened = applications.screen(application.id, force=True)
                real_score = screened.llm_score
                criteria = screened.criteria if isinstance(screened.criteria, dict) else {}
                error = ""
                break
            except Exception as exc:  # noqa: BLE001 — recorded as a row, not raised
                real_score, criteria = None, {}
                error = f"{type(exc).__name__}: {exc}"[:200]
                if attempt == 1:
                    print(f"      retrying {candidate.candidate_id}: {error[:80]}")
                    next_call_at = time.time() + RETRY_BACKOFF_SECONDS

        print(
            f"      {index + 1:>2}/{len(chosen)} {candidate.case:<16} "
            f"-> {real_score if real_score is not None else 'ERROR':>5}",
            flush=True,
        )

        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "case": candidate.case,
                "competence": round(candidate.competence, 4),
                "simulated_llm_score": candidate.llm_score,
                "real_llm_score": real_score,
                "ats_score": application.ats_score,
                "would_have_failed_ats_floor": bool(
                    application.ats_score < role.ats_floor
                ),
                "requirements_claimed": len(claimed),
                "requirements_available": len(requirements),
                "resume_chars": len(text),
                "seconds": round(time.time() - started, 3),
                "criteria": criteria,
                "error": error,
            }
        )

    return {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": provider.model,
        "provider": provider.name,
        "role_title": role.title,
        "role_ats_floor": role.ats_floor,
        "requirements": len(requirements),
        "per_case": per_case,
        "cohort_seed": SEED,
        "skipped_below_intake_floor": skipped,
        "candidates": rows,
    }


def _read_cache() -> dict[str, Any] | None:
    if not LIVE_CACHE.exists():
        return None
    try:
        return json.loads(LIVE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _report(section: Section, payload: dict[str, Any], *, replayed: bool) -> None:
    rows = [r for r in payload["candidates"] if r["real_llm_score"] is not None]
    failed = [r for r in payload["candidates"] if r["real_llm_score"] is None]
    model = payload.get("model", "unknown")
    when = (
        f" Measured live on {payload.get('measured_at_utc', 'an unrecorded date')}"
        + ("; replayed from cache." if replayed else " during this run.")
    )

    section.tables["candidates"] = [
        {k: v for k, v in row.items() if k != "criteria"} for row in payload["candidates"]
    ]

    section.add(
        "candidates_screened_by_a_real_model",
        len(rows),
        unit="candidates",
        kind="measured",
        source="services.application_service.screen via llm.gemini",
        note=(
            f"{payload['per_case']} per case x {len(CASES)} cases against {model}, "
            f"role '{payload['role_title']}' with {payload['requirements']} "
            f"requirements. {len(failed)} calls failed and are excluded. "
            "The shipped prompt template and the shipped response schema — this "
            "is the production screening path, not a re-implementation of it."
            + when
        ),
    )
    if failed:
        section.add(
            "screening_calls_that_failed",
            len(failed),
            unit="candidates",
            kind="measured",
            source="services.application_service.screen",
            note="; ".join(sorted({r["error"] for r in failed}))[:400] + when,
        )

    skipped = payload.get("skipped_below_intake_floor") or {}
    if any(skipped.values()):
        section.add(
            "candidates_passed_over_below_the_intake_floor",
            sum(skipped.values()),
            unit="candidates",
            kind="measured",
            source="metrics.m11_live_screening._stratified",
            note=(
                "Generated resumes shorter than core.resume.MIN_RESUME_WORDS (40) "
                "once the case-label sentence is removed, so the portal itself "
                "would refuse them at intake. Per case: "
                + ", ".join(f"{c}={n}" for c, n in skipped.items() if n)
                + ". The skip is not uniform across cases — the shortest resumes "
                "belong to candidates who claim the fewest requirements — so this "
                "subsample is very slightly biased toward the more verbose "
                "candidate within each case, and any A-minus-D gap below should "
                "be read with that in mind."
                + when
            ),
        )

    if len(rows) < 8:
        section.add(
            "live_screening_sample",
            None,
            kind="unavailable",
            source="metrics.m11_live_screening",
            needs=(
                f"only {len(rows)} usable rows, too few for a rank correlation. "
                "Re-run with a working key and quota."
            ),
        )
        return

    by_case = {
        case: [r for r in rows if r["case"] == case]
        for case in CASES
    }
    case_rows = []
    for case, bucket in by_case.items():
        if not bucket:
            continue
        real = [float(r["real_llm_score"]) for r in bucket]
        sim = [float(r["simulated_llm_score"]) for r in bucket]
        case_rows.append(
            {
                "case": case,
                "n": len(bucket),
                "mean_competence": round(
                    sum(r["competence"] for r in bucket) / len(bucket), 3
                ),
                "mean_real_score": round(sum(real) / len(real), 2),
                "mean_simulated_score": round(sum(sim) / len(sim), 2),
                "real_minus_simulated": round(
                    sum(real) / len(real) - sum(sim) / len(sim), 2
                ),
                "min_real": min(real),
                "max_real": max(real),
            }
        )
    section.tables["by_case"] = case_rows

    # ------------------------------------------------------------------ #
    # 1. Is the model fooled by inflation?
    # ------------------------------------------------------------------ #
    over = by_case.get("A_over_seller") or []
    weak = by_case.get("D_honest_weak") or []
    if over and weak:
        over_mean = sum(float(r["real_llm_score"]) for r in over) / len(over)
        weak_mean = sum(float(r["real_llm_score"]) for r in weak) / len(weak)
        over_comp = sum(r["competence"] for r in over) / len(over)
        weak_comp = sum(r["competence"] for r in weak) / len(weak)
        section.add(
            "inflation_premium_on_the_real_model",
            round(over_mean - weak_mean, 2),
            unit="points of 25",
            kind="measured",
            source="services.application_service.screen via llm.gemini",
            note=(
                f"over-sellers averaged {over_mean:.2f}/25 and honest-weak "
                f"candidates {weak_mean:.2f}/25, while their ground-truth "
                f"competence differed by only "
                f"{abs(over_comp - weak_comp):.3f} on a 0-1 scale "
                f"({over_comp:.3f} vs {weak_comp:.3f} — both drawn from the same "
                "0.05-0.40 band by construction). The difference is therefore "
                "attributable to the resume text and not to the candidate. "
                + (
                    "A positive premium is the finding the whole pipeline is "
                    "premised on, now measured against a real model instead of "
                    "assumed by the generator."
                    if over_mean > weak_mean
                    else "A non-positive premium would mean the model sees "
                    "through inflation unaided, and the paper's central premise "
                    "would need rewriting — read this number carefully before "
                    "citing anything downstream of it."
                )
                + when
            ),
        )
        section.add(
            "real_model_is_fooled_by_resume_inflation",
            bool(over_mean > weak_mean),
            kind="measured",
            source="services.application_service.screen via llm.gemini",
            note=(
                "the assumption in metrics.corpora.make_cohort, tested rather "
                "than asserted. Same competence band, different resume, "
                f"{'higher' if over_mean > weak_mean else 'not higher'} score."
                + when
            ),
        )

    # ------------------------------------------------------------------ #
    # 2. Does the real score track the resume, or the competence?
    # ------------------------------------------------------------------ #
    real = [float(r["real_llm_score"]) for r in rows]
    sim = [float(r["simulated_llm_score"]) for r in rows]
    truth = [r["competence"] for r in rows]

    rho_truth = spearman_rho(real, truth)
    rho_sim = spearman_rho(real, sim)
    section.add(
        "real_score_vs_ground_truth_competence",
        round(rho_truth, 4),
        unit="Spearman ρ",
        kind="measured",
        source="metrics.stats.spearman_rho over the live scores",
        note=(
            f"n = {len(rows)}. This is the screening stage's accuracy against a "
            "known truth, and the quantity the interview stage exists to improve "
            "on. It is a correlation with a *generated* competence value, so it "
            "measures the real model on a synthetic cohort — the model's half is "
            "measured, the truth's half is simulated." + when
        ),
    )
    section.add(
        "real_score_vs_the_resume_view_the_generator_planted",
        round(rho_sim, 4),
        unit="Spearman ρ",
        kind="measured",
        source="metrics.stats.spearman_rho over the live scores",
        note=(
            f"n = {len(rows)}. How closely the real model agrees with the "
            "generator's synthetic llm_score. This is the number that says "
            "whether M4's `resume_llm_simulated` baseline was a fair stand-in: "
            + (
                "it is the stronger of the two correlations, so the real model "
                "tracks what the resume claims rather than what the candidate "
                "can do — exactly the behaviour the generator models."
                if rho_sim > rho_truth
                else "it is the WEAKER of the two correlations, so the real "
                "model tracks true competence better than the generator's "
                "resume view does. The simulated baseline is then pessimistic "
                "about the screener and M4 must say so."
            )
            + when
        ),
    )
    section.add(
        "simulated_baseline_is_representative_of_a_real_screener",
        bool(rho_sim > rho_truth),
        kind="measured",
        source="metrics.m11_live_screening — the two correlations above",
        note=(
            f"ρ(real, resume view) = {rho_sim:.4f} against "
            f"ρ(real, competence) = {rho_truth:.4f}. The gap, "
            f"{rho_sim - rho_truth:+.4f}, is the screening error the fusion "
            "stage is asked to recover, measured on a real model rather than "
            "chosen as a parameter." + when
        ),
    )

    # ------------------------------------------------------------------ #
    # 3. Ranking quality of a real screener, for the baseline table
    # ------------------------------------------------------------------ #
    k = min(10, len(rows) // 2)
    ordered = sorted(rows, key=lambda r: (-float(r["real_llm_score"]), r["candidate_id"]))
    relevant = {
        r["candidate_id"]
        for r in sorted(rows, key=lambda r: -r["competence"])[:k]
    }
    relevances = [r["competence"] for r in ordered]
    hits = [r["candidate_id"] in relevant for r in ordered]
    positions = list(range(len(ordered), 0, -1))

    section.add(
        "real_llm_resume_only_tau_b",
        round(kendall_tau_b(positions, relevances), 4),
        unit="τ-b",
        kind="measured",
        source="metrics.m11_live_screening — real scores ranked against truth",
        note=(
            f"n = {len(rows)}, the real-model replacement for M4's "
            "`resume_llm_simulated` row. Ties broken by candidate id, which "
            "matters: a real screener produces heavy ties on a 0-25 rubric and "
            "an arbitrary tiebreak would inflate or deflate this figure "
            "depending on the direction chosen." + when
        ),
    )
    section.add(
        "real_llm_resume_only_ndcg_at_k",
        round(ndcg_at_k(relevances, k), 4),
        unit=f"NDCG@{k}",
        kind="measured",
        source="metrics.m11_live_screening — real scores ranked against truth",
        note=f"k = {k}, half the subsample, since k = 10 of 40 is a quarter." + when,
    )
    section.add(
        "real_llm_resume_only_precision_at_k",
        round(precision_at_k(hits, k), 4),
        unit=f"P@{k}",
        kind="measured",
        source="metrics.m11_live_screening — real scores ranked against truth",
        note=(
            f"of the top {k} the real screener produced, this share are genuinely "
            f"in the true top {k}." + when
        ),
    )

    distinct = len({r["real_llm_score"] for r in rows})
    section.add(
        "distinct_scores_the_real_model_produced",
        distinct,
        unit="values",
        kind="measured",
        source="services.application_service.screen via llm.gemini",
        note=(
            f"{distinct} distinct values across {len(rows)} candidates on a "
            f"26-point rubric. Directly relevant to the paper's tie problem: "
            f"the synthetic cohort has 97 of 100 candidates sharing an "
            f"llm_score, and this says whether a real model is comparably "
            "coarse. A screener that cannot separate candidates is the "
            "strongest argument for a second stage, and it is also why τ-b "
            "rather than τ-a is used throughout." + when
        ),
    )

    ats_would_reject = sum(1 for r in rows if r["would_have_failed_ats_floor"])
    section.add(
        "subsample_the_ats_floor_would_have_rejected",
        ats_would_reject,
        unit="candidates",
        kind="measured",
        source="application.ats_score < role.ats_floor",
        note=(
            f"{ats_would_reject} of {len(rows)} fell below the floor of "
            f"{payload['role_ats_floor']} and were screened anyway with "
            "force=True, so the correlations above are over the whole subsample. "
            "In production these would never reach the model — a cost saving "
            "measured in m3_ats and a recall risk measured there too." + when
        ),
    )

    latencies = [r["seconds"] for r in rows if r.get("seconds")]
    if latencies:
        stats_summary = summarize(latencies)
        section.add(
            "real_screening_call_seconds_p50",
            round(stats_summary["p50"], 3),
            unit="s",
            kind="measured",
            source="wall-clock around services.application_service.screen",
            note=(
                f"p95 {stats_summary['p95']:.3f}s, max {stats_summary['max']:.3f}s, "
                f"n = {len(latencies)}. Hosted round trip plus the portal's own "
                "work, over the network, from one location — NOT local inference "
                "latency, and not comparable to an Ollama figure. It does bound "
                "one honest claim: a candidate waits about this long for a "
                "screening decision on the cloud backend." + when
            ),
        )

    section.commentary = (
        "The module exists to test an assumption rather than to add a result. "
        "The over-seller premium is the load-bearing number: cases A and D are "
        "drawn from the same competence band and differ only in what the resume "
        "claims, so the score gap between them is the screening error, measured "
        "on a real model at the shipped prompt. Everything the paper says about "
        "the interview stage correcting the resume stage depends on that gap "
        "being real and positive. The correlations then place M4's simulated "
        "baseline: if the real model agrees with the generator's resume view "
        "more than with the hidden truth, the simulated baseline was a fair "
        "stand-in and can stay in the table with a note; if not, it is a "
        "statement about our generator and must be relabelled. Neither result "
        "touches the interview stage, which still needs human raters."
    )


def run(per_case: int = PER_CASE) -> Section:
    section = Section(
        key="m11_live_screening",
        title="Real-model screening — is the screener fooled the way the cohort assumes?",
    )

    requested = (os.environ.get(LIVE_ENV_VAR) or "").strip().lower()
    if requested == "gemini":
        try:
            payload = _run_live(per_case)
        except Exception as exc:  # noqa: BLE001 — reported, never fatal to the run
            section.add(
                "live_screening_sample",
                None,
                kind="unavailable",
                source="metrics.m11_live_screening._run_live",
                needs=f"the live run failed: {type(exc).__name__}: {exc}",
            )
            return section
        # Only cache a run that is worth replaying. Two failure modes, both real:
        # a run where every call 404s would otherwise write 40 null scores that
        # every later offline run replays as though they were a measurement, and —
        # worse — it would overwrite a good cache from a previous run with them.
        # The threshold matches _report's own floor for computing a correlation.
        usable = [r for r in payload["candidates"] if r["real_llm_score"] is not None]
        if len(usable) >= 8:
            write_json(LIVE_CACHE, payload)
        else:
            print(
                f"      not caching: only {len(usable)} of "
                f"{len(payload['candidates'])} calls returned a score",
                flush=True,
            )
        _report(section, payload, replayed=False)
        return section

    cached = _read_cache()
    if cached and cached.get("candidates"):
        _report(section, cached, replayed=True)
        return section

    for name, what in (
        (
            "real_model_is_fooled_by_resume_inflation",
            "whether a real screener scores an inflated resume above an honest "
            "one from the same competence band — the assumption every ranking "
            "result in this harness is built on",
        ),
        (
            "real_score_vs_ground_truth_competence",
            "the screening stage's rank correlation with a known truth, using a "
            "real model instead of the generator's synthetic score",
        ),
        (
            "simulated_baseline_is_representative_of_a_real_screener",
            "whether M4's `resume_llm_simulated` baseline stands in fairly for a "
            "real screener, or is only a statement about our generator",
        ),
        (
            "real_llm_resume_only_tau_b",
            "the real-model replacement for the simulated resume-only baseline",
        ),
    ):
        section.add(
            name,
            None,
            kind="unavailable",
            source="metrics.m11_live_screening._run_live",
            needs=(
                f"{what}. Costs {per_case * len(CASES)} model calls, paced at one "
                f"start every {MIN_SECONDS_BETWEEN_CALLS}s to stay inside a "
                f"free-tier rate limit (about "
                f"{per_case * len(CASES) * MIN_SECONDS_BETWEEN_CALLS / 60:.0f} "
                f"minutes): `{LIVE_ENV_VAR}=gemini .venv/Scripts/python -m "
                "metrics.run_all --only m11_live_screening`. Sends generated "
                "resume text only — no real candidate data exists in this "
                "project's throwaway database. "
                "Attempted on 2026-09-26 and not completed: the free-tier daily "
                "request allowance on both configured keys was exhausted partway "
                "through, and every remaining call returned HTTP 429. A 40-call "
                "validation run is therefore not reliably completable in one day "
                "on the free hosted tier — which is a measured argument for the "
                "local-Ollama path, not merely an inconvenience. Re-run with "
                "restored quota, or point LLM_PROVIDER at a local model."
            ),
        )
    return section
