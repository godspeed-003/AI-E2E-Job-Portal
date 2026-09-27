"""Streamlit entry point: bootstrap, then role-aware navigation.

Streamlit reruns this file top to bottom on every interaction, so it stays
cheap: process-wide setup hides behind ``session.bootstrap()``'s cache and every
screen is a plain ``render()`` function under ``ui/pages``.

Navigation is declarative — one row per page in :data:`NAV`. Adding a screen is
one line, and a page is only built for a visitor who holds one of its roles. The
page's own ``session.require`` checks again, so a bookmarked URL is not a way in.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import streamlit as st

from core.config import settings
from services import auth_service as auth
from ui import session, theme
from ui.pages import (
    account,
    admin_health,
    admin_sandbox,
    admin_users,
    apply,
    auth_page,
    home,
    interview_room,
    recruiter_pipeline,
    recruiter_roles,
)

st.set_page_config(
    page_title="AI Hiring Portal",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_EVERYONE = ("candidate", "recruiter", "admin")

# key, sidebar section, title, icon, roles allowed, render function
NAV: tuple[tuple[str, str, str, str, tuple[str, ...], Callable[[], None]], ...] = (
    ("home", "Portal", "Home", ":material/home:", _EVERYONE, home.render),
    (
        "apply",
        "Portal",
        "Apply",
        ":material/upload_file:",
        ("candidate", "admin"),
        apply.render,
    ),
    ("account", "Portal", "Account", ":material/settings:", _EVERYONE, account.render),
    (
        "interview_room",
        "Portal",
        "Interview",
        ":material/videocam:",
        ("candidate", "admin"),
        interview_room.render,
    ),
    (
        "recruiter_pipeline",
        "Hiring",
        "Pipeline",
        ":material/leaderboard:",
        ("recruiter", "admin"),
        recruiter_pipeline.render,
    ),
    (
        "recruiter_roles",
        "Hiring",
        "Roles",
        ":material/work:",
        ("recruiter", "admin"),
        recruiter_roles.render,
    ),
    (
        "admin_health",
        "Admin",
        "System health",
        ":material/monitor_heart:",
        ("admin",),
        admin_health.render,
    ),
    ("admin_users", "Admin", "People", ":material/group:", ("admin",), admin_users.render),
    (
        "admin_sandbox",
        "Admin",
        "Sandbox",
        ":material/science:",
        ("admin",),
        admin_sandbox.render,
    ),
)


def main() -> None:
    session.bootstrap()
    # First, before anything can read or write a cookie: mounts the cookie
    # component for this run and flushes a write queued by the previous one.
    session.start_run()
    theme.inject()

    user = session.restore_session()
    if user is None:
        theme.hide_sidebar()
        auth_page.render()
        return

    pages: dict[str, Any] = {}
    sections: dict[str, list[Any]] = {}
    for key, section, title, icon, roles, view in NAV:
        if not auth.has_role(user, *roles):
            continue
        page = st.Page(
            view, title=title, icon=icon, url_path=key, default=(key == "home")
        )
        pages[key] = page
        sections.setdefault(section, []).append(page)

    # Pages that want to link elsewhere read this rather than importing app.py.
    st.session_state["_pages"] = pages

    _sidebar(user)
    st.navigation(sections).run()


def _sidebar(user: auth.User) -> None:
    with st.sidebar:
        theme.html_block(
            '<div class="p-card" style="margin-bottom:.7rem;padding:.7rem .8rem">'
            '<div style="display:flex;align-items:center;gap:.6rem">'
            '<div style="width:34px;height:34px;flex:0 0 34px;border-radius:10px;'
            "display:grid;place-items:center;font-weight:700;font-size:.82rem;"
            "background:linear-gradient(135deg,rgba(99,102,241,.28),rgba(124,92,255,.12));"
            'border:1px solid rgba(139,147,255,.3)">'
            f"{theme.esc(user.initials)}</div>"
            '<div style="min-width:0">'
            '<div style="font-weight:620;font-size:.9rem;white-space:nowrap;'
            'overflow:hidden;text-overflow:ellipsis">'
            f"{theme.esc(user.display_name or user.email)}</div>"
            f'<div style="color:var(--muted);font-size:.76rem">'
            f"{theme.esc(user.role.title())}</div>"
            "</div></div></div>"
        )
        if session.sandbox_view():
            theme.html_block(theme.pill("Sandbox data", "warning"))

        if st.button("Sign out", use_container_width=True):
            session.sign_out()
            st.rerun()

        st.caption(
            f"AI backend: {settings.llm.provider.replace('_', ' ')} · "
            f"proctoring {'on' if settings.proctoring.enabled else 'off'}"
        )


main()
