"""The landing page after sign-in — one file, three audiences.

A candidate sees their applications and what to do next; a recruiter sees their
roles and how many people are waiting on them; an admin sees the state of the
whole system. Keeping them together means the three views cannot drift apart in
layout or vocabulary.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from core import db
from services import application_service
from services import auth_service as auth
from services import catalog_service
from ui import session, theme


def render() -> None:
    theme.inject()
    user = session.require("candidate", "recruiter", "admin")
    if session.sandbox_view():
        theme.sandbox_banner()

    if user.role == "admin":
        _admin_home(user)
    elif user.role == "recruiter":
        _recruiter_home(user)
    else:
        _candidate_home(user)


# --------------------------------------------------------------------------- #
# Candidate
# --------------------------------------------------------------------------- #


def _candidate_home(user: auth.User) -> None:
    theme.page_header(
        f"Hello, {user.display_name.split()[0] if user.display_name else 'there'}",
        "Your applications and everything waiting on you.",
        icon=user.initials,
    )
    rows = db.query(
        """
        SELECT a.id, a.role_id, a.status, a.llm_score, a.max_score, a.created_at,
               r.title AS role_title, c.name AS company_name,
               i.status AS interview_status, i.closes_at
        FROM applications a
        JOIN roles r ON r.id = a.role_id
        JOIN companies c ON c.id = r.company_id
        LEFT JOIN interviews i ON i.application_id = a.id
        WHERE a.user_id = ?
        ORDER BY a.created_at DESC
        """,
        (user.id,),
    )
    shortlisted = sum(1 for row in rows if row["status"] == "shortlisted")
    awaiting = sum(1 for row in rows if row["interview_status"] == "pending")
    theme.metric_row(
        [
            theme.metric_tile("Applications", len(rows), tone="primary"),
            theme.metric_tile("Shortlisted", shortlisted, tone="success"),
            theme.metric_tile("Interviews to sit", awaiting, tone="warning"),
        ]
    )
    st.write("")

    st.markdown("##### Your applications")
    if not rows:
        theme.empty_state(
            "No applications yet",
            "Pick a role below and upload your resume to get scored.",
            icon="📄",
        )
    for row in rows:
        _application_card(row)

    st.divider()
    _open_roles(user)


def _application_card(row: Any) -> None:
    badge = theme.status_pill(row["status"])
    if row["interview_status"]:
        badge += " " + theme.status_pill(row["interview_status"])
    detail = f"Applied {row['created_at'][:10]}"
    if row["llm_score"]:
        detail += f" · fit {row['llm_score']}/{row['max_score']}"
    if row["interview_status"] == "pending" and row["closes_at"]:
        detail += f" · interview window closes {row['closes_at'][:10]}"
    card, action = st.columns([4, 1], gap="small")
    with card:
        theme.html_block(
            theme.card(
                row["role_title"],
                subtitle=f"{row['company_name']} · {detail}",
                badge=badge,
                hover=True,
            )
        )
    with action:
        # If shortlisted with a pending interview, take them straight to the room.
        if row["interview_status"] in ("pending", "in_progress"):
            if st.button(
                "Interview",
                key=f"home_open_{row['id']}",
                use_container_width=True,
                type="primary",
            ):
                _go_interview(row["id"])
        else:
            if st.button(
                "Details",
                key=f"home_open_{row['id']}",
                use_container_width=True,
            ):
                _go_apply(row["role_id"])


def _open_roles(user: auth.User | None = None, limit: int = 6) -> None:
    st.markdown("##### Open roles")
    companies = catalog_service.company_names()
    roles = catalog_service.list_roles(only_open=True)[:limit]
    if not roles:
        theme.empty_state("Nothing open right now", "Check back later.", icon="🗓")
        return
    applied = application_service.applied_role_ids(user.id) if user else set()
    for role in roles:
        card, action = st.columns([4, 1], gap="small")
        with card:
            theme.html_block(
                theme.card(
                    role.title,
                    subtitle=companies.get(role.company_id, role.company_id),
                    badge=theme.pill(f"{role.question_budget} questions", "info"),
                    body=theme.esc(role.requirements_line),
                    hover=True,
                )
            )
        if user is None:
            continue
        with action:
            done = role.id in applied
            if st.button(
                "View" if done else "Apply",
                key=f"home_apply_{role.id}",
                use_container_width=True,
                type="secondary" if done else "primary",
            ):
                _go_apply(role.id)


def _go_interview(application_id: int) -> None:
    """Deep-link to the interview room for this application."""
    from services import interview_service as interviews

    interview = interviews.for_application(application_id)
    if interview is None:
        return
    st.query_params["interview_id"] = str(interview.id)
    page = st.session_state.get("_pages", {}).get("interview_room")
    if page is not None:
        st.switch_page(page)


def _go_apply(role_id: str) -> None:
    """Open the apply screen with this role already chosen.

    The key matches the ``st.selectbox`` on that page, so writing it here *is*
    the deep link — Streamlit seeds a keyed widget from session state.
    """
    st.session_state["apply_role_id"] = role_id
    page = st.session_state.get("_pages", {}).get("apply")
    if page is not None:
        st.switch_page(page)


# --------------------------------------------------------------------------- #
# Recruiter
# --------------------------------------------------------------------------- #


def _recruiter_home(user: auth.User) -> None:
    company = catalog_service.get_company(user.company_id or "")
    theme.page_header(
        company.name if company else "Hiring",
        "Your roles and the people waiting on a decision.",
        icon="🏢",
    )
    rows = db.query(
        """
        SELECT r.id, r.title, r.is_open,
               (SELECT COUNT(*) FROM applications a
                  WHERE a.role_id = r.id AND a.is_sandbox = 0) AS applicants,
               (SELECT COUNT(*) FROM applications a
                  WHERE a.role_id = r.id AND a.status = 'shortlisted'
                    AND a.is_sandbox = 0) AS shortlisted,
               (SELECT COUNT(*) FROM interviews i
                  JOIN applications a ON a.id = i.application_id
                  WHERE a.role_id = r.id AND i.status = 'completed'
                    AND i.is_sandbox = 0) AS interviewed
        FROM roles r
        WHERE r.company_id = ?
        ORDER BY r.is_open DESC, r.title
        """,
        (user.company_id or "",),
    )
    theme.metric_row(
        [
            theme.metric_tile(
                "Open roles", sum(1 for row in rows if row["is_open"]), tone="primary"
            ),
            theme.metric_tile(
                "Applicants", sum(row["applicants"] for row in rows), tone="info"
            ),
            theme.metric_tile(
                "Shortlisted", sum(row["shortlisted"] for row in rows), tone="success"
            ),
            theme.metric_tile(
                "Interviews done",
                sum(row["interviewed"] for row in rows),
                tone="warning",
            ),
        ]
    )
    st.write("")

    st.markdown("##### Roles")
    if not rows:
        theme.empty_state(
            "No roles for your company yet",
            "An admin can seed them from data/roles.json.",
            icon="🗂",
        )
        return
    for row in rows:
        theme.html_block(
            theme.card(
                row["title"],
                subtitle=f"{row['applicants']} applicant(s) · "
                f"{row['shortlisted']} shortlisted · {row['interviewed']} interviewed",
                badge=theme.pill(
                    "Open" if row["is_open"] else "Closed",
                    "success" if row["is_open"] else "neutral",
                ),
                hover=True,
            )
        )


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #

_QUICK_LINKS = (
    ("admin_health", "System health", "🩺"),
    ("admin_users", "People", "👥"),
    ("admin_sandbox", "Sandbox", "🧪"),
    ("account", "My account", "⚙"),
)


def _admin_home(user: auth.User) -> None:
    theme.page_header("Control room", "System-wide state at a glance.", icon="🛠")
    tables = ("users", "companies", "roles", "applications", "interviews")
    totals = {
        table: int(db.scalar(f"SELECT COUNT(*) FROM {table}") or 0) for table in tables
    }
    theme.metric_row(
        [
            theme.metric_tile("Users", totals["users"], tone="primary"),
            theme.metric_tile("Companies", totals["companies"], tone="info"),
            theme.metric_tile("Roles", totals["roles"], tone="info"),
            theme.metric_tile("Applications", totals["applications"], tone="success"),
            theme.metric_tile("Interviews", totals["interviews"], tone="warning"),
        ]
    )
    st.write("")

    left, right = st.columns([1, 1.15], gap="large")
    with left:
        st.markdown("##### Jump to")
        _quick_links()
    with right:
        st.markdown("##### Data")
        _data_summary()


def _quick_links() -> None:
    # app.py stashes the built pages so links survive any nav reshuffle.
    pages = st.session_state.get("_pages") or {}
    if not pages:
        st.caption("Use the sidebar.")
        return
    for key, label, icon in _QUICK_LINKS:
        if key in pages:
            st.page_link(pages[key], label=label, icon=icon)


def _data_summary() -> None:
    from core.config import settings

    sandbox = auth.sandbox_counts()
    size_mb = (
        round(settings.database_path.stat().st_size / 1_048_576, 2)
        if settings.database_path.exists()
        else 0.0
    )
    live = int(
        db.scalar(
            "SELECT COUNT(*) FROM sessions "
            "WHERE revoked_at IS NULL AND expires_at > ?",
            (db.utc_now_iso(),),
        )
        or 0
    )
    theme.html_block(
        theme.kv(
            [
                ("Live sessions", live),
                ("Sandbox rows", sum(sandbox.values())),
                ("Audit entries", int(db.scalar("SELECT COUNT(*) FROM audit_log") or 0)),
                ("Database size", f"{size_mb} MB"),
                ("AI backend", settings.llm.provider.replace("_", " ")),
            ]
        )
    )
