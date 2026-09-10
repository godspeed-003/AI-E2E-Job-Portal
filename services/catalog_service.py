"""Companies and roles.

The MVP read ``data/companies.json`` and ``data/roles.json`` on every rerun. They
are now seeded into SQLite so a recruiter can edit a job description, move a
threshold or close a role without touching a file — while the JSON stays as the
shipped default so a fresh clone has something to demo with.

Per-role overrides fall back to the global ``.env`` defaults, so a role only
stores the settings a recruiter actually changed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core import db
from core.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    type: str = ""
    culture: list[str] = field(default_factory=list)
    core_values: list[str] = field(default_factory=list)

    @property
    def culture_line(self) -> str:
        return ", ".join(self.culture)


@dataclass(frozen=True)
class Role:
    id: str
    company_id: str
    title: str
    job_description: str = ""
    requirements: list[str] = field(default_factory=list)
    is_open: bool = True
    ats_reject_below: int | None = None
    shortlist_llm_score_min: int | None = None
    planned_questions: int | None = None
    interview_duration_minutes: int | None = None
    interview_window_days: int | None = None

    # -- effective settings: role override, else the global default ---------- #

    @property
    def ats_floor(self) -> int:
        if self.ats_reject_below is None:
            return settings.screening.ats_reject_below
        return self.ats_reject_below

    @property
    def shortlist_floor(self) -> int:
        if self.shortlist_llm_score_min is None:
            return settings.screening.shortlist_llm_score_min
        return self.shortlist_llm_score_min

    @property
    def question_budget(self) -> int:
        if self.planned_questions is None:
            return settings.interview.planned_questions
        return self.planned_questions

    @property
    def duration_minutes(self) -> int:
        if self.interview_duration_minutes is None:
            return settings.interview.duration_minutes
        return self.interview_duration_minutes

    @property
    def window_days(self) -> int:
        if self.interview_window_days is None:
            return settings.interview.window_days
        return self.interview_window_days

    @property
    def requirements_line(self) -> str:
        return ", ".join(self.requirements)


def _to_company(row: Any) -> Company:
    return Company(
        id=row["id"],
        name=row["name"],
        type=row["type"] or "",
        culture=db.loads(row["culture"], []) or [],
        core_values=db.loads(row["core_values"], []) or [],
    )


def _to_role(row: Any) -> Role:
    return Role(
        id=row["id"],
        company_id=row["company_id"],
        title=row["title"],
        job_description=row["job_description"] or "",
        requirements=db.loads(row["requirements"], []) or [],
        is_open=bool(row["is_open"]),
        ats_reject_below=row["ats_reject_below"],
        shortlist_llm_score_min=row["shortlist_llm_score_min"],
        planned_questions=row["planned_questions"],
        interview_duration_minutes=row["interview_duration_minutes"],
        interview_window_days=row["interview_window_days"],
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def list_companies() -> list[Company]:
    return [_to_company(row) for row in db.query("SELECT * FROM companies ORDER BY name")]


def get_company(company_id: str) -> Company | None:
    row = db.query_one("SELECT * FROM companies WHERE id = ?", (company_id,))
    return None if row is None else _to_company(row)


def list_roles(
    *, company_id: str | None = None, only_open: bool = False
) -> list[Role]:
    clauses: list[str] = []
    params: list[Any] = []
    if company_id:
        clauses.append("company_id = ?")
        params.append(company_id)
    if only_open:
        clauses.append("is_open = 1")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(f"SELECT * FROM roles{where} ORDER BY company_id, title", params)
    return [_to_role(row) for row in rows]


def get_role(role_id: str) -> Role | None:
    row = db.query_one("SELECT * FROM roles WHERE id = ?", (role_id,))
    return None if row is None else _to_role(row)


def role_with_company(role_id: str) -> tuple[Role, Company] | None:
    role = get_role(role_id)
    if role is None:
        return None
    company = get_company(role.company_id) or Company(id=role.company_id, name=role.company_id)
    return role, company


def company_names() -> dict[str, str]:
    """``{company_id: name}`` for select boxes."""
    return {company.id: company.name for company in list_companies()}


def role_counts() -> dict[str, int]:
    rows = db.query(
        "SELECT company_id, COUNT(*) AS n FROM roles WHERE is_open = 1 GROUP BY company_id"
    )
    return {row["company_id"]: int(row["n"]) for row in rows}


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug or "item"


def upsert_company(
    company_id: str,
    name: str,
    *,
    type_: str = "",
    culture: list[str] | None = None,
    core_values: list[str] | None = None,
) -> Company:
    db.execute(
        """
        INSERT INTO companies(id, name, type, culture, core_values)
        VALUES(?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            culture = excluded.culture,
            core_values = excluded.core_values
        """,
        (
            company_id,
            name,
            type_,
            db.dumps(culture or []),
            db.dumps(core_values or []),
        ),
    )
    return get_company(company_id)  # type: ignore[return-value]


def upsert_role(
    role_id: str,
    company_id: str,
    title: str,
    *,
    job_description: str = "",
    requirements: list[str] | None = None,
    is_open: bool = True,
    ats_reject_below: int | None = None,
    shortlist_llm_score_min: int | None = None,
    planned_questions: int | None = None,
    interview_duration_minutes: int | None = None,
    interview_window_days: int | None = None,
) -> Role:
    db.execute(
        """
        INSERT INTO roles(id, company_id, title, job_description, requirements,
                          is_open, ats_reject_below, shortlist_llm_score_min,
                          planned_questions, interview_duration_minutes,
                          interview_window_days, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            company_id = excluded.company_id,
            title = excluded.title,
            job_description = excluded.job_description,
            requirements = excluded.requirements,
            is_open = excluded.is_open,
            ats_reject_below = excluded.ats_reject_below,
            shortlist_llm_score_min = excluded.shortlist_llm_score_min,
            planned_questions = excluded.planned_questions,
            interview_duration_minutes = excluded.interview_duration_minutes,
            interview_window_days = excluded.interview_window_days
        """,
        (
            role_id,
            company_id,
            title,
            job_description,
            db.dumps(requirements or []),
            1 if is_open else 0,
            ats_reject_below,
            shortlist_llm_score_min,
            planned_questions,
            interview_duration_minutes,
            interview_window_days,
            db.utc_now_iso(),
        ),
    )
    return get_role(role_id)  # type: ignore[return-value]


def set_role_open(role_id: str, is_open: bool) -> None:
    db.execute("UPDATE roles SET is_open = ? WHERE id = ?", (1 if is_open else 0, role_id))


# --------------------------------------------------------------------------- #
# Seeding from the shipped JSON
# --------------------------------------------------------------------------- #


def _read_json(name: str) -> list[dict[str, Any]]:
    path = settings.data_dir / name
    if not path.exists():
        log.warning("Seed file %s is missing", path)
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        return []
    return payload if isinstance(payload, list) else []


def seed_from_json(*, overwrite: bool = False) -> dict[str, int]:
    """Load the shipped catalogue into SQLite.

    Idempotent. With ``overwrite=False`` (the default) existing rows are left
    alone, so a recruiter's edits survive an app restart.
    """
    existing_companies = {c.id for c in list_companies()}
    existing_roles = {r.id for r in list_roles()}
    added = {"companies": 0, "roles": 0}

    for entry in _read_json("companies.json"):
        company_id = entry.get("company_id") or entry.get("id")
        if not company_id or (company_id in existing_companies and not overwrite):
            continue
        upsert_company(
            company_id,
            entry.get("name") or company_id.title(),
            type_=entry.get("type", ""),
            culture=entry.get("culture") or [],
            # The JSON calls this "values"; the column is core_values because
            # "values" is a reserved-ish word in too many places to be worth it.
            core_values=entry.get("values") or entry.get("core_values") or [],
        )
        added["companies"] += 1

    for entry in _read_json("roles.json"):
        role_id = entry.get("role_id") or entry.get("id")
        company_id = entry.get("company_id")
        if not role_id or not company_id:
            continue
        if role_id in existing_roles and not overwrite:
            continue
        if get_company(company_id) is None:
            upsert_company(company_id, company_id.title())
        upsert_role(
            role_id,
            company_id,
            entry.get("title") or role_id,
            job_description=entry.get("job_description", ""),
            requirements=entry.get("requirements") or [],
        )
        added["roles"] += 1

    if added["companies"] or added["roles"]:
        log.info("Seeded catalogue: %s", added)
    return added
