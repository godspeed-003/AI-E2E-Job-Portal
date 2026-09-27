"""The fusion function, checked as arithmetic rather than as a pipeline.

:mod:`core.ranking` is pure, so these tests need no database and no model. They
exist because ``S_final`` is the one number in the system that decides who a
recruiter looks at first, and because the research paper states its properties
as claims — monotonicity, the no-interview identity, and the promise that
proctoring noise can never push a candidate below their resume-only position.
A claim in a paper that no test pins down is a guess.
"""

from __future__ import annotations

import pytest

from core import ranking


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #


def test_resume_component_is_a_fraction_of_the_rubric():
    assert ranking.resume_component(25, 25) == 1.0
    assert ranking.resume_component(15, 25) == 0.6
    assert ranking.resume_component(0, 25) == 0.0


def test_resume_component_survives_a_zero_maximum():
    # A malformed row must not divide by zero and take the shortlist with it.
    assert ranking.resume_component(10, 0) == 0.0


def test_interview_component_is_none_when_unscored():
    assert ranking.interview_component(None, 25) is None
    assert ranking.interview_component(20, 25) == 0.8


def test_integrity_of_a_missing_score_is_full_trust():
    # Proctoring disabled is the operator's choice, not the candidate's fault.
    assert ranking.integrity_component(None) == 1.0
    assert ranking.integrity_component(100) == 1.0
    assert ranking.integrity_component(55) == 0.55
    assert ranking.integrity_component(0) == 0.0


def test_components_clamp_out_of_range_input():
    assert ranking.resume_component(40, 25) == 1.0
    assert ranking.integrity_component(140) == 1.0
    assert ranking.integrity_component(-10) == 0.0


# --------------------------------------------------------------------------- #
# final_score
# --------------------------------------------------------------------------- #


def test_no_interview_leaves_the_resume_score_untouched():
    assert ranking.final_score(s_resume=0.72, s_interview=None) == 0.72


def test_perfect_integrity_gives_the_plain_weighted_mean():
    got = ranking.final_score(
        s_resume=0.4, s_interview=0.8, integrity=1.0, interview_weight=0.5
    )
    assert got == pytest.approx(0.6)


def test_zero_integrity_discards_the_interview_entirely():
    # The candidate is ranked exactly as they were before they sat it.
    for s_interview in (0.0, 0.5, 1.0):
        got = ranking.final_score(
            s_resume=0.4, s_interview=s_interview, integrity=0.0, interview_weight=0.5
        )
        assert got == pytest.approx(0.4)


def test_proctoring_never_pushes_a_candidate_below_resume_only():
    """The property that makes the multiplicative form defensible.

    Whatever the integrity score, a candidate whose interview was at least as
    good as their resume can only move up. That is what stops a flaky webcam
    from reading as a character finding.
    """
    for integrity in (0.0, 0.25, 0.55, 0.8, 1.0):
        got = ranking.final_score(
            s_resume=0.5, s_interview=0.9, integrity=integrity, interview_weight=0.5
        )
        assert got >= 0.5


def test_a_bad_interview_costs_less_when_the_recording_is_untrusted():
    """Low trust shrinks the penalty as well as the reward — symmetric."""
    trusted = ranking.final_score(
        s_resume=0.8, s_interview=0.2, integrity=1.0, interview_weight=0.5
    )
    untrusted = ranking.final_score(
        s_resume=0.8, s_interview=0.2, integrity=0.3, interview_weight=0.5
    )
    assert trusted < untrusted < 0.8


def test_monotone_non_decreasing_in_the_interview_score():
    previous = -1.0
    for step in range(11):
        got = ranking.final_score(
            s_resume=0.5, s_interview=step / 10, integrity=0.9, interview_weight=0.5
        )
        assert got >= previous
        previous = got


def test_monotone_non_decreasing_in_the_resume_score():
    previous = -1.0
    for step in range(11):
        got = ranking.final_score(
            s_resume=step / 10, s_interview=0.5, integrity=0.9, interview_weight=0.5
        )
        assert got >= previous
        previous = got


def test_lambda_zero_is_resume_only_and_lambda_one_is_interview_only():
    resume_only = ranking.final_score(
        s_resume=0.3, s_interview=0.9, integrity=1.0, interview_weight=0.0
    )
    interview_only = ranking.final_score(
        s_resume=0.3, s_interview=0.9, integrity=1.0, interview_weight=1.0
    )
    assert resume_only == pytest.approx(0.3)
    assert interview_only == pytest.approx(0.9)


def test_output_stays_inside_the_unit_interval():
    for s_resume in (0.0, 0.5, 1.0):
        for s_interview in (None, 0.0, 0.5, 1.0):
            for integrity in (0.0, 0.5, 1.0):
                got = ranking.final_score(
                    s_resume=s_resume, s_interview=s_interview, integrity=integrity
                )
                assert 0.0 <= got <= 1.0


# --------------------------------------------------------------------------- #
# Ranking over rows
# --------------------------------------------------------------------------- #


def _row(app_id, llm, total=None, integrity=None, ats=60, name=""):
    return {
        "application_id": app_id,
        "candidate_name": name or f"c{app_id}",
        "llm_score": llm,
        "max_score": 25,
        "ats_score": ats,
        "total_score": total,
        "max_total_score": 25,
        "integrity_score": integrity,
        "integrity_verdict": "",
        "interview_status": "completed" if total is not None else "",
    }


def test_the_interview_can_overturn_the_resume_ordering():
    """The whole point: an over-seller falls, a hidden gem rises."""
    rows = [
        _row(1, llm=23, total=6, integrity=100, name="over-seller"),
        _row(2, llm=12, total=24, integrity=100, name="hidden gem"),
    ]
    resume_only = [c.candidate_name for c in ranking.resume_only_rank(rows)]
    fused = [c.candidate_name for c in ranking.rank(rows)]
    assert resume_only == ["over-seller", "hidden gem"]
    assert fused == ["hidden gem", "over-seller"]


def test_provisional_candidates_are_flagged_but_not_buried():
    rows = [
        _row(1, llm=10, total=10, integrity=100),
        _row(2, llm=24),  # strong resume, interview not sat yet
    ]
    order = ranking.rank(rows)
    assert order[0].application_id == 2
    assert order[0].provisional is True
    assert order[0].s_final == order[0].s_resume
    assert order[1].provisional is False


def test_ties_break_deterministically_on_application_id():
    rows = [_row(3, llm=20, total=20, integrity=100), _row(1, llm=20, total=20, integrity=100)]
    assert [c.application_id for c in ranking.rank(rows)] == [1, 3]
    # And again, to catch any dependence on input order.
    assert [c.application_id for c in ranking.rank(list(reversed(rows)))] == [1, 3]


def test_ties_break_on_ats_score_before_falling_back_to_the_id():
    # Identical fused scores, different keyword pre-filter scores: the ATS score
    # is evidence and the id is not, so it is consulted first. Ordered against
    # the id here so a fallthrough to the id would fail the assertion.
    rows = [
        _row(1, llm=20, total=20, integrity=100, ats=40),
        _row(2, llm=20, total=20, integrity=100, ats=90),
    ]
    assert [c.application_id for c in ranking.rank(rows)] == [2, 1]


def test_zero_integrity_reproduces_the_resume_only_order_exactly():
    # The collapse property at the level of the list, including ties. llm_score
    # is a 0-25 integer so ties are the normal case; without the ats_score key
    # in `rank` the two orderings diverge on every tied pair even though the
    # scores are identical. Audited over a full cohort in
    # `metrics/m5_fusion_audit.py`.
    rows = [
        _row(1, llm=20, total=5, integrity=0, ats=50),
        _row(2, llm=20, total=25, integrity=0, ats=90),
        _row(3, llm=20, total=12, integrity=0, ats=70),
        _row(4, llm=18, total=25, integrity=0, ats=99),
    ]
    fused = ranking.rank(rows)
    assert [c.application_id for c in fused] == [
        c.application_id for c in ranking.resume_only_rank(rows)
    ]
    # And elementwise on the scores, which is where the guarantee is stated.
    for candidate in fused:
        assert candidate.s_final == candidate.s_resume


def test_losing_trust_moves_a_candidate_toward_their_resume_score_only():
    # s_final must stay inside the closed interval between the resume-only score
    # and the fully-trusted fused score, whichever way round they are. This is
    # the precise form of "a flaky webcam is never a penalty" -- it holds for a
    # good interview (which loses its bonus) and a bad one (which loses its
    # drag) alike.
    for total in (2, 25):
        resume_only = ranking.rank([_row(1, llm=15, total=total, integrity=0)])[0].s_final
        trusted = ranking.rank([_row(1, llm=15, total=total, integrity=100)])[0].s_final
        low, high = min(resume_only, trusted), max(resume_only, trusted)
        for integrity in range(0, 101, 10):
            score = ranking.rank([_row(1, llm=15, total=total, integrity=integrity)])[0].s_final
            assert low - 1e-12 <= score <= high + 1e-12


def test_shift_reports_how_far_the_interview_moved_someone():
    rows = [_row(1, llm=10, total=25, integrity=100)]
    candidate = ranking.rank(rows)[0]
    assert candidate.s_resume == pytest.approx(0.4)
    assert candidate.s_final == pytest.approx(0.7)
    assert candidate.shift == pytest.approx(0.3)


def test_as_dict_carries_every_component_for_audit():
    payload = ranking.rank([_row(1, llm=10, total=25, integrity=90)])[0].as_dict()
    for key in (
        "s_resume",
        "s_interview",
        "integrity",
        "s_final",
        "shift",
        "provisional",
    ):
        assert key in payload


def test_rank_positions_is_one_based():
    rows = [_row(1, llm=5, total=5, integrity=100), _row(2, llm=25, total=25, integrity=100)]
    assert ranking.rank_positions(ranking.rank(rows)) == {2: 1, 1: 2}


def test_build_accepts_id_instead_of_application_id():
    # Rows coming straight from `SELECT * FROM applications` use `id`.
    built = ranking.build([{"id": 7, "llm_score": 20, "max_score": 25}])
    assert built[0].application_id == 7


def test_describe_reports_the_parameters_in_force():
    described = ranking.describe()
    assert described["interview_weight_lambda"] == ranking.DEFAULT_INTERVIEW_WEIGHT
    assert "s_final" in described["formula"]
