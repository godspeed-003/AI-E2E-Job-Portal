"""Schema upgrades must not need a wiped database.

The first version of this schema had no ``is_sandbox`` column. A real app.db
created back then still exists on disk, and starting the portal against it used
to crash: ``CREATE TABLE IF NOT EXISTS`` leaves the old table alone, so the index
on the new column failed on every boot. These tests pin the ordering that fixes
it — tables, then column migration, then indexes.
"""

from __future__ import annotations

from core import db
from services import auth_service as auth

_LEGACY_USERS = """
DROP TABLE IF EXISTS users;
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    company_id    TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
"""


def test_legacy_database_upgrades_in_place():
    conn = db.connect()
    conn.executescript(_LEGACY_USERS)

    db.init_db()  # used to raise: no such column: is_sandbox

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    assert "is_sandbox" in columns
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(users)")}
    assert "idx_users_sandbox" in indexes


def test_registration_works_after_the_upgrade():
    db.connect().executescript(_LEGACY_USERS)
    db.init_db()

    user = auth.register("legacy@test.local", "legacy-password-1")
    assert user.is_sandbox is False
    assert auth.get_user_by_email("legacy@test.local") is not None


def test_init_db_is_idempotent():
    for _ in range(3):
        db.init_db()
    assert int(db.scalar("SELECT COUNT(*) FROM users") or 0) == 0
