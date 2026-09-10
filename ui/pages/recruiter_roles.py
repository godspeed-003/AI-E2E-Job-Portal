"""Recruiter · Roles — create and tune the jobs candidates apply to.

Everything on this page was already honoured by the pipeline; none of it was
reachable without editing SQLite. ``catalog_service.Role`` resolves each setting
as *role override, else the global default*, so the forms here store ``None`` for
anything left on "default" rather than freezing today's ``.env`` value into the
row.

Two things a recruiter changes here have consequences worth naming, and both are
said on screen rather than only in this docstring:

* The **keyword floor** is a cost guard, not a judgement — it stops the model
  reading resumes that share no vocabulary with the role. Raising it rejects
  people before any model sees them.
* The **interview settings** are read when a candidate is shortlisted. Changing
  them does not rewrite an interview that already exists.

Company scoping is :mod:`services.access`'s job: a recruiter sees and edits
their own company's roles, an admin sees all of them.
"""

from __future__ import annotations

import streamlit as st

from core import db
from core.config import settings
from services import access
from services import application_service as apps
from services import auth_service as auth
from services import catalog_service as catalog
from ui import session, theme

_NEW = "__new__"


def render() -> None:
    theme.inject()
    user = session.require("recruiter", "admin")
    theme.page_header("Roles", "Job descriptions, thresholds and interview windows.", icon="🗂️")

    try:
        roles = access.visible_roles(user)
        company_limit = access.reach(user)
    except access.AccessError as exc:
        theme.empty_state("No hiring access", str(exc), icon="🔒")
        return

    companies = _companies(user, company_limit)
    if not companies:
        st.warning(
            "Your account is not attached to a company, so there is nothing to "
            "create a role under. An admin can set this on the People page.",
            icon="⚠️",
        )
        return

    _summary(roles)
    st.write("")

    options = [_NEW, *[role.id for role in roles]]
    labels = {role.id: role.title for role in roles} | {_NEW: "＋  New role"}
    chosen = st.selectbox(
        "Editing",
        options,
        format_func=lambda key: labels[key],
        key="_roles_editing",
    )

    st.divider()
    if chosen == _NEW:
        _editor(user, None, companies)
    else:
        role = next((r for r in roles if r.id == chosen), None)
        if role is None:
            st.info("That role is gone. Pick another.")
            return
        _editor(user, role, companies)


def _companies(user: auth.User, company_limit: str | None) -> dict[str, str]:
    """``{id: name}`` the user may file a role under."""
    names = catalog.company_names()
    if company_limit is None:
        return names
    if company_limit in names:
        return {company_limit: names[company_limit]}
    # A recruiter whose company row was never seeded: show it anyway, using the
    # id as the name, so they are not locked out of their own roles.
    return {company_limit: company_limit.replace("_", " ").title()}


def _summary(roles: list[catalog.Role]) -> None:
    open_count = sum(1 for role in roles if role.is_open)
    applicants = sum(
        sum(apps.counts_for_role(role.id).values()) for role in roles
    )
    theme.metric_row(
        [
            theme.metric_tile("Roles", len(roles), tone="primary"),
            theme.metric_tile("Open", open_count, "accepting applications", tone="success"),
            theme.metric_tile("Closed", len(roles) - open_count, tone="neutral"),
            theme.metric_tile("Applications", applicants, "across these roles", tone="info"),
        ]
    )


# --------------------------------------------------------------------------- #
# The editor
# --------------------------------------------------------------------------- #


def _editor(
    user: auth.User,
    role: catalog.Role | None,
    companies: dict[str, str],
) -> None:
    creating = role is None
    prefix = "new" if creating else role.id

    with st.form(f"role_form_{prefix}", border=False):
        st.markdown("##### Basics")
        left, right = st.columns(2, gap="medium")
        with left:
            title = st.text_input(
                "Job title",
                value="" if creating else role.title,
                placeholder="Senior Backend Engineer",
            )
            company_ids = list(companies)
            company_id = st.selectbox(
                "Company",
                company_ids,
                index=(
                    company_ids.index(role.company_id)
                    if role is not None and role.company_id in company_ids
                    else 0
                ),
                format_func=lambda cid: companies[cid],
                disabled=not creating,  # moving a role between companies would
                                        # orphan its applications; make a new one
                help=None if creating else "A role cannot change company.",
            )
        with right:
            is_open = st.toggle(
                "Open to applications",
                value=True if creating else role.is_open,
            )
            requirements_raw = st.text_area(
                "Requirements (one per line)",
                value="" if creating else "\n".join(role.requirements),
                height=120,
                placeholder="Python\nPostgreSQL\nDistributed systems",
            )

        job_description = st.text_area(
            "Job description",
            value="" if creating else role.job_description,
            height=180,
            placeholder="What the person will actually do, and what good looks like.",
        )

        st.markdown("##### Screening")
        st.caption(
            "The keyword floor is a cost guard, not a verdict — it stops the model "
            "reading resumes that share no vocabulary with the role. Raising it "
            "rejects people before any model sees them."
        )
        screen_cols = st.columns(2, gap="medium")
        with screen_cols[0]:
            ats_floor = _override_number(
                "Keyword floor (%)",
                current=None if creating else role.ats_reject_below,
                default=settings.screening.ats_reject_below,
                low=0,
                high=100,
                key=f"ats_{prefix}",
            )
        with screen_cols[1]:
            shortlist_floor = _override_number(
                "Shortlist at (out of 25)",
                current=None if creating else role.shortlist_llm_score_min,
                default=settings.screening.shortlist_llm_score_min,
                low=0,
                high=25,
                key=f"short_{prefix}",
            )

        st.markdown("##### Interview")
        st.caption(
            "Read when a candidate is shortlisted. Changing these does not "
            "rewrite an interview that already exists."
        )
        iv_cols = st.columns(3, gap="medium")
        with iv_cols[0]:
            questions = _override_number(
                "Planned questions",
                current=None if creating else role.planned_questions,
                default=settings.interview.planned_questions,
                low=1,
                high=12,
                key=f"q_{prefix}",
            )
        with iv_cols[1]:
            duration = _override_number(
                "Time limit (minutes)",
                current=None if creating else role.interview_duration_minutes,
                default=settings.interview.duration_minutes,
                low=5,
                high=180,
                key=f"dur_{prefix}",
            )
        with iv_cols[2]:
            window = _override_number(
                "Window (days to sit it)",
                current=None if creating else role.interview_window_days,
                default=settings.interview.window_days,
                low=1,
                high=90,
                key=f"win_{prefix}",
            )

        submitted = st.form_submit_button(
            "Create role" if creating else "Save changes",
            type="primary",
        )

    if submitted:
        _save(
            user,
            role,
            company_id=company_id,
            title=title,
            job_description=job_description,
            requirements_raw=requirements_raw,
            is_open=is_open,
            overrides={
                "ats_reject_below": ats_floor,
                "shortlist_llm_score_min": shortlist_floor,
                "planned_questions": questions,
                "interview_duration_minutes": duration,
                "interview_window_days": window,
            },
        )

    if role is not None:
        _footer(role)


def _override_number(
    label: str,
    *,
    current: int | None,
    default: int,
    low: int,
    high: int,
    key: str,
) -> int | None:
    """A number with an explicit "use the default" state.

    Returning ``None`` for default rather than writing today's value means a
    role that was never tuned follows the ``.env`` setting as it changes, which
    is what the ``Role`` properties already assume.

    The checkbox wins over the number. It is not wired to grey the number out:
    inside a form no widget triggers a rerun, so a ``disabled=`` computed from
    the checkbox would lag a submit behind and read as broken. The label says
    which one counts instead.
    """
    value = st.number_input(
        label,
        min_value=low,
        max_value=high,
        value=int(current if current is not None else default),
        step=1,
        key=key,
    )
    use_default = st.checkbox(
        f"use default ({default})",
        value=current is None,
        key=f"{key}_def",
        help="Ticked, this role follows the global setting and ignores the number above.",
    )
    return None if use_default else int(value)


def _save(
    user: auth.User,
    role: catalog.Role | None,
    *,
    company_id: str,
    title: str,
    job_description: str,
    requirements_raw: str,
    is_open: bool,
    overrides: dict[str, int | None],
) -> None:
    title = title.strip()
    if not title:
        st.error("A role needs a title.")
        return
    if not access.can_reach_company(user, company_id):
        st.error("You cannot create a role for that company.")
        return

    requirements = [line.strip() for line in requirements_raw.splitlines() if line.strip()]
    role_id = role.id if role is not None else _unique_id(company_id, title)

    catalog.upsert_role(
        role_id,
        company_id,
        title,
        job_description=job_description.strip(),
        requirements=requirements,
        is_open=is_open,
        **overrides,  # type: ignore[arg-type]
    )
    db.audit(
        user.id,
        "role.created" if role is None else "role.updated",
        role=role_id,
        company=company_id,
    )
    st.session_state["_roles_editing"] = role_id
    st.toast(f"Saved {title}.")
    st.rerun()


def _unique_id(company_id: str, title: str) -> str:
    """``company_title``, with a counter if that is taken.

    Role ids are the primary key and appear in every application row, so a
    collision would silently merge two jobs into one — hence the probe rather
    than trusting the slug to be unique.
    """
    base = f"{catalog.slugify(company_id)}_{catalog.slugify(title)}"
    candidate = base
    suffix = 2
    while catalog.get_role(candidate) is not None:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _footer(role: catalog.Role) -> None:
    counts = apps.counts_for_role(role.id)
    total = sum(counts.values())
    st.divider()
    theme.html_block(
        theme.kv(
            [
                ("Role id", role.id),
                ("Applications", total),
                ("Shortlisted", counts.get("shortlisted", 0)),
                ("Status", "Open" if role.is_open else "Closed"),
            ]
        )
    )
    if total:
        # Deleting cascades to applications and interviews, so it is not offered
        # here at all — closing the role is the reversible way to stop intake.
        st.caption(
            f"{total} application(s) hang off this role. Close it to stop new "
            "ones; the existing candidates keep their interviews."
        )
