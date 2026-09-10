"""Authentication: hashing, sessions, throttling, roles, sandbox isolation."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core import db, security
from services import auth_service as auth


def _candidate(email: str = "ada@example.com", password: str = "correct-horse-1") -> auth.User:
    return auth.register(email, password, full_name="Ada Lovelace")


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #


def test_hash_is_salted_and_verifiable():
    first = security.hash_password("correct-horse-1")
    second = security.hash_password("correct-horse-1")
    assert first != second, "each hash must use a fresh salt"
    assert first.startswith("scrypt$")
    assert security.verify_password("correct-horse-1", first)
    assert not security.verify_password("correct-horse-2", first)


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$bad", "bcrypt$1$2$3$4$5"])
def test_verify_never_raises_on_malformed_hash(stored):
    assert security.verify_password("anything", stored) is False


def test_password_policy():
    assert security.password_problem("short") is not None
    assert security.password_problem("password") is not None
    assert security.password_problem(" leading-space-1") is not None
    assert security.password_problem("correct-horse-1") is None


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_register_normalizes_email_and_rejects_duplicates():
    user = auth.register("  Ada@Example.COM ", "correct-horse-1")
    assert user.email == "ada@example.com"
    assert user.role == "candidate"
    with pytest.raises(auth.AuthError, match="already exists"):
        auth.register("ada@example.com", "another-pass-1")


def test_register_rejects_bad_email_and_weak_password():
    with pytest.raises(auth.AuthError, match="valid email"):
        auth.register("not-an-email", "correct-horse-1")
    with pytest.raises(auth.AuthError, match="at least 8"):
        auth.register("ada@example.com", "short")


def test_hiring_accounts_need_the_invite_code():
    with pytest.raises(auth.AuthError, match="invite code"):
        auth.register(
            "boss@corp.com", "correct-horse-1", role="recruiter", company_id="amazon"
        )
    with pytest.raises(auth.AuthError, match="invite code"):
        auth.register(
            "boss@corp.com",
            "correct-horse-1",
            role="recruiter",
            company_id="amazon",
            invite_code="wrong",
        )
    recruiter = auth.register(
        "boss@corp.com",
        "correct-horse-1",
        role="recruiter",
        company_id="amazon",
        invite_code="test-invite-code",
    )
    assert recruiter.role == "recruiter"
    assert recruiter.company_id == "amazon"


def test_recruiter_must_pick_a_company():
    with pytest.raises(auth.AuthError, match="company"):
        auth.register(
            "boss@corp.com",
            "correct-horse-1",
            role="recruiter",
            invite_code="test-invite-code",
        )


def test_bootstrap_admin_is_idempotent():
    first = auth.ensure_admin_account()
    second = auth.ensure_admin_account()
    assert first is not None and second is not None
    assert first.id == second.id
    assert first.role == "admin"
    assert db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1


# --------------------------------------------------------------------------- #
# Login and sessions
# --------------------------------------------------------------------------- #


def test_login_returns_a_working_session():
    created = _candidate()
    user, token = auth.login("ada@example.com", "correct-horse-1", user_agent="pytest")
    assert user.id == created.id
    assert len(token) > 30

    resolved = auth.resolve_session(token)
    assert resolved is not None and resolved.id == created.id

    # Only the hash is stored, so the raw token must not appear in the table.
    stored = db.query("SELECT token_hash FROM sessions")
    assert [row["token_hash"] for row in stored] == [security.hash_token(token)]


def test_wrong_password_and_unknown_email_are_indistinguishable():
    _candidate()
    with pytest.raises(auth.AuthError, match="incorrect"):
        auth.login("ada@example.com", "wrong-password")
    with pytest.raises(auth.AuthError, match="incorrect"):
        auth.login("nobody@example.com", "wrong-password")


def test_resolve_rejects_unknown_revoked_and_expired_tokens():
    _candidate()
    _, token = auth.login("ada@example.com", "correct-horse-1")

    assert auth.resolve_session("") is None
    assert auth.resolve_session("not-a-real-token") is None

    auth.logout(token)
    assert auth.resolve_session(token) is None

    _, second = auth.login("ada@example.com", "correct-horse-1")
    past = (db.utc_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
        (past, security.hash_token(second)),
    )
    assert auth.resolve_session(second) is None


def test_deactivated_account_cannot_log_in_and_loses_sessions():
    user = _candidate()
    _, token = auth.login("ada@example.com", "correct-horse-1")
    auth.set_active(user.id, False)

    assert auth.resolve_session(token) is None
    with pytest.raises(auth.AuthError, match="disabled"):
        auth.login("ada@example.com", "correct-horse-1")


def test_logout_all_revokes_every_device():
    user = _candidate()
    tokens = [auth.login("ada@example.com", "correct-horse-1")[1] for _ in range(3)]
    assert auth.logout_all(user.id) == 3
    assert all(auth.resolve_session(token) is None for token in tokens)


def test_change_password_requires_the_old_one_and_ends_sessions():
    user = _candidate()
    _, token = auth.login("ada@example.com", "correct-horse-1")

    with pytest.raises(auth.AuthError, match="Current password"):
        auth.change_password(user.id, "not-it", "brand-new-pass-1")
    with pytest.raises(auth.AuthError, match="at least 8"):
        auth.change_password(user.id, "correct-horse-1", "tiny")

    auth.change_password(user.id, "correct-horse-1", "brand-new-pass-1")
    assert auth.resolve_session(token) is None
    auth.login("ada@example.com", "brand-new-pass-1")


def test_purge_expired_sessions_keeps_live_ones():
    _candidate()
    _, live = auth.login("ada@example.com", "correct-horse-1")
    _, dead = auth.login("ada@example.com", "correct-horse-1")
    auth.logout(dead)

    assert auth.purge_expired_sessions() >= 1
    assert db.scalar("SELECT COUNT(*) FROM sessions") == 1
    assert auth.resolve_session(live) is not None


# --------------------------------------------------------------------------- #
# Throttling
# --------------------------------------------------------------------------- #


def test_login_is_throttled_after_repeated_failures():
    _candidate()
    for _ in range(5):  # LOGIN_MAX_ATTEMPTS is 5 in the test environment
        with pytest.raises(auth.AuthError, match="incorrect"):
            auth.login("ada@example.com", "wrong-password")

    # The correct password is now refused too — that is the point of a lockout.
    with pytest.raises(auth.AuthError, match="Too many failed attempts"):
        auth.login("ada@example.com", "correct-horse-1")


def test_throttle_window_expires():
    _candidate()
    for _ in range(5):
        with pytest.raises(auth.AuthError):
            auth.login("ada@example.com", "wrong-password")

    old = (db.utc_now() - timedelta(hours=2)).isoformat(timespec="seconds")
    db.execute("UPDATE login_attempts SET ts = ?", (old,))
    assert auth.recent_failures("ada@example.com") == 0
    auth.login("ada@example.com", "correct-horse-1")


def test_throttle_is_per_email():
    _candidate()
    _candidate("grace@example.com")
    for _ in range(5):
        with pytest.raises(auth.AuthError):
            auth.login("ada@example.com", "wrong-password")
    auth.login("grace@example.com", "correct-horse-1")


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


def test_role_gating():
    candidate = _candidate()
    recruiter = auth.register(
        "boss@corp.com",
        "correct-horse-1",
        role="recruiter",
        company_id="amazon",
        invite_code="test-invite-code",
    )
    admin = auth.ensure_admin_account()
    assert admin is not None

    assert auth.has_role(candidate, "candidate")
    assert not auth.has_role(candidate, "recruiter")
    assert auth.has_role(recruiter, "recruiter")
    assert not auth.has_role(recruiter, "candidate")

    # Admin passes every gate, so admin pages never need a special case.
    assert auth.has_role(admin, "candidate")
    assert auth.has_role(admin, "recruiter")
    assert auth.has_role(admin, "admin")

    assert auth.has_role(None, "candidate") is False
    with pytest.raises(auth.AuthError, match="do not have access"):
        auth.require_role(candidate, "recruiter")
    assert auth.require_role(recruiter, "recruiter").id == recruiter.id


def test_set_role_requires_a_company_for_recruiters():
    user = _candidate()
    with pytest.raises(auth.AuthError, match="company"):
        auth.set_role(user.id, "recruiter")
    promoted = auth.set_role(user.id, "recruiter", company_id="deloitte")
    assert promoted is not None and promoted.role == "recruiter"


def test_display_helpers():
    user = _candidate()
    assert user.display_name == "Ada Lovelace"
    assert user.initials == "AL"
    assert auth.register("solo@example.com", "correct-horse-1").initials == "SO"


# --------------------------------------------------------------------------- #
# Sandbox isolation — the admin must be able to wipe demo data safely
# --------------------------------------------------------------------------- #


def test_sandbox_candidate_is_flagged_and_reusable():
    first = auth.ensure_sandbox_candidate()
    second = auth.ensure_sandbox_candidate()
    assert first.id == second.id
    assert first.is_sandbox is True
    assert first.email.endswith("@sandbox.local")


def test_reset_sandbox_removes_only_sandbox_rows():
    real = _candidate()
    demo = auth.ensure_sandbox_candidate("demo")
    assert auth.sandbox_counts()["users"] == 1

    removed = auth.reset_sandbox()
    assert removed["users"] == 1
    assert auth.get_user(demo.id) is None
    assert auth.get_user(real.id) is not None
    assert auth.sandbox_counts() == {"users": 0, "applications": 0, "interviews": 0}


def test_list_users_can_hide_the_sandbox():
    _candidate()
    auth.ensure_sandbox_candidate()
    assert len(auth.list_users()) == 2
    assert len(auth.list_users(include_sandbox=False)) == 1


def test_audit_log_records_authentication_events():
    _candidate()
    _, token = auth.login("ada@example.com", "correct-horse-1")
    auth.logout(token)
    actions = [row["action"] for row in db.query("SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["user.register", "auth.login", "auth.logout"]
