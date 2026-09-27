"""Admin · People.

Everything an operator needs to unblock someone without opening SQL: promote a
recruiter, disable a leaver, force a sign-out, or create an account for a person
who cannot be handed the invite code.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from core import db
from core.config import settings
from services import auth_service as auth
from services import catalog_service
from ui import session, theme

_ROLE_TONE = {"candidate": "info", "recruiter": "primary", "admin": "success"}


def render() -> None:
    theme.inject()
    actor = session.require("admin")
    theme.page_header("People", "Accounts, roles and live sessions.", icon="👥")

    users = auth.list_users()
    _summary(users)
    st.write("")

    listing, create = st.tabs(["Accounts", "Create account"])
    with listing:
        _accounts(users, actor)
    with create:
        _create_form(actor)

    st.divider()
    _audit_tail()


def _summary(users: list[auth.User]) -> None:
    by_role = {role: sum(1 for user in users if user.role == role) for role in auth.ROLES}
    live = int(
        db.scalar(
            "SELECT COUNT(*) FROM sessions "
            "WHERE revoked_at IS NULL AND expires_at > ?",
            (db.utc_now_iso(),),
        )
        or 0
    )
    theme.metric_row(
        [
            theme.metric_tile("Candidates", by_role["candidate"], tone="info"),
            theme.metric_tile("Recruiters", by_role["recruiter"], tone="primary"),
            theme.metric_tile("Admins", by_role["admin"], tone="success"),
            theme.metric_tile("Live sessions", live, "across all devices", tone="warning"),
        ]
    )


def _accounts(users: list[auth.User], actor: auth.User) -> None:
    filters = st.columns([1.6, 1, 1])
    with filters[0]:
        needle = st.text_input(
            "Search", placeholder="Search name or email", label_visibility="collapsed"
        )
    with filters[1]:
        role_filter = st.selectbox(
            "Role", ["all roles", *auth.ROLES], label_visibility="collapsed"
        )
    with filters[2]:
        show_sandbox = st.toggle("Show sandbox", value=True)

    needle = needle.strip().lower()
    rows = [
        user
        for user in users
        if (role_filter == "all roles" or user.role == role_filter)
        and (show_sandbox or not user.is_sandbox)
        and (not needle or needle in f"{user.email} {user.full_name}".lower())
    ]
    if not rows:
        theme.empty_state("No accounts match", "Loosen the filters above.", icon="🔍")
        return

    meta = {
        int(row["id"]): row
        for row in db.query("SELECT id, created_at, last_login_at FROM users")
    }
    for user in rows:
        _account_row(user, meta.get(user.id), actor)


def _account_row(user: auth.User, meta: Any, actor: auth.User) -> None:
    badges = theme.pill(user.role.title(), _ROLE_TONE.get(user.role, "neutral"))
    if user.is_sandbox:
        badges += " " + theme.pill("Sandbox", "warning")
    if not user.is_active:
        badges += " " + theme.pill("Disabled", "danger")

    last = (meta["last_login_at"] if meta else None) or "never signed in"
    left, right = st.columns([3.2, 1], gap="small")
    with left:
        theme.html_block(
            theme.card(
                user.display_name or user.email,
                subtitle=f"{user.email} · {user.company_id or 'no company'} · "
                f"last sign-in {last[:16].replace('T', ' ')}",
                badge=badges,
            )
        )
    with right:
        with st.popover("Manage", use_container_width=True):
            _manage(user, actor)


def _manage(user: auth.User, actor: auth.User) -> None:
    is_self = user.id == actor.id
    st.caption(f"Account #{user.id}" + (" — this is you." if is_self else ""))

    companies = catalog_service.company_names()
    role = st.selectbox(
        "Role",
        auth.ROLES,
        index=auth.ROLES.index(user.role),
        key=f"role_{user.id}",
    )
    company_id = user.company_id
    if role == "recruiter":
        options = list(companies.keys())
        if options:
            company_id = st.selectbox(
                "Company",
                options,
                index=options.index(user.company_id) if user.company_id in options else 0,
                format_func=lambda cid: companies[cid],
                key=f"company_{user.id}",
            )
        else:
            st.warning("No companies exist yet.")

    if st.button("Apply role", key=f"apply_{user.id}", type="primary", use_container_width=True):
        try:
            auth.set_role(user.id, role, company_id=company_id, actor_id=actor.id)
        except auth.AuthError as exc:
            st.error(str(exc))
            return
        st.rerun()

    # An admin locking themselves out of their own portal is a support call, so
    # the two destructive actions are simply absent on your own row.
    if is_self:
        st.caption("Use the Account page to change your own password or sessions.")
        return

    label = "Disable account" if user.is_active else "Enable account"
    if st.button(label, key=f"active_{user.id}", use_container_width=True):
        auth.set_active(user.id, not user.is_active, actor_id=actor.id)
        st.rerun()

    if st.button("Sign out everywhere", key=f"logout_{user.id}", use_container_width=True):
        revoked = auth.logout_all(user.id)
        db.audit(actor.id, "user.force_logout", target=user.id, sessions=revoked)
        st.toast(f"Revoked {revoked} session(s).")
        st.rerun()


def _create_form(actor: auth.User) -> None:
    companies = catalog_service.company_names()
    st.caption(
        "Created accounts skip the invite code — you already hold it by being here."
    )
    with st.form("admin_create_user", border=False):
        left, right = st.columns(2)
        with left:
            full_name = st.text_input("Full name", placeholder="Ada Lovelace")
            email = st.text_input("Email", placeholder="ada@example.com")
        with right:
            role = st.selectbox("Role", auth.ROLES, index=1)
            password = st.text_input("Temporary password", type="password")

        company_id: str | None = None
        if companies:
            picked = st.selectbox(
                "Company (recruiters only)",
                ["—", *companies.keys()],
                format_func=lambda cid: companies.get(cid, "—"),
            )
            company_id = None if picked == "—" else picked

        submitted = st.form_submit_button("Create account", type="primary")

    if not submitted:
        return
    try:
        created = auth.register(
            email,
            password,
            full_name=full_name,
            role=role,
            company_id=company_id,
            invite_code=settings.auth.recruiter_invite_code,
        )
    except auth.AuthError as exc:
        st.error(str(exc))
        return
    db.audit(actor.id, "user.created_by_admin", target=created.id, role=created.role)
    st.success(
        f"Created {created.email} as {created.role}. "
        "Send them the temporary password and ask them to change it."
    )


_ACTION_TONES = (
    ("auth.", "info"),
    ("user.", "primary"),
    ("sandbox.", "warning"),
    ("interview.", "success"),
)


def _action_tone(action: str) -> str:
    for prefix, tone in _ACTION_TONES:
        if action.startswith(prefix):
            return tone
    return "neutral"


def _detail_line(raw: Any) -> str:
    detail = db.loads(raw, {}) or {}
    if not isinstance(detail, dict) or not detail:
        return ""
    return " ".join(f"{key}={value}" for key, value in list(detail.items())[:4])[:90]


def _audit_tail(limit: int = 25) -> None:
    st.markdown("##### Recent activity")
    rows = db.query(
        "SELECT ts, user_id, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    if not rows:
        theme.empty_state("Nothing logged yet", "Actions appear here as they happen.")
        return
    theme.html_block(
        theme.timeline(
            [
                (
                    row["ts"][5:16].replace("T", " "),
                    " · ".join(
                        part
                        for part in (
                            row["action"],
                            f"user {row['user_id']}" if row["user_id"] else "",
                            _detail_line(row["detail"]),
                        )
                        if part
                    ),
                    _action_tone(row["action"]),
                )
                for row in rows
            ]
        )
    )
