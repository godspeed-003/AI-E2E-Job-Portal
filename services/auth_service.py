"""Authentication, sessions and role checks.

Design notes:

* Passwords are scrypt hashed (see :mod:`core.security`).
* Session tokens are random 256-bit strings; only their SHA-256 is stored, so a
  stolen database cannot be replayed as a live login.
* Failed logins are counted per email and throttled, which is the one attack
  every public portal actually sees.
* Sandbox accounts are ordinary rows with ``is_sandbox = 1``. Everything the
  admin does while testing is therefore deletable in one statement, and real
  candidate data is never mixed into a demo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from core import db, security
from core.config import settings

log = logging.getLogger(__name__)

Role = Literal["candidate", "recruiter", "admin"]
ROLES: tuple[Role, ...] = ("candidate", "recruiter", "admin")


class AuthError(Exception):
    """Raised for any user-facing authentication failure."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    role: Role
    full_name: str
    company_id: str | None
    is_active: bool
    is_sandbox: bool

    @property
    def display_name(self) -> str:
        return self.full_name or self.email.split("@")[0]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_recruiter(self) -> bool:
        return self.role in ("recruiter", "admin")

    @property
    def initials(self) -> str:
        parts = [p for p in self.display_name.replace(".", " ").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


def _to_user(row: Any) -> User:
    return User(
        id=int(row["id"]),
        email=row["email"],
        role=row["role"],
        full_name=row["full_name"] or "",
        company_id=row["company_id"],
        is_active=bool(row["is_active"]),
        is_sandbox=bool(row["is_sandbox"]),
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(
    email: str,
    password: str,
    *,
    full_name: str = "",
    role: Role = "candidate",
    company_id: str | None = None,
    invite_code: str | None = None,
    is_sandbox: bool = False,
) -> User:
    email = security.normalize_email(email)
    if not security.is_valid_email(email):
        raise AuthError("Enter a valid email address.")

    problem = security.password_problem(password)
    if problem:
        raise AuthError(problem)

    if role not in ROLES:
        raise AuthError("Unknown account type.")

    # Recruiter and admin accounts are gated: self-service signup would let
    # anyone read every candidate's resume and interview recording.
    if role in ("recruiter", "admin"):
        expected = settings.auth.recruiter_invite_code
        if not expected or (invite_code or "").strip() != expected:
            raise AuthError("That invite code is not valid for a hiring account.")
    if role == "recruiter" and not company_id:
        raise AuthError("Choose the company you are hiring for.")

    if db.query_one("SELECT 1 FROM users WHERE email = ?", (email,)):
        raise AuthError("An account with that email already exists.")

    user_id = db.execute(
        """
        INSERT INTO users(email, password_hash, role, full_name, company_id,
                          is_active, is_sandbox, created_at)
        VALUES(?,?,?,?,?,1,?,?)
        """,
        (
            email,
            security.hash_password(password),
            role,
            (full_name or "").strip(),
            company_id,
            1 if is_sandbox else 0,
            db.utc_now_iso(),
        ),
    )
    db.audit(user_id, "user.register", role=role, sandbox=is_sandbox)
    return get_user(user_id)  # type: ignore[return-value]


def ensure_admin_account() -> User | None:
    """Create the bootstrap admin from ``.env`` on first run.

    Returns ``None`` when no admin password is configured, so a deployment can
    opt out and create the account by hand instead.
    """
    email = settings.auth.admin_email
    password = settings.auth.admin_password
    if not email or not password:
        return None

    existing = db.query_one("SELECT * FROM users WHERE email = ?", (email,))
    if existing:
        return _to_user(existing)

    user_id = db.execute(
        """
        INSERT INTO users(email, password_hash, role, full_name, is_active,
                          is_sandbox, created_at)
        VALUES(?,?,'admin','Administrator',1,0,?)
        """,
        (email, security.hash_password(password), db.utc_now_iso()),
    )
    log.info("Created bootstrap admin account %s", email)
    db.audit(user_id, "user.bootstrap_admin")
    return get_user(user_id)


# --------------------------------------------------------------------------- #
# Login throttling
# --------------------------------------------------------------------------- #

# Verifying against a throwaway hash when the email is unknown keeps the
# response time of "no such user" and "wrong password" comparable, so the login
# form cannot be used to enumerate who has an account.
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = security.hash_password("not-a-real-password")
    return _DUMMY_HASH


def _lockout_cutoff_iso() -> str:
    minutes = max(1, settings.auth.login_lockout_minutes)
    return (db.utc_now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def recent_failures(email: str) -> int:
    return int(
        db.scalar(
            "SELECT COUNT(*) FROM login_attempts "
            "WHERE email = ? AND successful = 0 AND ts >= ?",
            (security.normalize_email(email), _lockout_cutoff_iso()),
        )
        or 0
    )


def _record_attempt(email: str, successful: bool) -> None:
    db.execute(
        "INSERT INTO login_attempts(email, successful, ts) VALUES(?,?,?)",
        (email, 1 if successful else 0, db.utc_now_iso()),
    )


# --------------------------------------------------------------------------- #
# Login / sessions
# --------------------------------------------------------------------------- #


def login(email: str, password: str, *, user_agent: str = "") -> tuple[User, str]:
    """Verify credentials and mint a session. Returns ``(user, raw_token)``.

    The raw token is returned only here; the database keeps its hash.
    """
    email = security.normalize_email(email)

    if recent_failures(email) >= settings.auth.login_max_attempts:
        raise AuthError(
            f"Too many failed attempts. Wait {settings.auth.login_lockout_minutes} "
            "minutes and try again."
        )

    row = db.query_one("SELECT * FROM users WHERE email = ?", (email,))
    stored = row["password_hash"] if row else _dummy_hash()
    verified = security.verify_password(password, stored)
    ok = bool(row) and verified

    _record_attempt(email, ok)
    if not ok:
        raise AuthError("Email or password is incorrect.")
    if not row["is_active"]:  # type: ignore[index]
        raise AuthError("That account has been disabled. Contact the administrator.")

    user = _to_user(row)
    token = create_session(user.id, user_agent=user_agent)
    db.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?", (db.utc_now_iso(), user.id)
    )
    db.audit(user.id, "auth.login", role=user.role)
    return user, token


def create_session(user_id: int, *, user_agent: str = "") -> str:
    token = security.new_session_token()
    expires = db.utc_now() + timedelta(hours=max(1, settings.auth.session_ttl_hours))
    db.execute(
        """
        INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent)
        VALUES(?,?,?,?,?)
        """,
        (
            security.hash_token(token),
            user_id,
            db.utc_now_iso(),
            expires.isoformat(timespec="seconds"),
            (user_agent or "")[:300],
        ),
    )
    return token


def resolve_session(token: str) -> User | None:
    """Return the logged-in user for a raw token, or ``None`` if it is no good.

    Read-only on purpose: this runs on every Streamlit rerun, and a write per
    rerun would contend with the interview's turn-by-turn inserts.
    """
    if not token:
        return None
    row = db.query_one(
        """
        SELECT u.*, s.expires_at AS s_expires_at, s.revoked_at AS s_revoked_at
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ?
        """,
        (security.hash_token(token),),
    )
    if row is None or row["s_revoked_at"]:
        return None
    expires = db.parse_ts(row["s_expires_at"])
    if expires is None or expires <= db.utc_now():
        return None
    if not row["is_active"]:
        return None
    return _to_user(row)


def logout(token: str) -> None:
    if not token:
        return
    token_hash = security.hash_token(token)
    user_id = db.scalar("SELECT user_id FROM sessions WHERE token_hash = ?", (token_hash,))
    db.execute(
        "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (db.utc_now_iso(), token_hash),
    )
    if user_id is not None:
        db.audit(int(user_id), "auth.logout")


def logout_all(user_id: int) -> int:
    """Revoke every live session for a user (password change, admin action)."""
    live = db.query(
        "SELECT token_hash FROM sessions WHERE user_id = ? AND revoked_at IS NULL",
        (user_id,),
    )
    db.execute(
        "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (db.utc_now_iso(), user_id),
    )
    db.audit(user_id, "auth.logout_all", sessions=len(live))
    return len(live)


def purge_expired_sessions() -> int:
    """Housekeeping, called once at app start. Keeps the table from growing."""
    now = db.utc_now_iso()
    stale = db.scalar(
        "SELECT COUNT(*) FROM sessions WHERE expires_at < ? OR revoked_at IS NOT NULL",
        (now,),
    )
    db.execute(
        "DELETE FROM sessions WHERE expires_at < ? OR revoked_at IS NOT NULL", (now,)
    )
    db.execute(
        "DELETE FROM login_attempts WHERE ts < ?", (_lockout_cutoff_iso(),)
    )
    return int(stale or 0)


# --------------------------------------------------------------------------- #
# Lookups and account management
# --------------------------------------------------------------------------- #


def get_user(user_id: int) -> User | None:
    row = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return None if row is None else _to_user(row)


def get_user_by_email(email: str) -> User | None:
    row = db.query_one(
        "SELECT * FROM users WHERE email = ?", (security.normalize_email(email),)
    )
    return None if row is None else _to_user(row)


def list_users(*, include_sandbox: bool = True) -> list[User]:
    sql = "SELECT * FROM users"
    if not include_sandbox:
        sql += " WHERE is_sandbox = 0"
    sql += " ORDER BY role, email"
    return [_to_user(row) for row in db.query(sql)]


def set_active(user_id: int, active: bool, *, actor_id: int | None = None) -> None:
    db.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
    if not active:
        logout_all(user_id)
    db.audit(actor_id, "user.set_active", target=user_id, active=active)


def change_password(user_id: int, current: str, new: str) -> None:
    row = db.query_one("SELECT password_hash FROM users WHERE id = ?", (user_id,))
    if row is None:
        raise AuthError("Account not found.")
    if not security.verify_password(current, row["password_hash"]):
        raise AuthError("Current password is incorrect.")
    problem = security.password_problem(new)
    if problem:
        raise AuthError(problem)
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (security.hash_password(new), user_id),
    )
    logout_all(user_id)  # a password change should end sessions elsewhere
    db.audit(user_id, "user.change_password")


def update_profile(user_id: int, *, full_name: str | None = None) -> User | None:
    if full_name is not None:
        db.execute(
            "UPDATE users SET full_name = ? WHERE id = ?", (full_name.strip(), user_id)
        )
    return get_user(user_id)


def set_role(
    user_id: int,
    role: Role,
    *,
    company_id: str | None = None,
    actor_id: int | None = None,
) -> User | None:
    """Admin-only role change. Recruiters must be attached to a company."""
    if role not in ROLES:
        raise AuthError("Unknown account type.")
    if role == "recruiter" and not company_id:
        raise AuthError("Recruiters need a company.")
    db.execute(
        "UPDATE users SET role = ?, company_id = ? WHERE id = ?",
        (role, company_id, user_id),
    )
    db.audit(actor_id, "user.set_role", target=user_id, role=role)
    return get_user(user_id)


# --------------------------------------------------------------------------- #
# Role gating
# --------------------------------------------------------------------------- #


def has_role(user: User | None, *roles: str) -> bool:
    if user is None or not user.is_active:
        return False
    if user.role == "admin":
        return True  # admin is a superset of every other role
    return user.role in roles


def require_role(user: User | None, *roles: str) -> User:
    """Raise unless ``user`` may act in one of ``roles``. Admin always passes."""
    if not has_role(user, *roles):
        raise AuthError("You do not have access to that page.")
    return user  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Sandbox / demo accounts
# --------------------------------------------------------------------------- #

SANDBOX_DOMAIN = "sandbox.local"
SANDBOX_PASSWORD = "sandbox-demo-1234"


def sandbox_email(label: str = "candidate") -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-")
    return f"{slug or 'candidate'}@{SANDBOX_DOMAIN}"


def ensure_sandbox_candidate(
    label: str = "candidate", *, full_name: str = "Sandbox Candidate"
) -> User:
    """Get-or-create a throwaway candidate the admin can drive end to end.

    Password is fixed and printed in the admin UI: these accounts exist to be
    logged into during a demo and wiped afterwards, not to be secured.
    """
    email = sandbox_email(label)
    existing = get_user_by_email(email)
    if existing:
        return existing
    return register(
        email,
        SANDBOX_PASSWORD,
        full_name=full_name,
        role="candidate",
        is_sandbox=True,
    )


def sandbox_counts() -> dict[str, int]:
    return {
        "users": int(db.scalar("SELECT COUNT(*) FROM users WHERE is_sandbox = 1") or 0),
        "applications": int(
            db.scalar("SELECT COUNT(*) FROM applications WHERE is_sandbox = 1") or 0
        ),
        "interviews": int(
            db.scalar("SELECT COUNT(*) FROM interviews WHERE is_sandbox = 1") or 0
        ),
    }


def reset_sandbox(*, actor_id: int | None = None) -> dict[str, int]:
    """Delete every sandbox row and the media it wrote. Real data is untouched.

    Order matters only for the standalone sandbox applications: deleting the
    sandbox *users* already cascades to their applications, interviews, turns
    and proctor events via the foreign keys.

    Media is swept first, and it is the reason this is not simply three DELETEs:
    answer recordings and proctoring snapshots are files, not rows, so the
    cascade cannot reach them. Leaving them behind would mean a "reset" that
    keeps audio of a test interview — and photographs of whoever sat it —
    indefinitely.
    """
    # Imported here rather than at the top: auth_service is imported by almost
    # everything, and this is the only function in it that touches media.
    from services import proctor_service, recording_service

    removed = sandbox_counts()
    interview_ids = [
        int(row["id"])
        for row in db.query("SELECT id FROM interviews WHERE is_sandbox = 1")
    ]
    files = 0
    for interview_id in interview_ids:
        try:
            files += recording_service.purge(interview_id)
            files += proctor_service.purge_snapshots(interview_id)
        except Exception as exc:  # a locked file must not block the reset
            log.warning("Sandbox media sweep failed for %s: %s", interview_id, exc)

    with db.transaction():
        db.execute("DELETE FROM interviews WHERE is_sandbox = 1")
        db.execute("DELETE FROM applications WHERE is_sandbox = 1")
        db.execute("DELETE FROM users WHERE is_sandbox = 1")
    db.audit(actor_id, "sandbox.reset", files=files, **removed)
    log.info("Sandbox reset: %s, %s media file(s)", removed, files)
    return removed
