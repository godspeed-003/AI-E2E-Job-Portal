"""The application pipeline: a resume goes in, a screened application comes out.

parse → clean → ATS pre-filter → LLM evaluation → status.

This replaces ``modules/evaluator.py`` (which POSTed to a hardcoded Ollama URL)
and ``modules/ranker.py``, and lifts the screening logic out of the old
``app.py`` into one place that the candidate UI, the recruiter dashboard and the
tests all call.

Two structural changes from the MVP:

* Every application belongs to a ``user_id``. Screening is behind auth, so a
  candidate sees only their own result and a recruiter only their company's.
* The ATS pre-filter can end the pipeline on its own. A resume below the role's
  keyword floor is rejected without a model call — that is what makes the filter
  worth having, and it keeps the free Gemini quota for resumes worth reading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from core import db
from core import resume as resume_core
from core.config import settings
from llm import get_llm
from services import auth_service as auth
from services import catalog_service as catalog
from services import guardrail_service as guardrails

log = logging.getLogger(__name__)

STATUSES = ("under_review", "rejected", "shortlisted")
CRITERIA = ("skill_match", "experience", "projects", "communication", "culture_fit")
MAX_CRITERION = 5
MAX_LLM_SCORE = len(CRITERIA) * MAX_CRITERION  # 25

# Long resumes are truncated rather than refused: 18k characters is roughly six
# pages, past which the tail is education history that changes no score.
MAX_RESUME_CHARS = 18_000

# Wording matches ``ui.theme.STATUS_TONES`` exactly: a candidate must not be told
# "Not selected" on one screen and "Not progressing" on the next.
_STATUS_LABELS = {
    "under_review": "Under review",
    "rejected": "Not selected",
    "shortlisted": "Shortlisted",
}


class ApplicationError(Exception):
    """A user-facing problem with an application."""


@dataclass(frozen=True)
class Application:
    id: int
    user_id: int
    role_id: str
    candidate_name: str
    status: str
    ats_score: int
    llm_score: int
    max_score: int
    alignment_score: float
    reason: str
    resume_text: str
    resume_path: str = ""
    resume_sha256: str = ""
    ats_matched: list[str] = field(default_factory=list)
    ats_missing: list[str] = field(default_factory=list)
    criteria: dict[str, int] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    screening_flags: list[str] = field(default_factory=list)
    is_sandbox: bool = False
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_shortlisted(self) -> bool:
        return self.status == "shortlisted"

    @property
    def is_rejected(self) -> bool:
        return self.status == "rejected"

    @property
    def status_label(self) -> str:
        return _STATUS_LABELS.get(self.status, self.status.replace("_", " ").title())

    @property
    def screened(self) -> bool:
        """Whether a model has looked at this resume yet.

        Criteria only, not ``reason``: an ATS rejection writes a reason without
        any model having read the resume, and a recruiter needs to tell the two
        apart before trusting the number.
        """
        return bool(self.criteria)

    @property
    def score_percent(self) -> int:
        if not self.max_score:
            return 0
        return round(self.llm_score / self.max_score * 100)


def _to_application(row: Any) -> Application:
    return Application(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        role_id=row["role_id"],
        candidate_name=row["candidate_name"] or "",
        status=row["status"] or "under_review",
        ats_score=int(row["ats_score"] or 0),
        llm_score=int(row["llm_score"] or 0),
        max_score=int(row["max_score"] or MAX_LLM_SCORE),
        alignment_score=float(row["alignment_score"] or 0.0),
        reason=row["reason"] or "",
        resume_text=row["resume_text"] or "",
        resume_path=row["resume_path"] or "",
        resume_sha256=row["resume_sha256"] or "",
        ats_matched=db.loads(row["ats_matched"], []) or [],
        ats_missing=db.loads(row["ats_missing"], []) or [],
        criteria=db.loads(row["criteria"], {}) or {},
        strengths=db.loads(row["strengths"], []) or [],
        weaknesses=db.loads(row["weaknesses"], []) or [],
        screening_flags=db.loads(row["screening_flags"], []) or [],
        is_sandbox=bool(row["is_sandbox"]),
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get(application_id: int) -> Application | None:
    row = db.query_one("SELECT * FROM applications WHERE id = ?", (application_id,))
    return None if row is None else _to_application(row)


def latest_for(user_id: int, role_id: str) -> Application | None:
    """The one application a user has for a role — the pair is unique."""
    row = db.query_one(
        "SELECT * FROM applications WHERE user_id = ? AND role_id = ?",
        (user_id, role_id),
    )
    return None if row is None else _to_application(row)


def for_user(user_id: int) -> list[Application]:
    rows = db.query(
        "SELECT * FROM applications WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    return [_to_application(row) for row in rows]


def applied_role_ids(user_id: int) -> set[str]:
    """Roles this user has already applied to — for greying out the apply form."""
    rows = db.query("SELECT role_id FROM applications WHERE user_id = ?", (user_id,))
    return {row["role_id"] for row in rows}


def ranked_for_role(role_id: str, *, include_sandbox: bool = False) -> list[Application]:
    """Candidates for a role, best first.

    Ports ``modules/ranker.py``: LLM score, then ATS score. The extra
    ``created_at`` tiebreak is new — the old in-memory sort left equal
    candidates in whatever order the JSON files happened to load, so a
    recruiter's shortlist reordered itself between runs.
    """
    sql = "SELECT * FROM applications WHERE role_id = ?"
    if not include_sandbox:
        sql += " AND is_sandbox = 0"
    sql += " ORDER BY llm_score DESC, ats_score DESC, created_at ASC"
    return [_to_application(row) for row in db.query(sql, (role_id,))]


def for_company(company_id: str, *, include_sandbox: bool = False) -> list[Application]:
    sql = (
        "SELECT a.* FROM applications a JOIN roles r ON r.id = a.role_id "
        "WHERE r.company_id = ?"
    )
    if not include_sandbox:
        sql += " AND a.is_sandbox = 0"
    sql += " ORDER BY a.llm_score DESC, a.ats_score DESC, a.created_at ASC"
    return [_to_application(row) for row in db.query(sql, (company_id,))]


def counts_for_role(role_id: str, *, include_sandbox: bool = False) -> dict[str, int]:
    sql = "SELECT status, COUNT(*) AS n FROM applications WHERE role_id = ?"
    if not include_sandbox:
        sql += " AND is_sandbox = 0"
    sql += " GROUP BY status"
    counts = {status: 0 for status in STATUSES}
    for row in db.query(sql, (role_id,)):
        counts[row["status"]] = int(row["n"])
    return counts


# --------------------------------------------------------------------------- #
# Apply — everything here is offline
# --------------------------------------------------------------------------- #


def apply(
    user: auth.User,
    role_id: str,
    resume: resume_core.Resume,
    *,
    is_sandbox: bool | None = None,
) -> Application:
    """Record an application and run the free part of the pipeline.

    Sanitising the resume, keyword scoring and the insert all happen without a
    network call. :func:`screen` makes the model call separately, so a throttled
    or offline provider can never lose a candidate's submission.
    """
    role = catalog.get_role(role_id)
    if role is None:
        raise ApplicationError("That role is no longer listed.")
    if not role.is_open:
        raise ApplicationError(f"{role.title} is closed to new applications.")

    existing = latest_for(user.id, role_id)
    if existing is not None and existing.is_shortlisted:
        # Replacing the resume now would invalidate a question plan that has
        # already been generated from it, and perhaps an interview in progress.
        raise ApplicationError(
            "You are already shortlisted for this role — check your interview invite."
        )

    verdict = guardrails.sanitize_resume(resume.text)
    text = verdict.text[:MAX_RESUME_CHARS]
    ats = resume_core.ats_score(text, role.requirements)
    name = user.full_name.strip() or resume_core.guess_name(text)
    sandbox = user.is_sandbox if is_sandbox is None else is_sandbox
    now = db.utc_now_iso()

    db.execute(
        """
        INSERT INTO applications(
            user_id, role_id, candidate_name, resume_path, resume_text,
            resume_sha256, ats_score, ats_matched, ats_missing, screening_flags,
            status, is_sandbox, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,'under_review',?,?,?)
        ON CONFLICT(user_id, role_id) DO UPDATE SET
            candidate_name  = excluded.candidate_name,
            resume_path     = excluded.resume_path,
            resume_text     = excluded.resume_text,
            resume_sha256   = excluded.resume_sha256,
            ats_score       = excluded.ats_score,
            ats_matched     = excluded.ats_matched,
            ats_missing     = excluded.ats_missing,
            screening_flags = excluded.screening_flags,
            status          = 'under_review',
            updated_at      = excluded.updated_at,
            -- A new resume invalidates the evaluation of the old one.
            llm_score       = 0,
            alignment_score = 0,
            criteria        = '{}',
            strengths       = '[]',
            weaknesses      = '[]',
            reason          = ''
        """,
        (
            user.id,
            role_id,
            name,
            resume.stored_at,
            text,
            resume.sha256,
            ats.score,
            db.dumps(ats.matched),
            db.dumps(ats.missing),
            db.dumps(list(verdict.flags)),
            1 if sandbox else 0,
            now,
            now,
        ),
    )
    db.audit(
        user.id,
        "application.submit",
        role=role_id,
        ats=ats.score,
        words=resume.words,
        flags=list(verdict.flags),
        replaced=existing is not None,
    )
    application = latest_for(user.id, role_id)
    if application is None:  # pragma: no cover - the insert just succeeded
        raise ApplicationError("The application could not be saved. Please try again.")
    return application


def submit(
    user: auth.User,
    role_id: str,
    resume: resume_core.Resume,
    *,
    is_sandbox: bool | None = None,
) -> Application:
    """Apply and screen in one call — what the candidate UI wants."""
    application = apply(user, role_id, resume, is_sandbox=is_sandbox)
    return screen(application.id)


# --------------------------------------------------------------------------- #
# Screen — the one step that calls a model
# --------------------------------------------------------------------------- #


def decide_status(
    ats_score: int, llm_score: int, role: catalog.Role, *, ignore_ats: bool = False
) -> str:
    """Map the two scores onto a status.

    Nothing is auto-rejected on the model's judgement: below the shortlist floor
    an application waits for a human. Only the keyword filter rejects outright,
    and it rejects on a rule the candidate can read for themselves.

    ``ignore_ats`` is the recruiter's override. Without it a forced re-screen
    would spend the model call and then be stamped ``rejected`` by the very floor
    the recruiter asked to look past.
    """
    if not ignore_ats and ats_score < role.ats_floor:
        return "rejected"
    if llm_score >= role.shortlist_floor:
        return "shortlisted"
    return "under_review"


def _reject_on_ats(application: Application, role: catalog.Role) -> Application:
    """End the pipeline before any model call, and say why in plain words."""
    missing = ", ".join(application.ats_missing[:6]) or "the listed requirements"
    reason = (
        f"Keyword match {application.ats_score}% is below the {role.ats_floor}% "
        f"needed for {role.title}. Not evidenced in the resume: {missing}."
    )
    db.execute(
        "UPDATE applications SET status = 'rejected', reason = ?, updated_at = ? "
        "WHERE id = ?",
        (reason, db.utc_now_iso(), application.id),
    )
    db.audit(
        application.user_id,
        "application.reject_ats",
        application=application.id,
        ats=application.ats_score,
        floor=role.ats_floor,
    )
    return get(application.id)  # type: ignore[return-value]


def screen(application_id: int, *, force: bool = False) -> Application:
    """Score a saved application and set its status.

    ``force`` skips the ATS floor: that is how a recruiter asks for a second look
    at a resume the keyword filter threw out.
    """
    application = get(application_id)
    if application is None:
        raise ApplicationError("That application no longer exists.")
    pair = catalog.role_with_company(application.role_id)
    if pair is None:
        raise ApplicationError("The role for that application is missing.")
    role, company = pair

    if application.ats_score < role.ats_floor and not force:
        return _reject_on_ats(application, role)

    try:
        payload = get_llm().generate_json(
            _build_prompt(role, company, application),
            schema=_EVAL_SCHEMA,
            temperature=0.0,
            max_output_tokens=1200,
        )
    except Exception as exc:  # provider down, quota gone, JSON unusable twice
        log.warning("Evaluation failed for application %s: %s", application_id, exc)
        # The row is left exactly as it was, so a retry is idempotent and the
        # candidate's submission is not lost.
        raise ApplicationError(
            "Your application was saved, but the AI reviewer is unavailable right "
            "now. It will be scored shortly — there is no need to apply again."
        ) from exc

    evaluation = _normalize_evaluation(payload, fallback_name=application.candidate_name)
    status = decide_status(
        application.ats_score, evaluation["total_score"], role, ignore_ats=force
    )

    db.execute(
        """
        UPDATE applications
           SET candidate_name  = ?,
               llm_score       = ?,
               max_score       = ?,
               alignment_score = ?,
               criteria        = ?,
               strengths       = ?,
               weaknesses      = ?,
               reason          = ?,
               status          = ?,
               updated_at      = ?
         WHERE id = ?
        """,
        (
            evaluation["candidate_name"] or application.candidate_name,
            evaluation["total_score"],
            MAX_LLM_SCORE,
            evaluation["alignment_score"],
            db.dumps(evaluation["criteria"]),
            db.dumps(evaluation["strengths"]),
            db.dumps(evaluation["weaknesses"]),
            evaluation["reason"],
            status,
            db.utc_now_iso(),
            application_id,
        ),
    )
    db.audit(
        application.user_id,
        "application.screen",
        application=application_id,
        role=application.role_id,
        ats=application.ats_score,
        llm_score=evaluation["total_score"],
        status=status,
    )
    log.info(
        "Screened application %s for %s: %s (%s/%s)",
        application_id,
        application.role_id,
        status,
        evaluation["total_score"],
        MAX_LLM_SCORE,
    )
    screened = get(application_id)
    if screened is not None and screened.is_shortlisted:
        _prepare_interview(screened)
    return screened  # type: ignore[return-value]


def _prepare_interview(application: Application) -> None:
    """Create the interview and pre-generate its questions. Best effort.

    Imported here rather than at the top of the file: ``interview_service`` reads
    applications, so a module-level import in both directions is a cycle. The
    failure is logged and swallowed — being shortlisted is the news the candidate
    is waiting for, and a missing plan is rebuilt when they open the room.
    """
    from services import interview_service

    try:
        interview_service.ensure_for_application(application)
    except Exception as exc:  # a provider outage must not unshortlist anyone
        log.warning(
            "Interview setup deferred for application %s: %s", application.id, exc
        )


def set_status(
    application_id: int,
    status: str,
    *,
    actor_id: int | None = None,
    note: str = "",
) -> Application:
    """Recruiter override. The audit log keeps the previous value."""
    if status not in STATUSES:
        raise ApplicationError(f"Unknown status {status!r}.")
    application = get(application_id)
    if application is None:
        raise ApplicationError("That application no longer exists.")
    db.execute(
        "UPDATE applications SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
        (status, note.strip() or application.reason, db.utc_now_iso(), application_id),
    )
    db.audit(
        actor_id,
        "application.set_status",
        application=application_id,
        was=application.status,
        now=status,
    )
    updated = get(application_id)
    if updated is not None and updated.is_shortlisted:
        # A recruiter overriding an "under review" into a shortlist owes the
        # candidate the same interview an automatic shortlist would have created.
        _prepare_interview(updated)
    return updated  # type: ignore[return-value]


def withdraw(application_id: int, *, user_id: int) -> None:
    """A candidate deleting their own application. Cascades to the interview."""
    owner = db.scalar("SELECT user_id FROM applications WHERE id = ?", (application_id,))
    if owner is None or int(owner) != user_id:
        raise ApplicationError("That application does not belong to you.")
    db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
    db.audit(user_id, "application.withdraw", application=application_id)


# --------------------------------------------------------------------------- #
# Model plumbing
# --------------------------------------------------------------------------- #

_EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_name": {"type": "string"},
        "criteria": {
            "type": "object",
            "properties": {name: {"type": "integer"} for name in CRITERIA},
            "required": list(CRITERIA),
        },
        "total_score": {"type": "integer"},
        "alignment_score": {"type": "number"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": [
        "candidate_name",
        "criteria",
        "total_score",
        "alignment_score",
        "strengths",
        "weaknesses",
        "reason",
    ],
}


@lru_cache(maxsize=4)
def _template(name: str) -> str:
    path = settings.prompts_dir / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApplicationError(f"Prompt template {name} is missing.") from exc


def _build_prompt(
    role: catalog.Role, company: catalog.Company, application: Application
) -> str:
    """Fill the template with ``replace``, not ``format``.

    The template ends with a literal JSON example, and every brace in it would
    have to be doubled for ``str.format`` — one missed pair and the prompt raises
    at runtime instead of failing a review.
    """
    missing = "\n".join(f"- {req}" for req in application.ats_missing) or "- (none)"
    replacements = {
        "{role_title}": role.title,
        "{company_name}": company.name,
        "{job_description}": role.job_description or "(not provided)",
        "{requirements}": role.requirements_line or "(none listed)",
        "{culture}": company.culture_line or "(not documented)",
        "{values}": ", ".join(company.core_values) or "(not documented)",
        "{missing_requirements}": missing,
        "{resume_text}": application.resume_text[:MAX_RESUME_CHARS],
    }
    prompt = _template("resume_evaluation.txt")
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _string_list(value: Any, *, limit: int = 4) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit]


def _normalize_evaluation(payload: Any, *, fallback_name: str = "") -> dict[str, Any]:
    """Coerce a model reply into exactly what the columns expect.

    The total is recomputed from the five criteria rather than trusted. Models —
    small local ones especially — routinely return five scores and a sum that
    does not match them, and a recruiter comparing two candidates needs the
    number to mean one thing. ``alignment_score`` is derived for the same reason.

    ``fallback_name`` wins over the model's extraction, not the other way round:
    the name already on the row came from the account the candidate registered,
    and a model reading a resume can just as easily return a referee's name.
    """
    data = payload if isinstance(payload, dict) else {}
    raw = data.get("criteria")
    raw = raw if isinstance(raw, dict) else {}
    criteria = {
        name: _clamp_int(raw.get(name, 0), 0, MAX_CRITERION) for name in CRITERIA
    }
    total = sum(criteria.values())
    name = str(fallback_name or data.get("candidate_name") or "").strip()
    return {
        "candidate_name": name[:120],
        "criteria": criteria,
        "total_score": total,
        "alignment_score": round(total / MAX_LLM_SCORE, 2),
        "strengths": _string_list(data.get("strengths")),
        "weaknesses": _string_list(data.get("weaknesses")),
        "reason": str(data.get("reason") or "").strip()[:600],
    }
