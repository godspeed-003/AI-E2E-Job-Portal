"""Recruiter · Pipeline — the read side of everything the portal collects.

The screening pipeline, the interview agent and the proctor have all been
writing to SQLite for five phases with nothing reading it back. This page is
that reader: pick a role, see candidates ranked, open one and get the resume
evaluation, the interview transcript and the integrity timeline on one screen.

Every lookup goes through :mod:`services.access` rather than the service
functions directly, so a recruiter is confined to their own company's roles and
an id in a URL is not a way into a competitor's shortlist. ``session.require``
gates the page; ``access`` gates each record.

Nothing here calls a model. Scores, transcripts and integrity reports are all
already persisted, so the page is cheap to rerun — which matters, because
Streamlit reruns it on every click.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from services import access
from services import application_service as apps
from services import auth_service as auth
from services import catalog_service as catalog
from services import interview_service as ivs
from services import proctor_service as proctor
from ui import session, theme

# Selected role and candidate live in session state so a rerun does not throw
# the recruiter back to the top of the list on every button press.
_KEY_ROLE = "_rec_role_id"
_KEY_APPLICATION = "_rec_application_id"

_SEVERITY_TONE = {
    "critical": "danger",
    "high": "danger",
    "medium": "warning",
    "low": "info",
}

_EVENT_LABELS = {
    "no_face": "No face in frame",
    "multiple_faces": "More than one face",
    "looking_away": "Looking away from screen",
    "substitution": "Face does not match enrollment",
    "object_phone": "Phone visible",
    "object_extra_person": "Second person visible",
    "object_notes": "Notes or laptop on desk",
    "browser_tab_switch": "Switched browser tab",
    "browser_window_blur": "Left the window",
    "browser_paste": "Pasted into the answer box",
    "browser_fullscreen_exit": "Left fullscreen",
}


def render() -> None:
    theme.inject()
    user = session.require("recruiter", "admin")
    theme.page_header(
        "Pipeline",
        "Ranked candidates, interview transcripts and integrity evidence.",
        icon="📋",
    )

    try:
        roles = access.visible_roles(user)
    except access.AccessError as exc:
        theme.empty_state("No hiring access", str(exc), icon="🔒")
        return

    if not roles:
        theme.empty_state(
            "No roles yet",
            "Create one on the Roles page and candidates can start applying.",
            icon="📭",
        )
        return

    include_sandbox = _filters(user, roles)
    role = _selected_role(roles)
    if role is None:
        return

    candidates = apps.ranked_for_role(role.id, include_sandbox=include_sandbox)
    _role_summary(role, candidates, include_sandbox)
    st.write("")

    if not candidates:
        theme.empty_state(
            "No applications yet",
            f"Nobody has applied to {role.title} so far.",
            icon="🕓",
        )
        return

    interviews = {iv.application_id: iv for iv in ivs.for_role(role.id, include_sandbox=True)}
    _candidate_list(candidates, interviews)

    picked = _picked(candidates)
    if picked is not None:
        st.divider()
        _detail(user, picked, interviews.get(picked.id))


# --------------------------------------------------------------------------- #
# Role picking and summary
# --------------------------------------------------------------------------- #


def _filters(user: auth.User, roles: list[catalog.Role]) -> bool:
    """Role selector plus the sandbox toggle. Returns whether to include sandbox."""
    left, right = st.columns([3, 1.2], gap="medium")
    with left:
        labels = {r.id: f"{r.title}{'' if r.is_open else '  (closed)'}" for r in roles}
        ids = [r.id for r in roles]
        current = st.session_state.get(_KEY_ROLE)
        st.selectbox(
            "Role",
            ids,
            index=ids.index(current) if current in ids else 0,
            format_func=lambda rid: labels[rid],
            key=_KEY_ROLE,
            on_change=_clear_selection,
        )
    with right:
        # Off by default: a sandbox persona is an admin rehearsing the flow, and
        # leaving it in the ranking would put a fake candidate on a real shortlist.
        return st.toggle("Include sandbox", value=False, help="Admin test personas.")


def _clear_selection() -> None:
    """Switching role must not leave the previous role's candidate open."""
    st.session_state.pop(_KEY_APPLICATION, None)


def _selected_role(roles: list[catalog.Role]) -> catalog.Role | None:
    chosen = st.session_state.get(_KEY_ROLE)
    for role in roles:
        if role.id == chosen:
            return role
    return roles[0] if roles else None


def _role_summary(
    role: catalog.Role,
    candidates: list[apps.Application],
    include_sandbox: bool,
) -> None:
    counts = apps.counts_for_role(role.id, include_sandbox=include_sandbox)
    interviewed = sum(
        1 for iv in ivs.for_role(role.id, include_sandbox=include_sandbox) if iv.is_completed
    )
    theme.metric_row(
        [
            theme.metric_tile("Applicants", len(candidates), tone="primary"),
            theme.metric_tile("Shortlisted", counts.get("shortlisted", 0), tone="success"),
            theme.metric_tile("Under review", counts.get("under_review", 0), tone="info"),
            theme.metric_tile("Interviews done", interviewed, tone="warning"),
        ]
    )
    st.caption(
        f"{role.title} · keyword floor {role.ats_floor} · shortlist at "
        f"{role.shortlist_floor}/25 · {role.question_budget} planned questions · "
        f"{role.duration_minutes} min window"
    )


# --------------------------------------------------------------------------- #
# The ranked list
# --------------------------------------------------------------------------- #


def _candidate_list(
    candidates: list[apps.Application],
    interviews: dict[int, ivs.Interview],
) -> None:
    st.markdown("##### Ranked candidates")
    for rank, application in enumerate(candidates, start=1):
        interview = interviews.get(application.id)
        left, right = st.columns([3.4, 1], gap="small")
        with left:
            theme.html_block(
                theme.card(
                    f"{rank}.  {application.candidate_name or 'Unnamed candidate'}",
                    subtitle=_subtitle(application, interview),
                    badge=_badges(application, interview),
                )
            )
        with right:
            if st.button(
                "Open", key=f"open_{application.id}", use_container_width=True
            ):
                st.session_state[_KEY_APPLICATION] = application.id
                st.rerun()


def _subtitle(application: apps.Application, interview: ivs.Interview | None) -> str:
    parts = [f"resume {application.llm_score}/{application.max_score}"]
    if not application.screened:
        # An ATS rejection writes a reason with no model involved. Saying so
        # stops a recruiter reading 0/25 as a judgement of the person.
        parts[0] += " (not screened)"
    parts.append(f"keywords {application.ats_score}%")
    if interview is not None and interview.scored:
        parts.append(f"interview {interview.total_score}/{interview.max_total_score}")
    if interview is not None and interview.integrity_score is not None:
        parts.append(f"integrity {interview.integrity_score}")
    return " · ".join(parts)


def _badges(application: apps.Application, interview: ivs.Interview | None) -> str:
    badges = theme.status_pill(application.status)
    if interview is not None:
        badges += " " + theme.status_pill(interview.status)
        if interview.integrity_verdict and interview.integrity_verdict != "clean":
            badges += " " + theme.status_pill(interview.integrity_verdict)
    if application.screening_flags:
        badges += " " + theme.pill("Resume flagged", "warning")
    if application.is_sandbox:
        badges += " " + theme.pill("Sandbox", "warning")
    return badges


def _picked(candidates: list[apps.Application]) -> apps.Application | None:
    chosen = st.session_state.get(_KEY_APPLICATION)
    for application in candidates:
        if application.id == chosen:
            return application
    return None


# --------------------------------------------------------------------------- #
# One candidate
# --------------------------------------------------------------------------- #


def _detail(
    user: auth.User,
    application: apps.Application,
    interview: ivs.Interview | None,
) -> None:
    # Re-fetch through the guard. The list was already company-scoped, but this
    # is the function that reads a resume and a transcript, so it checks again
    # rather than trusting the path that got here.
    try:
        application = access.application(user, application.id)
    except access.AccessError as exc:
        st.error(str(exc))
        return

    if application.is_sandbox:
        theme.sandbox_banner("test persona, not a real applicant")

    header, actions = st.columns([3, 1.2], gap="medium")
    with header:
        st.markdown(f"### {application.candidate_name or 'Unnamed candidate'}")
        st.caption(f"Application #{application.id} · applied {application.created_at[:10]}")
    with actions:
        _status_control(application, user)

    tabs = ["Resume screening"]
    if interview is not None:
        tabs += ["Interview", "Integrity"]
    rendered = st.tabs(tabs)

    with rendered[0]:
        _resume_panel(application)
    if interview is not None:
        with rendered[1]:
            _interview_panel(interview)
        with rendered[2]:
            _integrity_panel(interview)


def _status_control(application: apps.Application, user: auth.User) -> None:
    options = list(apps.STATUSES)
    choice = st.selectbox(
        "Decision",
        options,
        index=options.index(application.status) if application.status in options else 0,
        format_func=lambda s: s.replace("_", " ").title(),
        key=f"status_{application.id}",
    )
    if choice == application.status:
        return
    if st.button("Save decision", key=f"save_{application.id}", type="primary",
                 use_container_width=True):
        try:
            apps.set_status(application.id, choice, actor_id=user.id)
        except apps.ApplicationError as exc:
            st.error(str(exc))
            return
        # Shortlisting here creates the interview and its question plan, exactly
        # as an automatic shortlist would — see application_service.set_status.
        st.toast(f"Marked {choice.replace('_', ' ')}.")
        st.rerun()


def _resume_panel(application: apps.Application) -> None:
    left, right = st.columns([1, 2.4], gap="large")
    with left:
        theme.html_block(
            theme.score_ring(
                application.llm_score, application.max_score, label="Resume score"
            )
        )
        theme.html_block(
            theme.score_ring(application.ats_score, 100, label="Keyword match", size=92)
        )
    with right:
        if application.screening_flags:
            st.warning(
                "Screening flags: " + ", ".join(application.screening_flags),
                icon="⚠️",
            )
        if not application.screened:
            st.info(
                "No model has read this resume — it was rejected on the keyword "
                "floor before evaluation. The score below is not a judgement of "
                "the candidate.",
                icon="ℹ️",
            )
        if application.reason:
            theme.html_block(theme.card("Verdict", body=theme.esc(application.reason)))
        if application.criteria:
            theme.html_block(
                theme.kv([(k.replace("_", " ").title(), f"{v}/5")
                          for k, v in application.criteria.items()])
            )
        _bullets("Strengths", application.strengths)
        _bullets("Gaps", application.weaknesses)

    with st.expander("Keyword detail"):
        st.markdown("**Matched**")
        theme.html_block(
            " ".join(theme.pill(k, "success", dot=False) for k in application.ats_matched)
            or "<span style='opacity:.6'>none</span>"
        )
        st.markdown("**Missing**")
        theme.html_block(
            " ".join(theme.pill(k, "danger", dot=False) for k in application.ats_missing)
            or "<span style='opacity:.6'>none</span>"
        )

    with st.expander("Resume text"):
        st.text(application.resume_text[:20000] or "(no text extracted)")


def _bullets(title: str, items: list[str]) -> None:
    if not items:
        return
    st.markdown(f"**{title}**")
    for item in items:
        st.markdown(f"- {item}")


# --------------------------------------------------------------------------- #
# Interview and integrity
# --------------------------------------------------------------------------- #


def _interview_panel(interview: ivs.Interview) -> None:
    turns = ivs.transcript(interview.id)
    asked, budget = ivs.progress(interview)

    theme.html_block(
        theme.hud(
            [
                ("Status", theme.status_meta(interview.status)[1]),
                ("Questions", f"{asked}/{budget}"),
                (
                    "Score",
                    f"{interview.total_score}/{interview.max_total_score}"
                    if interview.scored
                    else "not scored",
                ),
                ("Attempts", f"{interview.attempt_count}/{interview.max_attempts}"),
            ]
        )
    )

    if not interview.scored and interview.is_completed:
        st.info(
            "The interview finished but scoring did not — most likely a provider "
            "outage. The transcript is complete and can be scored later.",
            icon="ℹ️",
        )

    evaluation = interview.evaluation or {}
    if evaluation:
        left, right = st.columns([1, 2.4], gap="large")
        with left:
            theme.html_block(
                theme.score_ring(
                    interview.total_score or 0,
                    interview.max_total_score,
                    label="Interview score",
                )
            )
        with right:
            if evaluation.get("summary"):
                theme.html_block(
                    theme.card("Summary", body=theme.esc(evaluation["summary"]))
                )
            criteria = interview.criteria()
            if criteria:
                theme.html_block(
                    theme.kv([(k.replace("_", " ").title(), f"{v}/5")
                              for k, v in criteria.items()])
                )
            _bullets("Strengths", list(evaluation.get("strengths") or []))
            _bullets("Weaknesses", list(evaluation.get("weaknesses") or []))

    if interview.plan.degraded:
        st.caption(
            "This plan was the role-generic fallback — the model was unreachable "
            "when the candidate was shortlisted."
        )

    st.markdown("##### Transcript")
    if not turns:
        theme.empty_state("Nothing said yet", "The candidate has not started.")
        return
    for turn in turns:
        _turn_block(turn)


def _turn_block(turn: ivs.Turn) -> None:
    tags = []
    if turn.is_adaptive:
        tags.append(theme.pill(turn.action.title(), "info", dot=False))
    if turn.focus_area:
        tags.append(theme.pill(turn.focus_area, "neutral", dot=False))
    if turn.was_forced:
        # The candidate hit the guardrail three times and the answer was taken
        # anyway. A recruiter reading it should know why it looks odd.
        tags.append(theme.pill("Guardrail forced", "warning", dot=False))
    elif turn.flags:
        tags.append(theme.pill("Flagged", "warning", dot=False))

    theme.html_block(theme.question_block(turn.question))
    if tags:
        theme.html_block(" ".join(tags))
    if turn.answered:
        theme.html_block(theme.answer_block(turn.answer, who="Candidate"))
    else:
        st.caption("— unanswered —")


def _integrity_panel(interview: ivs.Interview) -> None:
    events = proctor.events_for(interview.id)

    st.caption(
        "Advisory evidence for a human reviewer. None of this is proof of "
        "misconduct — a flaky webcam and a wandering gaze look alike to a detector."
    )

    if interview.integrity_score is None:
        theme.empty_state(
            "No integrity report",
            "Proctoring was off for this session, or it never started.",
            icon="🎥",
        )
        return

    left, right = st.columns([1, 2.4], gap="large")
    with left:
        theme.html_block(
            theme.score_ring(interview.integrity_score, 100, label="Integrity")
        )
        theme.html_block(theme.status_pill(interview.integrity_verdict or "review"))
    with right:
        by_kind: dict[str, int] = {}
        worst: dict[str, str] = {}
        for row in events:
            kind = row["kind"]
            by_kind[kind] = by_kind.get(kind, 0) + 1
            worst.setdefault(kind, row["severity"])
        if by_kind:
            theme.html_block(
                theme.kv(
                    [
                        (_EVENT_LABELS.get(kind, kind), f"{count}×")
                        for kind, count in sorted(
                            by_kind.items(), key=lambda kv: -kv[1]
                        )
                    ]
                )
            )
        else:
            st.success("No integrity signals were raised.", icon="✅")

    if events:
        st.markdown("##### Timeline")
        theme.html_block(
            theme.timeline(
                [
                    (
                        _clock(row["elapsed_seconds"]),
                        _event_line(row),
                        _SEVERITY_TONE.get(row["severity"], "neutral"),
                    )
                    for row in events
                ]
            )
        )
        _snapshots(events)


def _clock(seconds: Any) -> str:
    total = int(seconds or 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def _event_line(row: dict[str, Any]) -> str:
    label = _EVENT_LABELS.get(row["kind"], row["kind"])
    duration = float(row.get("duration_seconds") or 0)
    if duration >= 1:
        label += f" ({duration:.0f}s)"
    return f"{label} · {row['severity']}"


def _snapshots(events: list[dict[str, Any]]) -> None:
    paths = [row["snapshot_path"] for row in events if row.get("snapshot_path")]
    if not paths:
        return
    with st.expander(f"Snapshots ({len(paths)})"):
        st.caption("Captured at the moment a high-severity signal fired.")
        for chunk in range(0, len(paths), 4):
            for column, path in zip(st.columns(4), paths[chunk:chunk + 4]):
                with column:
                    try:
                        st.image(path, use_container_width=True)
                    except Exception:
                        # The row outlives the file: a sandbox reset or a manual
                        # cleanup can remove media while the event stays.
                        st.caption("snapshot unavailable")
