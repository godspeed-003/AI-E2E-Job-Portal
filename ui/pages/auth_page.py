"""Sign-in and sign-up.

Rendered as a landing page rather than a bare form: the left half explains what
the product does, the right half is the form. It is also the only screen an
unauthenticated visitor can reach, so it carries the demo credentials hint when
a bootstrap admin exists.
"""

from __future__ import annotations

import streamlit as st

from core.config import settings
from services import auth_service as auth
from services import catalog_service
from ui import session, theme


def render() -> None:
    theme.inject()

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        _pitch()
    with right:
        _forms()


def _pitch() -> None:
    theme.hero(
        "AI hiring, end to end",
        "Screen, interview and",
        "verify — automatically",
        "Upload a resume once. The portal scores it against the role, shortlists "
        "the right people, then runs a live video interview with an AI "
        "interviewer that adapts to what the candidate actually says.",
        [
            "Resume + ATS scoring",
            "Tailored question plans",
            "Live video interview",
            "Proctoring evidence",
            "Runs fully offline",
        ],
    )

    roles = catalog_service.list_roles(only_open=True)
    companies = catalog_service.list_companies()
    theme.metric_row(
        [
            theme.metric_tile("Open roles", len(roles), tone="primary"),
            theme.metric_tile("Companies", len(companies), tone="info"),
            theme.metric_tile(
                "AI backend", settings.llm.provider.replace("_", " "), tone="success"
            ),
        ]
    )


def _forms() -> None:
    sign_in, sign_up = st.tabs(["Sign in", "Create account"])
    with sign_in:
        _sign_in_form()
    with sign_up:
        _sign_up_form()


def _sign_in_form() -> None:
    with st.form("sign_in", border=False):
        email = st.text_input("Email", placeholder="you@example.com")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button(
            "Sign in", type="primary", use_container_width=True
        )

    if submitted:
        try:
            user, token = auth.login(email, password, user_agent=_user_agent())
        except auth.AuthError as exc:
            st.error(str(exc))
            remaining = settings.auth.login_max_attempts - auth.recent_failures(email)
            if 0 < remaining <= 3:
                st.caption(f"{remaining} attempt(s) left before a temporary lockout.")
            return
        session.sign_in(user, token)
        st.rerun()

    if settings.auth.admin_password and auth.get_user_by_email(settings.auth.admin_email):
        with st.expander("Demo credentials"):
            st.caption(
                "Set in `.env`. Change `ADMIN_PASSWORD` before putting this on a "
                "network anyone else can reach."
            )
            st.code(
                f"{settings.auth.admin_email}\n{settings.auth.admin_password}",
                language="text",
            )


def _sign_up_form() -> None:
    companies = catalog_service.company_names()

    with st.form("sign_up", border=False):
        full_name = st.text_input("Full name", placeholder="Ada Lovelace")
        email = st.text_input("Email", placeholder="you@example.com", key="su_email")
        password = st.text_input("Password", type="password", key="su_pw")
        confirm = st.text_input("Confirm password", type="password", key="su_pw2")

        account_type = st.radio(
            "Account type",
            ["I am applying for jobs", "I am hiring"],
            horizontal=False,
        )
        hiring = account_type == "I am hiring"

        company_id: str | None = None
        invite_code: str | None = None
        if hiring:
            if companies:
                company_id = st.selectbox(
                    "Company",
                    options=list(companies.keys()),
                    format_func=lambda cid: companies[cid],
                )
            else:
                st.warning("No companies exist yet. Ask an admin to create one first.")
            invite_code = st.text_input(
                "Invite code",
                type="password",
                help="Hiring accounts can read every applicant's resume and "
                "interview recording, so they are invite-only.",
            )

        submitted = st.form_submit_button(
            "Create account", type="primary", use_container_width=True
        )

    if not submitted:
        return
    if password != confirm:
        st.error("The two passwords do not match.")
        return

    try:
        user = auth.register(
            email,
            password,
            full_name=full_name,
            role="recruiter" if hiring else "candidate",
            company_id=company_id,
            invite_code=invite_code,
        )
        _, token = auth.login(email, password, user_agent=_user_agent())
    except auth.AuthError as exc:
        st.error(str(exc))
        return

    session.sign_in(user, token)
    st.rerun()


def _user_agent() -> str:
    """Best effort; only used to label sessions in the admin view."""
    try:
        return str(st.context.headers.get("User-Agent", ""))
    except Exception:
        return ""
