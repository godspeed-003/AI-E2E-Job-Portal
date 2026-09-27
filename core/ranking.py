"""Fuse the three evidence streams into one final score.

Until this module existed the recruiter shortlist was ordered by
``llm_score DESC, ats_score DESC`` — resume evidence only. The interview was
conducted, scored and stored, and then had no effect on anyone's position in
the list. That is a real gap, not a stylistic one: the whole argument for
running an interview is that a resume is a *claim* and an interview is
*evidence*, and a system that never lets the evidence move the ranking cannot
tell you whether the interview was worth doing.

:func:`final_score` is that fusion, written as one pure function so it can be
read, tested and cited without starting the app.

The shape of it
---------------

Three inputs, all normalised to ``[0, 1]``:

``s_resume``
    ``llm_score / max_score`` — the graded screening rubric (25 points over five
    criteria). The keyword ATS score is deliberately *not* averaged in here: it
    is a gate applied before screening (``ats_reject_below``), and a keyword
    count is not a second opinion about competence.

``s_interview``
    ``total_score / max_total_score`` — the interview rubric (also 25 points
    over five criteria, a different five; see
    :mod:`services.interview_service`).

``integrity``
    ``integrity_score / 100`` from :func:`proctoring.rules.compute_integrity_score`.

and one weight, ``interview_weight`` (λ, default 0.5 — equal footing).

    trust    = integrity
    evidence = trust · s_interview + (1 − trust) · s_resume
    s_final  = (1 − λ) · s_resume + λ · evidence

Why integrity multiplies instead of subtracting
-----------------------------------------------

The obvious move is ``s_final = … − penalty(integrity)``. It is wrong, and
wrong in a way that hurts candidates. A poor integrity score does not mean the
candidate is less competent; it means *we are less able to believe this
recording*. Subtracting treats a flaky webcam as a character finding. What the
arithmetic above does instead is shrink the interview's influence toward the
resume prior in proportion to how much of the recording we can trust: at
``integrity = 1`` the interview counts fully, at ``integrity = 0`` the interview
is discarded and ``s_final == s_resume`` exactly — the candidate's score is
returned to what it was before they sat the interview.

The precise guarantee, stated at the level it holds, is about *scores*:
``c(E)`` interpolates between ``s_resume`` and the fully-trusted fused value, so
``s_final`` stays inside that closed interval for every ``c(E)``. Degrading the
integrity score can only move a candidate *toward* their resume score, never
past it, so proctoring noise can never subtract from what the resume already
earned. It is deliberately not a claim about *positions*: a candidate's place in
the list can still change when the candidates around them are re-scored, and no
fusion function can promise otherwise. Both forms are measured in
``metrics/m5_fusion_audit.py`` — the score guarantee holds exactly, and the
residual positional difference against the shipped baseline is reported
separately as a tiebreak artefact.

No verdict in this module is ever a finding of misconduct — that judgement
belongs to the recruiter looking at the flagged recording.

Candidates with no completed interview keep ``s_final == s_resume`` and are
marked ``provisional``. They are not sorted last: an unfinished interview is
missing evidence, not bad evidence, and burying them would quietly punish
whoever the scheduler reached last.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import settings

# Interview weight λ. 0.5 puts the interview and the resume on equal footing,
# which is the claim the pipeline is making; the metrics harness sweeps it from
# 0.0 to 1.0 so the sensitivity of the ranking to this one number is reported
# rather than assumed.
DEFAULT_INTERVIEW_WEIGHT = 0.5


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


def resume_component(llm_score: int | float, max_score: int | float) -> float:
    """``S_resume`` — the screening rubric as a fraction of its maximum."""
    if not max_score or max_score <= 0:
        return 0.0
    return _clamp01(llm_score / max_score)


def interview_component(
    total_score: int | float | None, max_total_score: int | float
) -> float | None:
    """``S_interview``, or ``None`` when the interview has not been scored."""
    if total_score is None:
        return None
    if not max_total_score or max_total_score <= 0:
        return None
    return _clamp01(total_score / max_total_score)


def integrity_component(integrity_score: int | float | None) -> float:
    """``c(E)`` — trust in the recording, in ``[0, 1]``.

    An interview with no proctoring events recorded at all yields ``1.0``: the
    absence of evidence against is not evidence against. A missing integrity
    score (proctoring disabled) also yields ``1.0``, because refusing to trust
    an interview the operator chose not to proctor would penalise the candidate
    for the deployment's configuration.
    """
    if integrity_score is None:
        return 1.0
    return _clamp01(integrity_score / 100.0)


def final_score(
    *,
    s_resume: float,
    s_interview: float | None,
    integrity: float = 1.0,
    interview_weight: float | None = None,
) -> float:
    """``h(S_resume, S_interview, c(E))`` → ``S_final`` in ``[0, 1]``.

    Pure, total, and monotone non-decreasing in every input. Returns
    ``s_resume`` unchanged when there is no interview score.
    """
    lam = (
        DEFAULT_INTERVIEW_WEIGHT if interview_weight is None else _clamp01(interview_weight)
    )
    s_resume = _clamp01(s_resume)
    if s_interview is None:
        return s_resume
    trust = _clamp01(integrity)
    evidence = trust * _clamp01(s_interview) + (1.0 - trust) * s_resume
    return _clamp01((1.0 - lam) * s_resume + lam * evidence)


@dataclass(frozen=True)
class RankedCandidate:
    """One row of a fused shortlist, carrying its own arithmetic.

    Every component is kept alongside the result on purpose. A recruiter who
    cannot see *why* a candidate moved has been handed a black box, and a
    reviewer reading the paper cannot check the fusion from a single column.
    """

    application_id: int
    candidate_name: str
    s_resume: float
    s_interview: float | None
    integrity: float
    s_final: float
    provisional: bool
    integrity_verdict: str = ""
    llm_score: int = 0
    ats_score: int = 0
    interview_status: str = ""

    @property
    def shift(self) -> float:
        """How far the interview moved this candidate, in score units."""
        return self.s_final - self.s_resume

    def as_dict(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "candidate_name": self.candidate_name,
            "s_resume": round(self.s_resume, 6),
            "s_interview": None if self.s_interview is None else round(self.s_interview, 6),
            "integrity": round(self.integrity, 6),
            "s_final": round(self.s_final, 6),
            "shift": round(self.shift, 6),
            "provisional": self.provisional,
            "integrity_verdict": self.integrity_verdict,
            "llm_score": self.llm_score,
            "ats_score": self.ats_score,
            "interview_status": self.interview_status,
        }


def build(
    rows: list[dict],
    *,
    interview_weight: float | None = None,
    llm_max_default: int = 25,
    interview_max_default: int = 25,
) -> list[RankedCandidate]:
    """Turn joined application+interview dicts into scored candidates.

    ``rows`` need only the keys this reads, so the metrics harness can feed it
    plain dicts and the service layer can feed it ``sqlite3.Row`` objects
    converted by :func:`core.db.row_to_dict` — no import of the service layer
    from here, which keeps the fusion testable without a database.
    """
    built: list[RankedCandidate] = []
    for row in rows:
        max_score = row.get("max_score") or llm_max_default
        s_resume = resume_component(row.get("llm_score") or 0, max_score)
        s_interview = interview_component(
            row.get("total_score"),
            row.get("max_total_score") or interview_max_default,
        )
        integrity = integrity_component(row.get("integrity_score"))
        built.append(
            RankedCandidate(
                application_id=int(row.get("application_id") or row.get("id") or 0),
                candidate_name=str(row.get("candidate_name") or ""),
                s_resume=s_resume,
                s_interview=s_interview,
                integrity=integrity,
                s_final=final_score(
                    s_resume=s_resume,
                    s_interview=s_interview,
                    integrity=integrity,
                    interview_weight=interview_weight,
                ),
                provisional=s_interview is None,
                integrity_verdict=str(row.get("integrity_verdict") or ""),
                llm_score=int(row.get("llm_score") or 0),
                ats_score=int(row.get("ats_score") or 0),
                interview_status=str(row.get("interview_status") or ""),
            )
        )
    return built


def rank(
    rows: list[dict], *, interview_weight: float | None = None
) -> list[RankedCandidate]:
    """Best first, with deterministic tiebreaks.

    ``s_final``, then ``s_resume``, then ``ats_score``, then ``application_id``.
    The id tiebreak is what stops the shortlist from reshuffling itself between
    page loads when two candidates score identically — the same reason
    :func:`services.application_service.ranked_for_role` breaks ties on
    ``created_at``.

    ``ats_score`` sits in front of the id so that ties are resolved on evidence
    before they are resolved arbitrarily, and so that this ordering matches
    :func:`resume_only_rank` exactly when ``c(E) = 0``. That matters more than it
    looks: ``llm_score`` is a 0–25 integer, so in a cohort of any size most
    candidates share their score with someone, and without this key the two
    rankings differ on ties even where every fused score is identical to its
    resume-only counterpart. ``metrics/m5_fusion_audit.py`` measures the
    divergence — it was 83 positions in a 100-candidate cohort before this key
    was added.
    """
    return sorted(
        build(rows, interview_weight=interview_weight),
        key=lambda c: (-c.s_final, -c.s_resume, -c.ats_score, c.application_id),
    )


def rank_positions(ranked: list[RankedCandidate]) -> dict[int, int]:
    """``application_id`` → 1-based position, for measuring rank movement."""
    return {c.application_id: i + 1 for i, c in enumerate(ranked)}


def resume_only_rank(rows: list[dict]) -> list[RankedCandidate]:
    """The pre-fusion ordering, for comparison.

    Mirrors ``ORDER BY llm_score DESC, ats_score DESC`` — the ordering the
    portal shipped with — so a paper can report the two side by side rather
    than asserting an improvement over an unstated baseline.
    """
    return sorted(
        build(rows),
        key=lambda c: (-c.llm_score, -c.ats_score, c.application_id),
    )


def describe() -> dict[str, object]:
    """The parameters actually in force — for the provenance record."""
    return {
        "interview_weight_lambda": DEFAULT_INTERVIEW_WEIGHT,
        "llm_max_score": settings.screening.llm_max_score,
        "integrity_fail_below": settings.proctoring.integrity_fail_below,
        "formula": (
            "s_final = (1-L)*s_resume + L*(c*s_interview + (1-c)*s_resume), "
            "c = integrity/100, L = interview_weight"
        ),
    }
