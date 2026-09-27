"""The router is the security boundary, so it gets tested like one.

``AppTest`` runs ``app.py`` in-process, which also makes this the cheapest smoke
test available: an import error or a typo in any page surfaces as ``at.exception``
rather than as a blank screen ten minutes into a demo.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from core.config import PROJECT_ROOT, settings
from services import auth_service as auth
from services import catalog_service
from ui import session as ui_session

APP_PATH = str(PROJECT_ROOT / "app.py")
PASSWORD = "router-test-1234"


@pytest.fixture(autouse=True)
def _fresh_bootstrap():
    """``bootstrap()`` is cached per process; the DB is recreated per test."""
    ui_session.bootstrap.clear()
    yield
    ui_session.bootstrap.clear()


def _run_as(role: str) -> AppTest:
    company_id = None
    if role == "recruiter":
        catalog_service.upsert_company("testco", "Test Co")
        company_id = "testco"
    email = f"{role}@router.test"
    auth.register(
        email,
        PASSWORD,
        role=role,
        company_id=company_id,
        invite_code=settings.auth.recruiter_invite_code,
    )
    _, token = auth.login(email, PASSWORD)

    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.session_state["_auth_token"] = token
    app.session_state["_cookie_boot"] = True  # skip the cookie handshake rerun
    app.run()
    return app


def test_anonymous_visitor_gets_the_login_page():
    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.session_state["_cookie_boot"] = True
    app.run()

    assert not app.exception
    assert "_pages" not in app.session_state
    assert any("AI hiring, end to end" in block.value for block in app.markdown)


def test_cookie_handshake_shows_a_splash_before_the_login_form():
    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.run()

    assert not app.exception
    assert any("Restoring your session" in block.value for block in app.markdown)


@pytest.mark.parametrize("role", ["candidate", "recruiter", "admin"])
def test_signed_in_roles_render_without_error(role):
    app = _run_as(role)
    assert not app.exception


def test_candidate_cannot_see_admin_pages():
    app = _run_as("candidate")
    assert set(app.session_state["_pages"]) == {
        "home",
        "apply",
        "account",
        "interview_room",
    }


def test_recruiter_cannot_see_admin_pages():
    app = _run_as("recruiter")
    # Nor the candidate's apply screen: a recruiter has no resume to submit.
    assert set(app.session_state["_pages"]) == {
        "home",
        "account",
        "recruiter_pipeline",
        "recruiter_roles",
    }


def test_admin_sees_every_page():
    app = _run_as("admin")
    assert set(app.session_state["_pages"]) == {
        "home",
        "apply",
        "account",
        "interview_room",
        "recruiter_pipeline",
        "recruiter_roles",
        "admin_health",
        "admin_users",
        "admin_sandbox",
    }


def test_signed_in_sidebar_offers_sign_out():
    app = _run_as("candidate")
    labels = [button.label for button in app.sidebar.button]
    assert "Sign out" in labels


# --------------------------------------------------------------------------- #
# Cookie persistence
#
# The cookie is written by a hidden component, so the write only reaches the
# browser from a script run that finishes normally. Sign-in and sign-out both end
# in ``st.rerun()``, which discards their own deltas — hence the queue. These
# tests pin the queue, not the component: they assert the op was issued on the
# run *after* the one that asked for it.
# --------------------------------------------------------------------------- #


def test_sign_in_writes_the_cookie_on_the_run_after_the_form():
    auth.register("cookie@router.test", PASSWORD)

    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.session_state["_cookie_boot"] = True
    app.run()

    app.text_input[0].set_value("cookie@router.test")
    app.text_input[1].set_value(PASSWORD)
    next(b for b in app.button if b.label == "Sign in").click().run()

    assert not app.exception
    assert app.session_state["_auth_token"]
    assert app.session_state["_cookie_ops"] == ["set"]
    assert "_cookie_pending" not in app.session_state


def test_sign_out_deletes_the_cookie_on_the_run_after_the_click():
    app = _run_as("candidate")

    next(b for b in app.sidebar.button if b.label == "Sign out").click().run()

    assert not app.exception
    assert "_auth_token" not in app.session_state
    assert app.session_state["_cookie_ops"] == ["delete"]


def test_a_stale_cookie_lands_on_the_login_page():
    """A revoked or expired token must not strand the visitor on a blank page."""
    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.session_state["_auth_token"] = "not-a-real-token"
    app.session_state["_cookie_boot"] = True
    app.run()

    assert not app.exception
    assert "_pages" not in app.session_state
    assert "_auth_token" not in app.session_state
    assert app.session_state["_cookie_ops"] == ["delete"]
    assert any("AI hiring, end to end" in block.value for block in app.markdown)
