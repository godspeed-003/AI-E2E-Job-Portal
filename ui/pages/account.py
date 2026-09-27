"""Account settings: profile, password, active sessions."""

from __future__ import annotations

import streamlit as st

from core import db
from services import auth_service as auth
from ui import session, theme


def render() -> None:
    theme.inject()
    user = session.require("candidate", "recruiter", "admin")
    theme.page_header("Account", user.email, icon=user.initials)

    left, right = st.columns([1, 1], gap="large")
    with left:
        _profile(user)
        _password(user)
    with right:
        _summary(user)
        _sessions(user)


def _profile(user: auth.User) -> None:
    st.subheader("Profile")
    with st.form("profile", border=False):
        full_name = st.text_input("Full name", value=user.full_name)
        saved = st.form_submit_button("Save", type="primary")
    if saved:
        auth.update_profile(user.id, full_name=full_name)
        session.refresh_user()
        st.success("Saved.")
        st.rerun()


def _password(user: auth.User) -> None:
    st.subheader("Password")
    st.caption("Changing your password signs you out everywhere, including here.")
    with st.form("password", border=False):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password")
    if not submitted:
        return
    if new != confirm:
        st.error("The two new passwords do not match.")
        return
    try:
        auth.change_password(user.id, current, new)
    except auth.AuthError as exc:
        st.error(str(exc))
        return
    session.sign_out()
    st.success("Password updated. Please sign in again.")
    st.rerun()


def _summary(user: auth.User) -> None:
    row = db.query_one(
        "SELECT created_at, last_login_at FROM users WHERE id = ?", (user.id,)
    )
    theme.html_block(
        theme.kv(
            [
                ("Role", user.role.title()),
                ("Company", user.company_id or "—"),
                ("Member since", (row["created_at"] if row else "")[:10] or "—"),
                ("Last sign-in", (row["last_login_at"] if row else None) or "—"),
                ("Account type", "Sandbox" if user.is_sandbox else "Live"),
            ]
        )
    )


def _sessions(user: auth.User) -> None:
    st.subheader("Active sessions")
    rows = db.query(
        """
        SELECT created_at, expires_at, user_agent FROM sessions
        WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
        ORDER BY created_at DESC
        """,
        (user.id, db.utc_now_iso()),
    )
    if not rows:
        theme.empty_state("No other devices", "Only this session is live.")
        return
    theme.html_block(
        theme.timeline(
            [
                (
                    row["created_at"][5:16].replace("T", " "),
                    (row["user_agent"] or "unknown device")[:70],
                    "info",
                )
                for row in rows
            ]
        )
    )
    if st.button(f"Sign out of all {len(rows)} session(s)"):
        auth.logout_all(user.id)
        session.sign_out()
        st.rerun()
