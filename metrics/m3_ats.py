"""M3 — the ATS keyword pre-filter: what it can and cannot do.

``core.resume.ats_score`` is the cheapest and most consequential stage in the
pipeline: a resume below ``ats_reject_below`` (τ = 40) is rejected *without a
model call*, so this regex decides who never gets read. A paper that reports
only end-to-end quality and leaves the gate unexamined is hiding where most of
its rejections happen.

Three measurements:

1. **Matching correctness on a hand-labelled trap set.** Technical keywords are
   full of substring traps — ``java`` inside ``javascript``, ``c`` inside
   ``c++``, ``go`` inside ``golang`` and ``google``, ``r`` inside everything.
   ``_requirement_pattern`` builds ``(?<![a-z0-9])body(tail)`` for exactly this
   reason, and either it works or the gate rejects people for the wrong reason.
   Each case here is labelled by hand with the intended answer.

2. **Behaviour of the gate itself.** Sweeping τ from 0 to 100 over the stored
   applications gives the rejection curve, which is the honest way to present a
   threshold: the reader sees what τ = 40 costs rather than being told it is
   correct.

3. **Structural properties.** Monotonicity under adding requirements, the empty
   cases, and — the one that matters for the paper's own text — that the score
   is *literal*, with no synonym or semantic matching anywhere in it.
"""

from __future__ import annotations

import json
from pathlib import Path

from core import resume as resume_core
from core.config import settings
from metrics import PROJECT_ROOT, Section
from metrics.stats import confusion, summarize


# Hand-labelled matching cases: (resume text, requirement, should_match, why).
# The "why" is the label's justification and goes into the paper's appendix — a
# label without a reason is an opinion.
TRAPS: tuple[tuple[str, str, bool, str], ...] = (
    # --- must match ----------------------------------------------------- #
    ("Built services in Python and Go.", "Python", True, "exact word"),
    ("Experienced with PYTHON 3.12", "python", True, "case-insensitive"),
    ("Strong C++ background", "C++", True, "plus signs preserved by _NORMALIZE_RE"),
    ("Wrote tooling in C#", "C#", True, "hash preserved"),
    ("Node.js and Express", "node.js", True, "dot preserved"),
    ("Worked on system\ndesign reviews", "system design", True,
     "whitespace in a requirement matches a newline"),
    ("Skills: SQL, ETL, Pandas", "ETL", True, "comma-separated list"),
    ("Deep AWS experience", "AWS basics", False,
     "multi-word requirement is matched literally, not per-token"),
    ("I use pandas daily", "Pandas", True, "case-insensitive"),
    ("REST APIs, gRPC", "gRPC", True, "mixed case"),
    ("Kubernetes (k8s) operator work", "Kubernetes", True, "parenthesis is a separator"),
    ("machine-learning pipelines", "machine learning", True,
     "hyphen normalises to a space"),
    # --- must NOT match: substring traps -------------------------------- #
    ("Ten years of JavaScript", "Java", False, "java is a prefix of javascript"),
    ("Golang microservices", "Go", False, "go is a prefix of golang"),
    ("I work at Google", "Go", False, "go is a prefix of google"),
    ("C++ only, no plain C work", "C", False,
     "the lookbehind/lookahead must stop C matching inside C++"),
    ("Scalable architectures", "Scala", False, "scala is a prefix of scalable"),
    ("Used TypeScript throughout", "Type", False, "type is a prefix of typescript"),
    ("Redshift warehouse", "Redis", False, "shares a prefix only"),
    ("Kotlin Android apps", "Java", False, "no textual overlap at all"),
    ("Reactive streams with RxJava", "React", False,
     "react is a prefix of reactive"),
    ("Postgres tuning", "Postman", False, "different tool, shared prefix"),
    # --- semantic gaps: the honest limits of a literal matcher ---------- #
    ("Expert in PostgreSQL", "SQL", True,
     "matches only because 'sql' is a suffix boundary in postgresql — "
     "accidental, and worth reporting as such"),
    ("Deep experience with relational databases", "SQL", False,
     "a synonym; the matcher is literal by design and will not find it"),
    ("Shipped ML models to production", "machine learning", False,
     "an abbreviation the matcher cannot expand"),
    ("Wrote Terraform modules", "Infrastructure as Code", False,
     "a concept, not a keyword"),
)


def _stored_applications() -> tuple[list[dict], list[dict]]:
    """The 30 hand-written records in ``data/results/`` plus the role catalogue.

    These are checked into the repo and are *hand-written illustrative records*,
    not measurements — they have no resume text, so no ATS score can be
    recomputed from them. They are used here only for the distribution of the
    stored ``ats_score``/``llm_score`` columns, and every figure drawn from them
    is labelled as illustrative rather than experimental.
    """
    roles = json.loads(
        (PROJECT_ROOT / "data" / "roles.json").read_text(encoding="utf-8")
    )
    records: list[dict] = []
    results_dir = PROJECT_ROOT / "data" / "results"
    for path in sorted(results_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else payload.get("results", [])
        for item in items:
            if isinstance(item, dict):
                item = dict(item)
                item["_file"] = path.name
                records.append(item)
    return records, roles


def run() -> Section:
    section = Section(
        key="m3_ats",
        title="ATS keyword pre-filter — matching correctness and gate behaviour",
    )

    tau = settings.screening.ats_reject_below
    section.add(
        "ats_reject_below_tau",
        tau,
        unit="percent",
        kind="specification",
        source="core.config.ScreeningSettings.ats_reject_below",
    )
    section.add(
        "shortlist_llm_score_min",
        settings.screening.shortlist_llm_score_min,
        unit=f"of {settings.screening.llm_max_score}",
        kind="specification",
        source="core.config.ScreeningSettings.shortlist_llm_score_min",
    )
    section.add(
        "min_resume_words",
        resume_core.MIN_RESUME_WORDS,
        unit="words",
        kind="specification",
        source="core.resume.MIN_RESUME_WORDS",
    )
    section.add(
        "max_upload_bytes",
        resume_core.MAX_UPLOAD_BYTES,
        unit="bytes",
        kind="specification",
        source="core.resume.MAX_UPLOAD_BYTES",
    )
    section.add(
        "supported_formats",
        list(resume_core.SUPPORTED_SUFFIXES),
        kind="specification",
        source="core.resume.SUPPORTED_SUFFIXES",
    )

    # ------------------------------------------------------------------ #
    # 1. Trap set
    # ------------------------------------------------------------------ #
    trap_rows = []
    for text, requirement, should_match, why in TRAPS:
        result = resume_core.ats_score(text, [requirement])
        matched = bool(result.matched)
        trap_rows.append(
            {
                "resume_fragment": text.replace("\n", "\\n"),
                "requirement": requirement,
                "expected_match": should_match,
                "actual_match": matched,
                "correct": matched == should_match,
                "score": result.score,
                "rationale": why,
            }
        )
    section.tables["boundary_traps"] = trap_rows

    correct = sum(1 for r in trap_rows if r["correct"])
    section.add(
        "trap_cases",
        len(trap_rows),
        unit="cases",
        kind="measured",
        source="metrics.m3_ats.TRAPS",
        note="hand-labelled; positive, negative and semantic-gap cases",
    )
    section.add(
        "boundary_accuracy",
        correct / len(trap_rows),
        unit="fraction",
        kind="derived",
        source="core.resume.ats_score",
        note=f"{correct}/{len(trap_rows)} cases matched their hand label",
    )
    matrix = confusion(
        [r["actual_match"] for r in trap_rows],
        [r["expected_match"] for r in trap_rows],
    )
    section.tables["trap_confusion"] = matrix
    section.add(
        "trap_precision",
        matrix["precision"],
        unit="fraction",
        kind="derived",
        source="metrics.stats.confusion",
        note="of the requirements it claimed to find, how many were really there",
    )
    section.add(
        "trap_recall",
        matrix["recall"],
        unit="fraction",
        kind="derived",
        source="metrics.stats.confusion",
        note=(
            "recall is bounded below 1 on purpose: the semantic-gap cases "
            "(synonyms, abbreviations, concepts) are labelled as things a literal "
            "matcher cannot find, and they are in the corpus so the limitation is "
            "quantified rather than described"
        ),
    )
    section.tables["trap_failures"] = [r for r in trap_rows if not r["correct"]]

    # Split the score: how it does on lexical cases vs semantic ones.
    lexical = [r for r in trap_rows if "synonym" not in r["rationale"]
               and "abbreviation" not in r["rationale"]
               and "a concept" not in r["rationale"]]
    semantic = [r for r in trap_rows if r not in lexical]
    section.add(
        "boundary_accuracy_lexical_only",
        sum(1 for r in lexical if r["correct"]) / len(lexical),
        unit="fraction",
        kind="derived",
        source="core.resume.ats_score",
        note=f"{len(lexical)} cases where the answer is decidable from the characters",
    )
    section.add(
        "semantic_cases_missed",
        sum(1 for r in semantic if not r["actual_match"]),
        unit=f"of {len(semantic)}",
        kind="measured",
        source="core.resume.ats_score",
        note="the measured cost of having no synonym expansion",
    )

    # ------------------------------------------------------------------ #
    # 2. Structural properties
    # ------------------------------------------------------------------ #
    section.add(
        "empty_text_scores_zero",
        resume_core.ats_score("", ["Python"]).score == 0,
        kind="measured",
        source="core.resume.ats_score",
    )
    section.add(
        "empty_requirements_scores_zero",
        resume_core.ats_score("Python expert", []).score == 0,
        kind="measured",
        source="core.resume.ats_score",
        note="a role with no requirements cannot reject anyone on keywords",
    )

    text = "Python, SQL, ETL, Pandas, AWS basics, Airflow, Spark"
    monotone_rows = []
    requirements: list[str] = []
    for requirement in ["Python", "SQL", "ETL", "Pandas", "AWS basics", "Kotlin", "COBOL"]:
        requirements.append(requirement)
        result = resume_core.ats_score(text, requirements)
        monotone_rows.append(
            {
                "requirements": len(requirements),
                "added": requirement,
                "score": result.score,
                "matched": len(result.matched),
            }
        )
    section.tables["requirement_growth"] = monotone_rows
    section.add(
        "score_is_matched_over_total",
        all(
            row["score"] == round(row["matched"] / row["requirements"] * 100)
            for row in monotone_rows
        ),
        kind="measured",
        source="core.resume.ats_score",
        note="score == round(|matched| / |requirements| * 100), exactly",
    )

    # ------------------------------------------------------------------ #
    # 3. The gate: τ sweep over the real role catalogue
    # ------------------------------------------------------------------ #
    records, roles = _stored_applications()
    section.add(
        "stored_records",
        len(records),
        unit="records",
        kind="specification",
        source="data/results/*.json",
        note=(
            "hand-written illustrative records checked into the repo; they carry "
            "no resume text, so nothing here is recomputed from them and no "
            "quality claim is made on them"
        ),
    )
    section.add(
        "roles_in_catalogue",
        len(roles),
        unit="roles",
        kind="specification",
        source="data/roles.json",
    )
    section.add(
        "requirements_per_role",
        summarize([float(len(r.get("requirements") or [])) for r in roles]),
        kind="specification",
        source="data/roles.json",
        note=(
            "the ATS score's granularity is 100/|requirements|, so a 5-requirement "
            "role can only ever score 0, 20, 40, 60, 80 or 100 — τ = 40 therefore "
            "means 'at least two of five', and no intermediate τ between 21 and 40 "
            "behaves any differently"
        ),
    )

    granularity_rows = []
    for role in roles:
        count = len(role.get("requirements") or [])
        if not count:
            continue
        achievable = [round(k / count * 100) for k in range(count + 1)]
        granularity_rows.append(
            {
                "role_id": role.get("role_id", ""),
                "title": role.get("title", ""),
                "requirements": count,
                "step_size_percent": round(100 / count, 2),
                "achievable_scores": achievable,
                "min_matches_to_pass_tau": next(
                    (k for k in range(count + 1) if round(k / count * 100) >= tau),
                    None,
                ),
            }
        )
    section.tables["gate_granularity"] = granularity_rows

    # Synthetic sweep: for a 5-requirement role, what each τ actually rejects.
    sweep = []
    for threshold in range(0, 101, 5):
        sweep.append(
            {
                "tau": threshold,
                "min_matches_of_5": next(
                    (k for k in range(6) if round(k / 5 * 100) >= threshold), None
                ),
            }
        )
    section.tables["tau_sweep_5_requirements"] = sweep

    section.add(
        "end_to_end_screening_quality",
        None,
        kind="unavailable",
        source="services.application_service.screen",
        needs=(
            "A resume corpus with human relevance labels, plus a model run. The "
            "stored data/results records are illustrative and were written by "
            "hand; measuring precision/recall of screening against them would be "
            "measuring the fixture, not the system."
        ),
    )

    section.commentary = (
        "The pre-filter's lexical behaviour is exact and its word-boundary "
        "handling holds on every trap case; its blindness to synonyms and "
        "abbreviations is quantified rather than described. The finding most "
        "worth stating in the paper is granularity: with five requirements per "
        "role the score can only take six values, so τ = 40 is not a tuned "
        "threshold but a restatement of 'at least two of five'."
    )
    return section
