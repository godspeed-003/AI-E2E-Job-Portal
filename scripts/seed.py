"""Fill an empty database with something worth looking at.

``streamlit run app.py`` on a fresh clone works, but it lands you on a login
screen with one admin account and five roles nobody has applied to. Every
screen that matters — the pipeline, the ranking, a transcript, an integrity
report — is empty until someone spends twenty minutes applying as several
candidates and sitting an interview. That is a bad first five minutes, and it
is the reason this script exists.

    python scripts/seed.py                 # accounts, roles, ranked candidates
    python scripts/seed.py --offline       # same, no API calls, no key needed
    python scripts/seed.py --candidates 6  # a longer shortlist to rank
    python scripts/seed.py --reset         # delete what this script created

**Demo accounts are real accounts, not sandbox rows.** Sandbox data is hidden
from the recruiter pipeline by default, which is exactly right for a persona an
admin minted to test with, and exactly wrong for a demo whose whole point is
that the pipeline has people in it. The consequence is that ``--reset`` deletes
real rows, so it only ever touches the ``@demo.local`` domain and says what it
is about to remove.

**Screening costs money by default.** Ranking candidates means asking a model to
read resumes — that is the product. With ``GEMINI_API_KEY`` set, seeding N
candidates is N evaluation calls. ``--offline`` swaps in the fake provider used
by the test suite: the scores are canned and the shape of every screen is
identical, which is all a demo or a UI change needs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402
from core import resume as resume_core  # noqa: E402
from core.config import settings  # noqa: E402
from services import application_service as apps  # noqa: E402
from services import auth_service as auth  # noqa: E402
from services import catalog_service as catalog  # noqa: E402
from services import interview_service as interviews  # noqa: E402

DEMO_DOMAIN = "demo.local"
DEMO_PASSWORD = "demo-portal-1234"

# Each candidate names the roles they apply to. Matching the shipped roles'
# requirement keywords is not decoration: the keyword floor rejects a resume
# scoring under ATS_REJECT_BELOW *before* any model reads it, so a demo built
# from generically-worded resumes shows five rejections and an empty pipeline.
#
# The spread is deliberate. Tom applies to a backend role he is plainly wrong
# for, because a pipeline where everyone is shortlisted demonstrates nothing —
# a reviewer needs to see the rejection path too.
CANDIDATES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "maya",
        "Maya Fernandes",
        """Backend engineer, 5 years. Python and Java services on AWS, with
        Postgres and Kafka behind them. Designed and owns the APIs for a payments
        platform handling 4k requests per second; led its system design review
        after the monolith split. Migrated a monolithic billing service to
        event-driven workers, cutting p99 latency from 900 ms to 120 ms. Deep
        with relational databases: partitioning, query plans, replication lag.
        Writes SQL daily. Wrote the reconciliation service the finance team
        closes the month on. Mentors two juniors.""",
        ("amazon_backend_dev", "amazon_data_engineer"),
    ),
    (
        "arjun",
        "Arjun Mehta",
        """Data engineer, 3 years. Builds ETL in Python and SQL: Airflow DAGs over
        Snowflake, dbt models for the analytics team, Pandas for everything that
        does not justify a cluster. Rebuilt a nightly batch that kept missing its
        window into an incremental pipeline finishing in eleven minutes. AWS
        basics day to day — S3, Glue, Lambda. Owns the data quality checks that
        gate the warehouse.""",
        ("amazon_data_engineer",),
    ),
    (
        "sofia",
        "Sofia Lindqvist",
        """Full-stack developer, 6 years. TypeScript and React on the front, Python
        and Java services behind it. Ran the API platform team at a 40-person
        startup: REST and gRPC APIs, Postgres and Redis databases, Docker and
        Kubernetes. Distributed systems coursework and a Raft implementation for
        fun; comfortable leading a system design conversation. Some SQL-heavy ETL
        work when the data team was short-handed.""",
        ("amazon_backend_dev",),
    ),
    (
        "tom",
        "Tom Whelan",
        """Marketing executive, 4 years. Runs content and SEO for a B2B SaaS:
        owns the editorial calendar, the keyword strategy and the campaigns
        behind each launch. Grew organic traffic 3x in eighteen months. Manages
        social media across LinkedIn and X, and the paid campaigns that support
        them. Reports on attribution to the leadership team. Teaching myself
        Python on the side and curious about engineering.""",
        ("deloitte_marketing", "amazon_backend_dev"),
    ),
    (
        "priyanka",
        "Priyanka Bose",
        """Platform engineer, 8 years. Python, Java, Kubernetes, Terraform.
        Designed the multi-region failover for a payments platform and ran the
        incident review process afterwards. Builds internal APIs the product
        teams depend on. Deep Postgres and MySQL databases work: partitioning,
        query plans, replication lag. Regular system design interviewer; speaks
        at conferences on distributed systems. SQL fluent.""",
        ("amazon_backend_dev",),
    ),
    (
        "hana",
        "Hana Okafor",
        """HR generalist, 4 years, for a 200-person consultancy. Owns recruitment
        end to end: graduate intake, interview panels, offers. Strong written and
        verbal communication — writes the handbook and runs the all-hands Q&A.
        Handles conflict resolution between teams and the follow-up documentation.
        Coordination across three offices; monthly reporting to the partners with
        the attention to detail that implies. HRIS tooling and compensation
        benchmarking.""",
        ("deloitte_hr", "deloitte_operations"),
    ),
)


def _say(message: str) -> None:
    print(message, flush=True)


def _utf8_stdout() -> None:
    """Stop a Windows console from killing the run over a dash.

    ``python scripts/seed.py`` on Windows gets a cp1252 stdout, and printing an
    em-dash or an arrow raises ``UnicodeEncodeError`` mid-seed — leaving a
    half-populated database and a traceback that has nothing to do with seeding.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass  # already UTF-8, or not a real console


def _email(handle: str) -> str:
    return f"{handle}@{DEMO_DOMAIN}"


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def seed_catalog() -> None:
    counts = catalog.seed_from_json()
    roles = catalog.list_roles()
    _say(
        f"  catalog     {counts.get('companies', 0)} companies, "
        f"{counts.get('roles', 0)} roles inserted "
        f"({len(roles)} roles present)"
    )


def seed_recruiters() -> list[auth.User]:
    """One hiring account per company, so every role has an owner who can see it."""
    made: list[auth.User] = []
    invite = settings.auth.recruiter_invite_code
    if not invite:
        _say(
            "  recruiters  skipped — RECRUITER_INVITE_CODE is unset, and hiring "
            "accounts are gated on it. Set it in .env and re-run."
        )
        return made

    for company in catalog.list_companies():
        email = _email(f"hr.{company.id}")
        if auth.get_user_by_email(email):
            continue
        made.append(
            auth.register(
                email,
                DEMO_PASSWORD,
                full_name=f"{company.name} Hiring",
                role="recruiter",
                company_id=company.id,
                invite_code=invite,
            )
        )
    _say(f"  recruiters  {len(made)} created, one per company")
    return made


def seed_candidates(limit: int) -> list[auth.User]:
    made: list[auth.User] = []
    for handle, name, _text, _roles in CANDIDATES[:limit]:
        email = _email(handle)
        existing = auth.get_user_by_email(email)
        if existing:
            made.append(existing)
            continue
        made.append(auth.register(email, DEMO_PASSWORD, full_name=name))
    _say(f"  candidates  {len(made)} accounts")
    return made


def seed_applications(candidates: list[auth.User], limit: int) -> int:
    """Apply each candidate to the roles they named, and screen them.

    Screening is the step that calls a model, so this is where ``--offline``
    earns its place.
    """
    targets = {
        handle: (text, roles) for handle, _name, text, roles in CANDIDATES[:limit]
    }

    submitted = 0
    for user in candidates:
        handle = user.email.split("@")[0]
        entry = targets.get(handle)
        if entry is None:
            continue
        text, role_ids = entry
        for role_id in role_ids:
            role = catalog.get_role(role_id)
            if role is None:
                # The catalogue is editable, so a shipped id may have been
                # renamed. Skipping beats seeding against a guess.
                _say(f"    ! {role_id} is not in the catalogue; skipped")
                continue
            if apps.latest_for(user.id, role.id) is not None:
                continue
            try:
                application = apps.submit(
                    user, role.id, resume_core.from_text(" ".join(text.split()))
                )
            except Exception as exc:
                _say(f"    ! {user.email} -> {role.id}: {exc}")
                continue
            submitted += 1
            _say(
                f"    {user.full_name or user.email} -> {role.title}: "
                f"ats {application.ats_score}, model {application.llm_score}/25, "
                f"{application.status}"
            )
    _say(f"  applications  {submitted} screened")
    return submitted


def report(admin: auth.User | None) -> None:
    shortlisted = db.query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE status = 'shortlisted'"
    )
    pending = db.query_one(
        "SELECT COUNT(*) AS n FROM interviews WHERE status = 'pending'"
    )
    _say("")
    _say("Sign in at http://localhost:8501 with any of:")
    for user in auth.list_users():
        if not user.email.endswith(f"@{DEMO_DOMAIN}"):
            continue
        if admin is not None and user.id == admin.id:
            # The bootstrap admin's password is ADMIN_PASSWORD from .env, not
            # this script's. Printing DEMO_PASSWORD next to it would send the
            # first person here straight into a failed login.
            _say(f"  {user.role:9} {user.email}  /  your ADMIN_PASSWORD from .env")
            continue
        _say(f"  {user.role:9} {user.email}  /  {DEMO_PASSWORD}")
    _say("")
    _say(
        f"{shortlisted['n'] if shortlisted else 0} shortlisted candidate(s), "
        f"{pending['n'] if pending else 0} interview(s) waiting to be sat."
    )
    _say("Sign in as a candidate to sit one; as a recruiter to read the pipeline.")


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #


def reset(*, assume_yes: bool) -> int:
    """Delete every ``@demo.local`` account and everything hanging off it."""
    rows = db.query(
        "SELECT id, email FROM users WHERE email LIKE ?", (f"%@{DEMO_DOMAIN}",)
    )
    if not rows:
        _say("Nothing to remove: no demo accounts found.")
        return 0

    _say(f"About to delete {len(rows)} demo account(s) and all their data:")
    for row in rows:
        _say(f"  {row['email']}")
    if not assume_yes:
        answer = input("Type 'delete' to confirm: ").strip().lower()
        if answer != "delete":
            _say("Cancelled.")
            return 0

    # Applications, interviews, turns and proctoring events cascade from users.
    for row in rows:
        db.execute("DELETE FROM users WHERE id = ?", (row["id"],))
    _say(f"Removed {len(rows)} demo account(s).")
    return len(rows)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed",
        description="Populate the portal with demo accounts, roles and candidates.",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=4,
        metavar="N",
        help=f"how many demo candidates to screen (max {len(CANDIDATES)}, default 4)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use the fake model provider: no API key, no calls, canned scores",
    )
    parser.add_argument(
        "--accounts-only",
        action="store_true",
        help="create accounts and roles but do not screen anyone (no model calls)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=f"delete every @{DEMO_DOMAIN} account and its applications",
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt on --reset"
    )
    args = parser.parse_args(argv)

    _utf8_stdout()
    settings.ensure_dirs()
    db.init_db()

    if args.reset:
        return 0 if reset(assume_yes=args.yes) >= 0 else 1

    if args.offline:
        from llm import set_provider
        from llm.fake import FakeProvider

        set_provider(FakeProvider())
        _say("Using the fake provider: scores are canned, nothing leaves the machine.")
    elif not args.accounts_only:
        _say(f"Screening with the '{settings.llm.provider}' provider.")

    _say("Seeding:")
    admin = auth.ensure_admin_account()
    _say(
        f"  admin       {admin.email}"
        if admin
        else "  admin       skipped — set ADMIN_EMAIL and ADMIN_PASSWORD in .env"
    )
    seed_catalog()
    seed_recruiters()

    limit = max(0, min(args.candidates, len(CANDIDATES)))
    candidates = seed_candidates(limit)
    if not args.accounts_only:
        seed_applications(candidates, limit)

    report(admin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
