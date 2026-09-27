"""M4 — lexical baselines: TF-IDF and BM25 against the same cohort.

A paper that claims an LLM pipeline beats keyword screening has to say what it
beat. The two standard lexical retrieval baselines are implemented here rather
than imported, for the same reason the statistics are: 40 MB of scientific stack
to produce two rankings is a bad trade, and a reviewer can check twenty lines of
BM25 but not a vendored dependency.

The experiment
--------------

One role's requirement list is the query. The cohort's generated resumes are the
documents. Four rankers are run and each is scored against the **latent
competence** the cohort was generated from:

* ``ats`` — ``core.resume.ats_score``, the portal's actual pre-filter
* ``tfidf`` — cosine similarity, ``ltc`` weighting (log tf, idf, cosine norm)
* ``bm25`` — Okapi BM25, k₁ = 1.5, b = 0.75
* ``resume_llm`` — the cohort's simulated screening score

The result this is set up to expose is not "our system is better". It is
structural: **every ranker in that list reads the same document, and the
document contains claims.** An over-seller's resume claims every requirement,
so TF-IDF, BM25 and the keyword gate all rank them highly — not because the
methods are weak but because the evidence they are given is self-reported.
That is the argument for conducting an interview at all, and it is measurable
without a single model call.

Honesty label: **simulated.** The documents are generated (:mod:`metrics.corpora`),
so this measures how lexical retrieval behaves on self-reported evidence with a
known ground truth. It is not a measurement on real resumes, and no claim about
real-world screening accuracy follows from it.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter

from core import resume as resume_core
from metrics import PROJECT_ROOT, Section
from metrics.corpora import make_cohort, make_resume_text
from metrics.stats import (
    kendall_tau_b,
    mrr,
    ndcg_at_k,
    precision_at_k,
    spearman_rho,
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*")

BM25_K1 = 1.5
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping ``c++``/``c#``/``node.js`` intact.

    Deliberately the same character class as ``core.resume._NORMALIZE_RE`` so
    the baselines and the portal's own matcher see the same tokens and any
    difference in their rankings is the method, not the tokeniser.
    """
    return _TOKEN_RE.findall((text or "").lower())


def _idf(documents: list[list[str]]) -> dict[str, float]:
    n = len(documents)
    document_frequency: Counter[str] = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))
    # Smoothed idf: log(1 + N/df). Never negative, unlike the raw BM25 idf,
    # which goes negative for terms in over half the corpus and can then make a
    # *more* relevant document score lower.
    return {term: math.log(1 + n / df) for term, df in document_frequency.items()}


def tfidf_scores(query: str, documents: list[str]) -> list[float]:
    """Cosine similarity with log-tf, smoothed idf, and L2 normalisation."""
    doc_tokens = [tokenize(d) for d in documents]
    idf = _idf(doc_tokens)
    query_tokens = tokenize(query)

    def vector(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        raw = {
            term: (1 + math.log(count)) * idf.get(term, 0.0)
            for term, count in counts.items()
        }
        norm = math.sqrt(sum(v * v for v in raw.values()))
        return {} if norm == 0 else {t: v / norm for t, v in raw.items()}

    query_vector = vector(query_tokens)
    scores = []
    for tokens in doc_tokens:
        doc_vector = vector(tokens)
        scores.append(
            sum(weight * doc_vector.get(term, 0.0) for term, weight in query_vector.items())
        )
    return scores


def bm25_scores(
    query: str, documents: list[str], *, k1: float = BM25_K1, b: float = BM25_B
) -> list[float]:
    """Okapi BM25 with the smoothed idf above."""
    doc_tokens = [tokenize(d) for d in documents]
    idf = _idf(doc_tokens)
    lengths = [len(t) for t in doc_tokens]
    average_length = sum(lengths) / len(lengths) if lengths else 0.0
    query_terms = tokenize(query)

    scores = []
    for tokens, length in zip(doc_tokens, lengths):
        counts = Counter(tokens)
        total = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            denominator = frequency + k1 * (
                1 - b + b * (length / average_length if average_length else 1.0)
            )
            total += idf.get(term, 0.0) * frequency * (k1 + 1) / denominator
        scores.append(total)
    return scores


def _evaluate(
    ranked_ids: list[int],
    truth: dict[int, float],
    *,
    k: int,
    relevant: set[int],
) -> dict[str, float]:
    """Score one ranking against the latent competence ground truth."""
    relevances = [truth[i] for i in ranked_ids]
    hits = [i in relevant for i in ranked_ids]
    ordered_truth = [truth[i] for i in ranked_ids]
    positions = list(range(len(ranked_ids), 0, -1))  # best first → highest value
    return {
        f"ndcg@{k}": ndcg_at_k(relevances, k),
        f"precision@{k}": precision_at_k(hits, k),
        "mrr": mrr(hits),
        "kendall_tau_b": kendall_tau_b(positions, ordered_truth),
        "spearman_rho": spearman_rho(positions, ordered_truth),
    }


def run(n_per_case: int = 25, k: int = 10) -> Section:
    section = Section(
        key="m4_baselines",
        title="Lexical baselines (ATS, TF-IDF, BM25) on self-reported evidence",
    )

    roles = json.loads(
        (PROJECT_ROOT / "data" / "roles.json").read_text(encoding="utf-8")
    )
    # Use the role with the most requirements: the query has to be rich enough
    # for TF-IDF and BM25 to differ from a flat keyword count.
    role = max(roles, key=lambda r: len(r.get("requirements") or []))
    requirements = list(role.get("requirements") or [])
    query = " ".join([role.get("title", ""), role.get("job_description", ""), *requirements])

    cohort = make_cohort(n_per_case=n_per_case)
    documents, claims = [], []
    for candidate in cohort.candidates:
        text, claimed = make_resume_text(candidate, requirements)
        documents.append(text)
        claims.append(claimed)

    ids = [c.candidate_id for c in cohort.candidates]
    truth = cohort.truth
    # "Relevant" = genuinely in the top quartile by latent competence. Defined on
    # competence, never on any score a ranker produces.
    cutoff = sorted(truth.values(), reverse=True)[max(0, len(truth) // 4 - 1)]
    relevant = {i for i, value in truth.items() if value >= cutoff}

    section.add(
        "role_used",
        f"{role.get('role_id')} ({len(requirements)} requirements)",
        kind="specification",
        source="data/roles.json",
    )
    section.add(
        "cohort_size",
        len(ids),
        unit="candidates",
        kind="specification",
        source="metrics.corpora.make_cohort",
        note=f"{n_per_case} per case across 4 planted cases; seed {cohort.seed}",
    )
    section.add(
        "relevant_set_size",
        len(relevant),
        unit="candidates",
        kind="specification",
        source="metrics.corpora.make_cohort",
        note="top quartile by latent competence — defined on truth, not on any score",
    )
    section.add(
        "mean_document_length",
        sum(len(tokenize(d)) for d in documents) / len(documents),
        unit="tokens",
        kind="measured",
        source="metrics.corpora.make_resume_text",
    )

    # ------------------------------------------------------------------ #
    # Run the rankers
    # ------------------------------------------------------------------ #
    ats = [resume_core.ats_score(d, requirements).score for d in documents]
    tfidf = tfidf_scores(query, documents)
    bm25 = bm25_scores(query, documents)
    resume_llm = [c.llm_score for c in cohort.candidates]

    rankers = {
        "ats_keyword": ats,
        "tfidf_cosine": tfidf,
        "bm25": bm25,
        "resume_llm_simulated": resume_llm,
    }

    results = {}
    for name, scores in rankers.items():
        order = sorted(range(len(ids)), key=lambda i: (-scores[i], ids[i]))
        ranked_ids = [ids[i] for i in order]
        results[name] = _evaluate(ranked_ids, truth, k=k, relevant=relevant)
        results[name]["top_k_cases"] = Counter(
            cohort.candidates[i].case for i in order[:k]
        )
    section.tables["ranker_quality"] = {
        name: {kk: vv for kk, vv in values.items() if kk != "top_k_cases"}
        for name, values in results.items()
    }
    section.tables["top_k_case_mix"] = {
        name: dict(values["top_k_cases"]) for name, values in results.items()
    }

    for name, values in results.items():
        section.add(
            f"{name}_ndcg@{k}",
            values[f"ndcg@{k}"],
            unit="fraction",
            kind="simulated",
            source=f"metrics.m4_baselines ({name})",
        )
        section.add(
            f"{name}_kendall_tau_b",
            values["kendall_tau_b"],
            kind="simulated",
            source="metrics.stats.kendall_tau_b",
            note="agreement between the ranker's order and latent competence",
        )

    # ------------------------------------------------------------------ #
    # The structural finding: every lexical ranker is fooled by claims
    # ------------------------------------------------------------------ #
    over_seller_ids = {c.candidate_id for c in cohort.by_case("A_over_seller")}
    inflation_rows = []
    for name, scores in rankers.items():
        order = sorted(range(len(ids)), key=lambda i: (-scores[i], ids[i]))
        top = [ids[i] for i in order[:k]]
        inflation_rows.append(
            {
                "ranker": name,
                f"over_sellers_in_top_{k}": sum(1 for i in top if i in over_seller_ids),
                f"genuinely_relevant_in_top_{k}": sum(1 for i in top if i in relevant),
                f"ndcg@{k}": round(results[name][f"ndcg@{k}"], 4),
            }
        )
    section.tables["over_seller_contamination"] = inflation_rows
    section.add(
        "over_sellers_in_top_k_by_ranker",
        {row["ranker"]: row[f"over_sellers_in_top_{k}"] for row in inflation_rows},
        unit=f"of top {k}",
        kind="simulated",
        source="metrics.m4_baselines",
        note=(
            "the point of the baseline table: all four rankers read the same "
            "self-reported document, so an inflated resume defeats all of them. "
            "No amount of retrieval sophistication fixes evidence that is a claim."
        ),
    )

    # ------------------------------------------------------------------ #
    # Ranker-vs-ranker agreement, which needs no ground truth at all
    # ------------------------------------------------------------------ #
    names = list(rankers)
    agreement = {}
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            agreement[f"{left}__vs__{right}"] = kendall_tau_b(
                rankers[left], rankers[right]
            )
    section.tables["ranker_agreement_tau"] = agreement

    section.add(
        "sentence_bert_baseline",
        None,
        kind="unavailable",
        source="not implemented",
        needs=(
            "sentence-transformers plus a model download (~90 MB for MiniLM). "
            "Left out deliberately: it is not a dependency of the portal, and an "
            "embedding baseline reads the same self-reported document as the "
            "lexical ones, so it would move the numbers without changing the "
            "structural finding. Worth adding for the paper's related-work "
            "comparison; state it as an addition, not as a result you have."
        ),
    )

    section.commentary = (
        "ATS, TF-IDF and BM25 are all ranking the same self-reported document, "
        "and the cohort's over-sellers are the candidates whose documents claim "
        "the most. The resulting contamination of the top k is the measured "
        "argument for a second, independent evidence stream — which is what the "
        "interview is. Simulated: documents are generated from a known latent "
        "competence, so this bounds a structural claim, not a real-world one."
    )
    return section
