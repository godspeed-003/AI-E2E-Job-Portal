"""Candidate apply screen: upload a resume, then see exactly how it scored.

The page shows the pipeline's reasoning rather than a verdict. The keyword match
lists the requirements it did and did not find; the model's five criteria,
strengths and weaknesses sit underneath. A candidate filtered out by the keyword
floor can read the rule that filtered them, and a recruiter can defend it.

Admins reach the same screen so the module can be exercised end to end. Whatever
an admin submits here is flagged disposable — an admin is never a real candidate
for the role — so a test run can never reach the shortlist a recruiter reads.
"""

from __future__ import annotations

import streamlit as st

from core import resume as resume_core
from services import application_service as apps
from services import auth_service as auth
from services import catalog_service as catalog
from ui import session, theme

_FLASH = "_apply_flash"
_ACCEPTED = [suffix.lstrip(".") for suffix in resume_core.SUPPORTED_SUFFIXES]


def render() -> None:
    theme.inject()
    user = session.require("candidate", "admin")
    # No toggle to forget: an admin on this page is exercising the module, not
    # applying, so the row is disposable whatever the sandbox switch says.
    sandbox = session.sandbox_view() or user.is_sandbox or user.role == "admin"
    theme.page_header(
        "Apply to a role",
        "Upload your resume and see how it scores against the requirements.",
        icon="📄",
    )
    if sandbox:
        theme.sandbox_banner("nothing here reaches a recruiter's shortlist")
    _flash()

    roles = catalog.list_roles(only_open=True)
    if not roles:
        theme.empty_state(
            "No open roles right now", "Check back later.", icon="🗓"
        )
        return

    role = _role_picker(roles)
    company = catalog.get_company(role.company_id)
    _role_summary(role, company)

    existing = apps.latest_for(user.id, role.id)
    if existing is not None:
        _existing_application(user, existing, role, sandbox=sandbox)
    else:
        _upload_form(user, role, sandbox=sandbox)


def _flash() -> None:
    """Show and clear a message queued before a rerun."""
    message = st.session_state.pop(_FLASH, None)
    if not message:
        return
    kind, text = message
    {"success": st.success, "warning": st.warning, "error": st.error}.get(
        kind, st.info
    )(text)


def _queue_flash(kind: str, text: str) -> None:
    st.session_state[_FLASH] = (kind, text)


# --------------------------------------------------------------------------- #
# Role selection
# --------------------------------------------------------------------------- #


def _role_picker(roles: list[catalog.Role]) -> catalog.Role:
    names = catalog.company_names()
    labels = {
        role.id: f"{role.title} · {names.get(role.company_id, role.company_id)}"
        for role in roles
    }
    chosen = st.selectbox(
        "Role",
        options=[role.id for role in roles],
        format_func=lambda role_id: labels[role_id],
        key="apply_role_id",
    )
    return next(role for role in roles if role.id == chosen)


def _role_summary(role: catalog.Role, company: catalog.Company | None) -> None:
    requirement_pills = " ".join(
        theme.pill(req, "info", dot=False) for req in role.requirements
    ) or theme.pill("No requirements listed", "neutral", dot=False)
    theme.html_block(
        theme.card(
            role.title,
            subtitle=company.name if company else role.company_id,
            badge=theme.pill(f"{role.ats_floor}% keyword floor", "warning"),
            body=(
                f'<p style="margin:0 0 .5rem">{theme.esc(role.job_description)}</p>'
                f"{requirement_pills}"
            ),
        )
    )


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #


def _upload_form(
    user: auth.User, role: catalog.Role, *, sandbox: bool, replacing: bool = False
) -> None:
    st.markdown("##### " + ("Replace your resume" if replacing else "Your resume"))
    upload_tab, paste_tab = st.tabs(["Upload a file", "Paste the text"])

    with upload_tab:
        with st.form(f"apply_file_{role.id}", border=False):
            upload = st.file_uploader(
                f"PDF, DOCX or plain text · up to "
                f"{resume_core.MAX_UPLOAD_BYTES // 1_048_576} MB",
                type=_ACCEPTED,
                accept_multiple_files=False,
            )
            sent = st.form_submit_button("Submit application", type="primary")
        if sent:
            if upload is None:
                st.error("Choose a file first.")
            else:
                _run_pipeline(
                    user,
                    role,
                    sandbox=sandbox,
                    data=upload.getvalue(),
                    filename=upload.name,
                )

    with paste_tab:
        with st.form(f"apply_text_{role.id}", border=False):
            pasted = st.text_area(
                "Resume text",
                height=240,
                placeholder="Paste your resume here if the file will not upload…",
            )
            # Deliberately not the same label as the file tab: two buttons reading
            # "Submit application" is ambiguous to anyone not looking at the tabs.
            sent_text = st.form_submit_button("Submit pasted resume")
        if sent_text:
            _run_pipeline(user, role, sandbox=sandbox, pasted=pasted)


def _run_pipeline(
    user: auth.User,
    role: catalog.Role,
    *,
    sandbox: bool,
    data: bytes | None = None,
    filename: str = "",
    pasted: str = "",
) -> None:
    """Ingest, then screen. Every failure here is worded for the candidate."""
    try:
        if data is not None:
            resume = resume_core.ingest(data, filename, prefix=f"u{user.id}")
        else:
            resume = resume_core.from_text(pasted)
    except resume_core.ResumeError as exc:
        st.error(str(exc))
        return

    with st.spinner("Reading your resume and scoring it against the role…"):
        try:
            application = apps.submit(user, role.id, resume, is_sandbox=sandbox)
        except apps.ApplicationError as exc:
            # apply() may well have succeeded and only the model call failed, so
            # the row is left in place and the retry button below picks it up.
            _queue_flash("warning", str(exc))
            st.rerun()
            return

    _queue_flash(
        "success",
        f"Application submitted — {application.status_label.lower()} "
        f"({application.ats_score}% keyword match).",
    )
    st.rerun()


# --------------------------------------------------------------------------- #
# An application that already exists
# --------------------------------------------------------------------------- #


def _existing_application(
    user: auth.User,
    application: apps.Application,
    role: catalog.Role,
    *,
    sandbox: bool,
) -> None:
    st.markdown("##### Your application")
    theme.html_block(
        theme.card(
            application.candidate_name or "Your application",
            subtitle=f"Submitted {application.created_at[:10]}"
            f" · updated {application.updated_at[:10]}",
            badge=theme.status_pill(application.status),
        )
    )
    _scores(application, role)
    _actions(user, application, role, sandbox=sandbox)


# --------------------------------------------------------------------------- #
# The scoring breakdown
# --------------------------------------------------------------------------- #

_CRITERION_LABELS = {
    "skill_match": "Skill match",
    "experience": "Experience",
    "projects": "Projects",
    "communication": "Communication",
    "culture_fit": "Culture fit",
}

# Plain wording for the sanitiser's flags. A candidate who pasted a template off
# the internet deserves to know which line tripped the filter, not a raw slug.
_FLAG_LABELS = {
    "instruction_override": "text asking the reviewer to ignore its instructions",
    "persona_hijack": "text asking the reviewer to take on another role",
    "chat_markup": "chat control markup",
    "score_demand": "text demanding a particular score",
    "verdict_demand": "text demanding a particular decision",
}


def _scores(application: apps.Application, role: catalog.Role) -> None:
    """The point of the page: why this application scored what it scored."""
    scored = application.screened
    theme.metric_row(
        [
            theme.metric_tile(
                "Keyword match",
                f"{application.ats_score}%",
                f"{role.ats_floor}% needed to reach the reviewer",
                "success" if application.ats_score >= role.ats_floor else "danger",
            ),
            theme.metric_tile(
                "Fit score",
                f"{application.llm_score}/{application.max_score}" if scored else "—",
                f"{role.shortlist_floor} or more shortlists"
                if scored
                else "not reviewed yet",
                "success"
                if application.llm_score >= role.shortlist_floor
                else "warning",
            ),
            theme.metric_tile(
                "Alignment",
                f"{application.alignment_score:.2f}" if scored else "—",
                "how close the reviewer read the fit",
                "info",
            ),
        ]
    )
    _keywords(application, role)
    if scored:
        _verdict(application)
    elif application.is_rejected:
        theme.html_block(
            theme.card(
                "Screening stopped at the keyword floor",
                subtitle="No reviewer read the resume, so there is no fit score.",
                body=f'<p style="margin:0">{theme.esc(application.reason)}</p>',
            )
        )
    else:
        st.warning(
            "The keyword check ran but the review did not finish. Your submission "
            "is saved — retry below."
        )
    _flags(application)


def _keywords(application: apps.Application, role: catalog.Role) -> None:
    """Which requirements were found and which were not — the rule, in full."""
    if not role.requirements:
        return
    found = " ".join(
        theme.pill(word, "success", dot=False) for word in application.ats_matched
    ) or '<span style="color:#94a3b8">none</span>'
    missing = " ".join(
        theme.pill(word, "danger", dot=False) for word in application.ats_missing
    ) or '<span style="color:#94a3b8">none</span>'
    theme.html_block(
        theme.card(
            "Keyword check",
            subtitle=f"{len(application.ats_matched)} of "
            f"{len(role.requirements)} requirements found in your resume",
            badge=theme.pill(f"{application.ats_score}%", "info"),
            body=(
                f'<div style="margin:0 0 .5rem"><strong>Found</strong><br>{found}</div>'
                f"<div><strong>Not found</strong><br>{missing}</div>"
            ),
        )
    )


def _verdict(application: apps.Application) -> None:
    """The five criteria, then the prose the reviewer wrote about them."""
    ring, detail = st.columns([1, 2.4], gap="large")
    with ring:
        theme.html_block(
            theme.score_ring(
                application.llm_score, application.max_score, label="Fit score"
            )
        )
    with detail:
        theme.html_block(
            theme.kv(
                (_CRITERION_LABELS.get(name, name), f"{score} / {apps.MAX_CRITERION}")
                for name, score in application.criteria.items()
            )
        )

    if application.reason:
        theme.html_block(
            theme.card(
                "Why this score",
                body=f'<p style="margin:0">{theme.esc(application.reason)}</p>',
            )
        )

    strong, weak = st.columns(2, gap="large")
    with strong:
        _bullets("Strengths", application.strengths, "success")
    with weak:
        _bullets("Gaps", application.weaknesses, "warning")


def _bullets(title: str, items: list[str], tone: str) -> None:
    body = (
        "".join(
            f'<li style="margin:0 0 .3rem">{theme.esc(item)}</li>' for item in items
        )
        or '<li style="color:#94a3b8">Nothing recorded.</li>'
    )
    theme.html_block(
        theme.card(
            title,
            badge=theme.pill(str(len(items)), tone),
            body=f'<ul style="margin:0;padding-left:1.1rem">{body}</ul>',
        )
    )


def _flags(application: apps.Application) -> None:
    """Tell the candidate their resume was sanitised, and why.

    Nothing here rejects an application: the flagged lines are removed before the
    reviewer sees the text, and the rest of the resume is scored normally.
    """
    if not application.screening_flags:
        return
    named = [
        _FLAG_LABELS.get(flag, flag.replace("_", " "))
        for flag in application.screening_flags
    ]
    st.info(
        "Part of your resume was removed before review — "
        + ", ".join(named)
        + ". The rest was scored as normal. If that was a template artefact, "
        "replace the file below."
    )


# --------------------------------------------------------------------------- #
# What the candidate can do next
# --------------------------------------------------------------------------- #


def _actions(
    user: auth.User,
    application: apps.Application,
    role: catalog.Role,
    *,
    sandbox: bool,
) -> None:
    if not application.screened and not application.is_rejected:
        _retry(application)

    if application.is_shortlisted:
        from services import interview_service as interviews

        interview = interviews.for_application(application.id)
        col_msg, col_btn = st.columns([3, 1], gap="small")
        with col_msg:
            st.success(
                "You are shortlisted. Your interview is tailored to this resume — "
                "open the interview room to sit it inside your window."
            )
        with col_btn:
            if interview is not None and interview.status in ("pending", "in_progress"):
                if st.button(
                    "Go to interview",
                    key=f"apply_goto_interview_{application.id}",
                    type="primary",
                    use_container_width=True,
                ):
                    st.query_params["interview_id"] = str(interview.id)
                    page = st.session_state.get("_pages", {}).get("interview_room")
                    if page is not None:
                        st.switch_page(page)
    else:
        with st.expander("Replace my resume"):
            st.caption(
                "A new file replaces this application and is scored again from "
                "scratch. The previous scores are discarded."
            )
            _upload_form(user, role, sandbox=sandbox, replacing=True)

    with st.expander("Withdraw this application"):
        st.caption(
            "This deletes the application and anything prepared from it. You can "
            "apply again afterwards."
        )
        if st.button("Withdraw", key=f"withdraw_{application.id}"):
            try:
                apps.withdraw(application.id, user_id=user.id)
            except apps.ApplicationError as exc:
                _queue_flash("error", str(exc))
            else:
                _queue_flash("info", f"Withdrawn from {role.title}.")
            st.rerun()


def _retry(application: apps.Application) -> None:
    """Second chance at the model call, without re-uploading the file."""
    if not st.button("Retry scoring", type="primary", key=f"retry_{application.id}"):
        return
    with st.spinner("Scoring your resume against the role…"):
        try:
            scored = apps.screen(application.id)
        except apps.ApplicationError as exc:
            _queue_flash("warning", str(exc))
        else:
            _queue_flash(
                "success", f"Scoring finished — {scored.status_label.lower()}."
            )
    st.rerun()
