"""Admin · Sandbox.

The point of this page is that testing the portal must never dirty real data.
Everything created while sandbox mode is on carries ``is_sandbox = 1``, which
keeps it out of recruiter reporting and makes cleanup a single click.

An admin can also mint a disposable candidate account and sign in as it in a
second tab, which is the only honest way to rehearse the candidate journey —
upload a resume, get shortlisted, sit the interview — without inventing a real
person.
"""

from __future__ import annotations

import streamlit as st

from core import db
from services import auth_service as auth
from ui import session, theme

_GUARANTEES = (
    "Sandbox accounts live on the @sandbox.local domain and are never emailed.",
    "Recruiter dashboards and metrics exclude sandbox rows by default.",
    "Deleting a sandbox user cascades to its applications, interviews, turns "
    "and proctoring events.",
    "Reset only ever touches rows flagged as sandbox — live data is untouched.",
)


def render() -> None:
    theme.inject()
    actor = session.require("admin")
    theme.page_header(
        "Sandbox",
        "Exercise the whole pipeline without touching real applicant data.",
        icon="🧪",
    )
    theme.sandbox_banner("every row created here is flagged is_sandbox = 1")

    _mode(actor)
    counts = auth.sandbox_counts()
    theme.metric_row(
        [
            theme.metric_tile("Sandbox accounts", counts["users"], tone="warning"),
            theme.metric_tile("Applications", counts["applications"], tone="info"),
            theme.metric_tile("Interviews", counts["interviews"], tone="primary"),
        ]
    )
    st.write("")

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        _persona()
    with right:
        _reset(actor, counts)
        _guarantees()


def _mode(actor: auth.User) -> None:
    enabled = session.sandbox_view()
    chosen = st.toggle(
        "Sandbox mode for my own account",
        value=enabled,
        help="While this is on, anything you create from your own account is "
        "flagged as sandbox data and stays out of recruiter reporting.",
    )
    if chosen != enabled:
        session.set_sandbox_view(chosen)
        db.audit(actor.id, "sandbox.mode", enabled=chosen)
        st.rerun()


def _persona() -> None:
    st.markdown("##### Disposable candidate")
    st.caption(
        "Sign in as this account in a private window to walk the candidate "
        "journey end to end. The password is fixed on purpose: these accounts "
        "exist to be thrown away, not secured."
    )
    label = st.text_input(
        "Persona label",
        value="candidate",
        help="Becomes the email prefix, so several personas can coexist.",
    )
    email = auth.sandbox_email(label or "candidate")
    existing = auth.get_user_by_email(email)
    theme.html_block(
        theme.kv(
            [
                ("Email", email),
                ("Password", auth.SANDBOX_PASSWORD),
                ("Status", "ready to use" if existing else "not created yet"),
            ]
        )
    )
    if st.button("Create or reuse this persona", type="primary", use_container_width=True):
        person = auth.ensure_sandbox_candidate(
            label or "candidate",
            full_name=f"Sandbox {(label or 'candidate').replace('-', ' ').title()}",
        )
        st.toast(f"{person.email} is ready.")
        st.rerun()


def _reset(actor: auth.User, counts: dict[str, int]) -> None:
    st.markdown("##### Reset")
    total = sum(counts.values())
    st.caption(
        "Deletes every sandbox account, application, interview, turn and "
        "proctoring event. Real candidate data is not affected."
    )
    confirm = st.checkbox(
        f"Yes, delete all {total} sandbox row(s)", disabled=total == 0
    )
    if st.button(
        "Reset sandbox", disabled=not confirm, use_container_width=True
    ):
        removed = auth.reset_sandbox(actor_id=actor.id)
        st.toast("Removed " + ", ".join(f"{v} {k}" for k, v in removed.items()) + ".")
        st.rerun()


def _guarantees() -> None:
    items = "".join(f"<li>{theme.esc(line)}</li>" for line in _GUARANTEES)
    theme.html_block(
        theme.card(
            "What sandbox mode guarantees",
            body=f'<ul style="margin:0;padding-left:1.1rem;color:{theme.MUTED}">{items}</ul>',
        )
    )
