"""Phase 6 — the recruiter's half: company scoping, ranking, and the two pages.

The access-control tests here are the reason this file exists. Holding the
``recruiter`` role is not authorization: a recruiter belongs to one company, and
until :mod:`services.access` existed nothing narrowed a lookup to it. An
application id is a small integer in a URL, so "can recruiter B read company A's
candidate" is not a hypothetical — it is the first thing anyone would try.

Everything runs offline against the fake provider, like the rest of the suite.
The page tests render the page function directly rather than going through
``app.py``: the router builds pages with ``st.navigation``, and ``AppTest``'s
``switch_page`` only handles file-based pages, so a deep link would silently
render the home page and the assertions would pass against the wrong screen.
"""

from __future__ import annotations

import pytest

from core import db
from core import resume as resume_core
from services import access
from services import application_service as apps
from services import auth_service as auth
from services import catalog_service as catalog
from services import interview_service as interviews
from services import proctor_service as proctor
from core.config import settings
from proctoring.rules import ProctoringEvent

from test_interview import PASSWORD, RESUME, _answer_current, _shortlisted

RECRUITER_PASSWORD = "recruiter-pass-1234"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def _recruiter(email: str, company_id: str) -> auth.User:
    catalog.upsert_company(company_id, company_id.replace("_", " ").title())
    return auth.register(
        email,
        RECRUITER_PASSWORD,
        role="recruiter",
        company_id=company_id,
        invite_code=settings.auth.recruiter_invite_code,
    )


def _admin(email: str = "admin@rec.test") -> auth.User:
    return auth.register(
        email,
        RECRUITER_PASSWORD,
        role="admin",
        invite_code=settings.auth.recruiter_invite_code,
    )


@pytest.fixture
def zeta(fake_llm):
    """A shortlisted candidate at Zeta, plus a recruiter who works there."""
    user, application, interview = _shortlisted()
    return _recruiter("hr@zeta.test", "zeta"), user, application, interview


@pytest.fixture
def rival():
    """A recruiter at a company that has nothing to do with Zeta."""
    catalog.upsert_company("acme", "Acme Corp")
    # Deliberately a different title from Zeta's role: the roles page shows
    # titles, so two roles called "Backend Engineer" would make the leak test
    # unable to tell whose role it was looking at.
    catalog.upsert_role("acme-platform", "acme", "Platform Engineer")
    return _recruiter("hr@acme.test", "acme")


# --------------------------------------------------------------------------- #
# Access control — a recruiter is confined to their own company
# --------------------------------------------------------------------------- #


def test_a_recruiter_reaches_their_own_companys_role(zeta):
    recruiter, _user, _application, _interview = zeta
    assert access.role(recruiter, "zeta-backend").company_id == "zeta"


def test_a_recruiter_cannot_reach_another_companys_role(zeta, rival):
    with pytest.raises(access.AccessError):
        access.role(rival, "zeta-backend")


def test_a_recruiter_cannot_read_another_companys_application(zeta, rival):
    _recruiter_at_zeta, _user, application, _interview = zeta
    with pytest.raises(access.AccessError):
        access.application(rival, application.id)


def test_a_recruiter_cannot_read_another_companys_interview(zeta, rival):
    _recruiter_at_zeta, _user, _application, interview = zeta
    with pytest.raises(access.AccessError):
        access.interview(rival, interview.id)


def test_a_forbidden_record_is_indistinguishable_from_a_missing_one(zeta, rival):
    """Different messages would let a rival count a competitor's candidates."""
    _r, _u, application, _i = zeta
    with pytest.raises(access.AccessError) as forbidden:
        access.application(rival, application.id)
    with pytest.raises(access.AccessError) as missing:
        access.application(rival, 999_999)
    assert str(forbidden.value) == str(missing.value)


def test_an_admin_reaches_every_company(zeta):
    _recruiter, _user, application, interview = zeta
    admin = _admin()
    assert access.reach(admin) is None
    assert access.application(admin, application.id).id == application.id
    assert access.interview(admin, interview.id).id == interview.id


def test_a_candidate_reaches_no_hiring_data(zeta):
    _recruiter, candidate, application, _interview = zeta
    with pytest.raises(access.AccessError):
        access.reach(candidate)
    # Even their own application: candidates read theirs through
    # application_service.for_user, which checks ownership rather than company.
    with pytest.raises(access.AccessError):
        access.application(candidate, application.id)
    assert access.can_reach_company(candidate, "zeta") is False


def test_a_recruiter_with_no_company_reaches_nothing(zeta):
    """A legacy row, not something register() can produce any more.

    The failure mode worth pinning is the other one: treating "no company" as
    "no restriction" would hand a half-configured account every company at once.
    """
    _r, _u, application, _i = zeta
    stray = auth.register("stray@nowhere.test", RECRUITER_PASSWORD)
    db.execute(
        "UPDATE users SET role = 'recruiter', company_id = NULL WHERE id = ?",
        (stray.id,),
    )
    reloaded = auth.get_user(stray.id)
    assert reloaded is not None and reloaded.role == "recruiter"

    with pytest.raises(access.AccessError):
        access.reach(reloaded)
    with pytest.raises(access.AccessError):
        access.application(reloaded, application.id)


def test_visible_roles_is_narrowed_to_the_recruiters_company(zeta, rival):
    recruiter, _u, _a, _i = zeta
    assert [role.id for role in access.visible_roles(recruiter)] == ["zeta-backend"]
    assert [role.id for role in access.visible_roles(rival)] == ["acme-platform"]
    assert {role.id for role in access.visible_roles(_admin())} == {
        "zeta-backend",
        "acme-platform",
    }


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def test_candidates_come_back_best_first(fake_llm):
    _shortlisted("first@example.com")
    weaker = auth.register("second@example.com", PASSWORD, full_name="Sam Weak")
    apps.submit(weaker, "zeta-backend", resume_core.from_text(RESUME))

    ranked = apps.ranked_for_role("zeta-backend")
    assert len(ranked) == 2
    scores = [application.llm_score for application in ranked]
    assert scores == sorted(scores, reverse=True)


def test_a_tie_is_broken_by_keyword_score_then_arrival(fake_llm):
    """Without a total order the shortlist reorders itself between page loads."""
    _shortlisted("a@example.com")
    second = auth.register("b@example.com", PASSWORD, full_name="B")
    apps.submit(second, "zeta-backend", resume_core.from_text(RESUME))

    first_pass = [application.id for application in apps.ranked_for_role("zeta-backend")]
    second_pass = [application.id for application in apps.ranked_for_role("zeta-backend")]
    assert first_pass == second_pass


def test_sandbox_candidates_stay_off_the_real_ranking(fake_llm):
    _shortlisted("real@example.com")
    persona = auth.register("persona@example.com", PASSWORD, full_name="Test Persona")
    db.execute("UPDATE users SET is_sandbox = 1 WHERE id = ?", (persona.id,))
    apps.apply(
        auth.get_user(persona.id),  # type: ignore[arg-type]
        "zeta-backend",
        resume_core.from_text(RESUME),
    )

    assert len(apps.ranked_for_role("zeta-backend")) == 1
    assert len(apps.ranked_for_role("zeta-backend", include_sandbox=True)) == 2


# --------------------------------------------------------------------------- #
# The pipeline page
# --------------------------------------------------------------------------- #


def _pipeline_script() -> None:
    from ui.pages import recruiter_pipeline

    recruiter_pipeline.render()


def _roles_script() -> None:
    from ui.pages import recruiter_roles

    recruiter_roles.render()


def _page(script, email: str, password: str = RECRUITER_PASSWORD, **state):
    from streamlit.testing.v1 import AppTest

    _, token = auth.login(email, password)
    app = AppTest.from_function(script, default_timeout=60)
    app.session_state["_auth_token"] = token
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    return app


def _text(app) -> str:
    """Everything the page rendered, as one searchable string."""
    parts = [block.value for block in app.markdown]
    parts += [block.value for block in app.caption]
    parts += [block.value for block in app.info]
    parts += [block.value for block in app.warning]
    parts += [block.value for block in app.error]
    parts += [block.value for block in app.success]
    return "\n".join(str(part) for part in parts)


def test_the_pipeline_lists_the_companys_candidates(zeta):
    _recruiter, _user, _application, _interview = zeta
    app = _page(_pipeline_script, "hr@zeta.test")

    assert not app.exception
    rendered = _text(app)
    assert "Pipeline" in rendered
    assert "Priya Raman" in rendered


def test_the_pipeline_shows_a_rival_nothing(zeta, rival):
    app = _page(_pipeline_script, "hr@acme.test")

    assert not app.exception
    rendered = _text(app)
    assert "Priya Raman" not in rendered
    # Acme's own role exists but has no applicants.
    assert "No applications yet" in rendered


def test_a_candidate_is_stopped_at_the_pipeline_door(zeta):
    _recruiter, _user, _application, _interview = zeta
    app = _page(_pipeline_script, "priya@example.com", password=PASSWORD)

    assert not app.exception
    assert any("do not have access" in str(block.value) for block in app.error)
    assert "Priya Raman" not in _text(app)


def test_opening_a_candidate_shows_the_transcript(zeta):
    _recruiter, user, application, interview = zeta
    interviews.start(interview.id, user_id=user.id)
    _answer_current(interview, user)

    app = _page(
        _pipeline_script,
        "hr@zeta.test",
        _rec_role_id="zeta-backend",
        _rec_application_id=application.id,
    )

    assert not app.exception
    rendered = _text(app)
    assert "Transcript" in rendered
    assert "reconciliation service" in rendered  # the answer that was given


def test_the_integrity_panel_reports_the_stored_events(zeta):
    _recruiter, user, application, interview = zeta
    interviews.start(interview.id, user_id=user.id)
    proctor.record_event(
        interview.id,
        ProctoringEvent(kind="multiple_faces", severity="high", confidence=1.0),
        elapsed_seconds=42.0,
    )
    score, verdict = proctor.finalize(interview.id)
    assert verdict in ("clean", "review", "flag")

    app = _page(
        _pipeline_script,
        "hr@zeta.test",
        _rec_role_id="zeta-backend",
        _rec_application_id=application.id,
    )

    assert not app.exception
    rendered = _text(app)
    assert "More than one face" in rendered
    assert "advisory evidence" in rendered.lower()


def test_an_unscreened_application_says_so_rather_than_reading_as_a_zero(fake_llm):
    """An ATS rejection writes 0/25 with no model involved. Saying which is which
    is the difference between "we didn't look" and "we looked and said no"."""
    catalog.upsert_company("zeta", "Zeta Payments")
    catalog.upsert_role(
        "zeta-backend", "zeta", "Backend Engineer",
        requirements=["Rust", "Erlang", "Haskell", "OCaml"],
        ats_reject_below=90,
    )
    recruiter = _recruiter("hr@zeta.test", "zeta")
    candidate = auth.register("nomatch@example.com", PASSWORD, full_name="No Match")
    application = apps.submit(candidate, "zeta-backend", resume_core.from_text(RESUME))
    assert application.is_rejected and not application.screened

    app = _page(
        _pipeline_script,
        "hr@zeta.test",
        _rec_role_id="zeta-backend",
        _rec_application_id=application.id,
    )

    assert not app.exception
    assert "not screened" in _text(app)


# --------------------------------------------------------------------------- #
# The roles page
# --------------------------------------------------------------------------- #


def test_the_roles_page_lists_only_the_recruiters_roles(zeta, rival):
    app = _page(_roles_script, "hr@zeta.test")

    assert not app.exception
    # ``options`` comes back formatted, so these are titles rather than ids.
    options = app.selectbox[0].options
    assert "Backend Engineer" in options
    assert "Platform Engineer" not in options


def test_creating_a_role_stores_it_against_the_recruiters_company(zeta):
    from ui.pages import recruiter_roles

    recruiter, _u, _a, _i = zeta
    recruiter_roles._save(
        recruiter,
        None,
        company_id="zeta",
        title="Staff Data Engineer",
        job_description="Own the warehouse.",
        requirements_raw="Python\nSQL\n\n  dbt  \n",
        is_open=True,
        overrides={
            "ats_reject_below": None,
            "shortlist_llm_score_min": 20,
            "planned_questions": None,
            "interview_duration_minutes": None,
            "interview_window_days": None,
        },
    )

    created = catalog.get_role("zeta_staff_data_engineer")
    assert created is not None
    assert created.company_id == "zeta"
    assert created.requirements == ["Python", "SQL", "dbt"]  # trimmed, blanks dropped
    assert created.shortlist_llm_score_min == 20
    # Left on "default": stored as NULL so the role follows the global setting
    # rather than freezing today's value into the row.
    assert created.ats_reject_below is None
    assert created.ats_floor == settings.screening.ats_reject_below


def test_a_recruiter_cannot_create_a_role_for_another_company(zeta, rival):
    from ui.pages import recruiter_roles

    recruiter_roles._save(
        rival,
        None,
        company_id="zeta",
        title="Trojan Role",
        job_description="",
        requirements_raw="",
        is_open=True,
        overrides={},
    )
    assert catalog.get_role("zeta_trojan_role") is None


def test_a_new_role_never_reuses_an_existing_id(zeta):
    from ui.pages import recruiter_roles

    catalog.upsert_role("zeta_backend_engineer", "zeta", "Backend Engineer")
    assert recruiter_roles._unique_id("zeta", "Backend Engineer") == "zeta_backend_engineer_2"


def test_closing_a_role_hides_it_from_applicants_but_keeps_its_candidates(zeta):
    _recruiter, _user, application, _interview = zeta
    catalog.set_role_open("zeta-backend", False)

    assert "zeta-backend" not in {r.id for r in catalog.list_roles(only_open=True)}
    assert application.id in {a.id for a in apps.ranked_for_role("zeta-backend")}
