"""Admin · Sandbox.

The point of this page is that testing the portal must never dirty real data.
Everything created while sandbox mode is on carries ``is_sandbox = 1``, which
keeps it out of recruiter reporting and makes cleanup a single click.

An admin can also mint a disposable candidate account and sign in as it in a
second tab, which is the only honest way to rehearse the candidate journey —
upload a resume, get shortlisted, sit the interview — without inventing a real
person.

The skip-ahead panel exists because that rehearsal is otherwise expensive.
Reaching the scoring screen the honest way means writing a resume, waiting on a
screening call and sitting six questions; a tester checking one change to the
integrity report will not do that twice, and so stops testing the end of the
flow at all. Skipping ahead puts a shortlisted candidate and a two-question
interview one click away — the same rows the real path produces, just without
the twenty minutes.
"""

from __future__ import annotations

import streamlit as st

from core import db
from core import resume as resume_core
from services import application_service as apps
from services import auth_service as auth
from services import catalog_service as catalog
from services import interview_service as interviews
from ui import session, theme

_GUARANTEES = (
    "Sandbox accounts live on the @sandbox.local domain and are never emailed.",
    "Recruiter dashboards and metrics exclude sandbox rows by default.",
    "Deleting a sandbox user cascades to its applications, interviews, turns "
    "and proctoring events.",
    "Reset also deletes the files the cascade cannot reach: answer recordings "
    "and proctoring snapshots.",
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
        st.write("")
        _skip_ahead(actor)
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


def _sample_resume(role: catalog.Role) -> str:
    """A resume built from the role's own requirements.

    Written from the role rather than shipped as a fixed blob so that the
    question plan the model generates is about the role being tested. A canned
    backend resume tested against an HR role produces an interview about neither.
    """
    skills = ", ".join(role.requirements[:8]) or "the requirements listed for this role"
    return (
        f"Sandbox Candidate — applying for {role.title}.\n\n"
        f"Six years of hands-on experience covering {skills}. "
        "Led a team of four through two product launches, owning the design "
        "decisions and the post-incident reviews that followed. Comfortable "
        "explaining trade-offs to a non-technical audience.\n\n"
        "This resume was generated by the sandbox page for testing. It is not a "
        "real person and no hiring decision should ever be read from it."
    )


def _skip_ahead(actor: auth.User) -> None:
    """Shortlist a persona and prepare a short interview in one click."""
    st.markdown("##### Skip ahead")
    st.caption(
        "Puts a persona straight into a shortlisted state with an interview "
        "waiting, so the room and the scoring screens can be reached without "
        "writing a resume first."
    )

    personas = [
        user
        for user in auth.list_users()
        if user.is_sandbox and user.role == "candidate"
    ]
    roles = catalog.list_roles(only_open=True)
    if not personas:
        st.info("Create a disposable candidate above first.")
        return
    if not roles:
        st.info("No open roles in the catalogue to shortlist against.")
        return

    persona = st.selectbox(
        "Persona",
        personas,
        format_func=lambda user: f"{user.full_name or user.email} · {user.email}",
    )
    role = st.selectbox("Role", roles, format_func=lambda item: item.title)
    questions = st.number_input(
        "Questions in the interview",
        min_value=1,
        max_value=6,
        value=2,
        help="Two is enough to exercise plan, ask, answer, score and the "
        "integrity report without sitting a full interview.",
    )

    if st.button("Shortlist and prepare interview", use_container_width=True):
        _prepare(actor, persona, role, int(questions))


def _prepare(
    actor: auth.User, persona: auth.User, role: catalog.Role, questions: int
) -> None:
    """Apply, force the shortlist, then cut the interview down.

    ``set_status`` rather than ``submit``: forcing the status skips the model
    call that screening would make, and the recruiter override path it goes
    through is the same one that creates the interview for a real shortlist. A
    generated resume failing the keyword floor is not a finding about anything.
    """
    try:
        existing = apps.latest_for(persona.id, role.id)
        if existing is not None and existing.is_shortlisted:
            # Clicking twice must not be an error: apply() refuses to replace the
            # resume behind a live shortlist, and rightly so.
            application = existing
        else:
            application = apps.apply(
                persona,
                role.id,
                resume_core.from_text(_sample_resume(role)),
                is_sandbox=True,
            )
            application = apps.set_status(
                application.id,
                "shortlisted",
                actor_id=actor.id,
                note="Shortlisted from the sandbox page for testing.",
            )
    except Exception as exc:
        st.error(f"Could not shortlist: {exc}")
        return

    interview = interviews.for_application(application.id)
    if interview is None:
        st.warning(
            "Shortlisted, but the interview was not created — check the AI "
            "provider on the Health page."
        )
        return

    try:
        interview = interviews.shorten(interview.id, questions)
    except interviews.InterviewError as exc:
        # Already sat, most likely. The shortlist still stands.
        st.warning(f"Shortlisted, but the interview was left at its full length: {exc}")

    db.audit(actor.id, "sandbox.skip_ahead", user=persona.id, role=role.id)
    st.toast(
        f"{persona.email} is shortlisted for {role.title} "
        f"with {interview.planned_questions} question(s)."
    )
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
