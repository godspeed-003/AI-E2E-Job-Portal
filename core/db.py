"""SQLite persistence layer.

The MVP kept everything in ``data/results/*.json``. That breaks down as soon as
interviews exist: turn-by-turn writes, proctoring events arriving from a WebRTC
worker thread and a recruiter reading the same records concurrently all need
real transactions. SQLite in WAL mode handles that with no server to run, which
keeps the "free and open source, works offline" constraint intact.

Connections are thread-local because Streamlit reruns and the video callback
threads touch the database from different threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from core.config import settings

SCHEMA_VERSION = 1

_local = threading.local()

# Every connection ever handed out, so that a shutdown (or a test) can close the
# handles other threads are holding. `_generation` invalidates the thread-local
# cache: after close_all(), a thread that comes back reconnects instead of using
# the handle we closed underneath it.
_registry_lock = threading.Lock()
_open_conns: list[sqlite3.Connection] = []
_generation = 0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Timestamps are stored as ISO-8601 UTC strings: sortable and readable."""
    return utc_now().isoformat(timespec="seconds")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: Any, fallback: Any = None) -> Any:
    """Tolerant JSON decode — legacy rows may hold plain strings or NULL."""
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def connect() -> sqlite3.Connection:
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "gen", -1) == _generation:
        return conn

    settings.ensure_dirs()
    conn = sqlite3.connect(
        settings.database_path,
        timeout=15.0,
        isolation_level=None,  # autocommit; explicit transactions via transaction()
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    conn.execute("PRAGMA synchronous = NORMAL")
    _local.conn = conn
    _local.gen = _generation
    with _registry_lock:
        _open_conns.append(conn)
    return conn


def close() -> None:
    """Close this thread's connection."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        return
    with _registry_lock:
        if conn in _open_conns:
            _open_conns.remove(conn)
    conn.close()
    _local.conn = None


def close_all() -> None:
    """Close every thread's connection.

    Streamlit's script thread, the WebRTC callback threads and a test's main
    thread each hold their own handle, and on Windows an open handle is enough to
    make the database file undeletable. ``check_same_thread=False`` is what makes
    closing another thread's connection legal here.
    """
    global _generation
    with _registry_lock:
        conns = list(_open_conns)
        _open_conns.clear()
        _generation += 1
    for conn in conns:
        try:
            conn.close()
        except Exception:  # already closed, or closed mid-statement
            pass
    _local.conn = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def execute(sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
    cur = connect().execute(sql, params)
    return int(cur.lastrowid or 0)


def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> None:
    connect().executemany(sql, seq)


def query(sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def query_one(
    sql: str, params: Sequence[Any] | dict[str, Any] = ()
) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def scalar(sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
    row = query_one(sql, params)
    return None if row is None else row[0]


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('candidate','recruiter','admin')),
    full_name     TEXT NOT NULL DEFAULT '',
    company_id    TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    is_sandbox    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL COLLATE NOCASE,
    successful INTEGER NOT NULL,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT '',
    culture    TEXT NOT NULL DEFAULT '[]',
    core_values TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS roles (
    id                         TEXT PRIMARY KEY,
    company_id                 TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    title                      TEXT NOT NULL,
    job_description            TEXT NOT NULL DEFAULT '',
    requirements               TEXT NOT NULL DEFAULT '[]',
    is_open                    INTEGER NOT NULL DEFAULT 1,
    ats_reject_below           INTEGER,
    shortlist_llm_score_min    INTEGER,
    planned_questions          INTEGER,
    interview_duration_minutes INTEGER,
    interview_window_days      INTEGER,
    created_at                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id         TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    candidate_name  TEXT NOT NULL DEFAULT '',
    resume_path     TEXT,
    resume_text     TEXT NOT NULL DEFAULT '',
    resume_sha256   TEXT,
    ats_score       INTEGER NOT NULL DEFAULT 0,
    ats_matched     TEXT NOT NULL DEFAULT '[]',
    ats_missing     TEXT NOT NULL DEFAULT '[]',
    llm_score       INTEGER NOT NULL DEFAULT 0,
    max_score       INTEGER NOT NULL DEFAULT 25,
    alignment_score REAL NOT NULL DEFAULT 0,
    criteria        TEXT NOT NULL DEFAULT '{}',
    strengths       TEXT NOT NULL DEFAULT '[]',
    weaknesses      TEXT NOT NULL DEFAULT '[]',
    reason          TEXT NOT NULL DEFAULT '',
    screening_flags TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'under_review',
    is_sandbox      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS interviews (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id           INTEGER NOT NULL UNIQUE
                             REFERENCES applications(id) ON DELETE CASCADE,
    status                   TEXT NOT NULL DEFAULT 'pending',
    opens_at                 TEXT NOT NULL,
    closes_at                TEXT NOT NULL,
    duration_limit_seconds   INTEGER NOT NULL DEFAULT 1800,
    planned_questions        INTEGER NOT NULL DEFAULT 6,
    max_turns                INTEGER NOT NULL DEFAULT 9,
    max_attempts             INTEGER NOT NULL DEFAULT 1,
    attempt_count            INTEGER NOT NULL DEFAULT 0,
    started_at               TEXT,
    deadline_at              TEXT,
    completed_at             TEXT,
    plan                     TEXT,
    plan_generated_at        TEXT,
    evaluation               TEXT,
    total_score              INTEGER,
    max_total_score          INTEGER NOT NULL DEFAULT 25,
    integrity_score          INTEGER,
    integrity_verdict        TEXT,
    integrity_report         TEXT,
    recording_path           TEXT,
    enrollment_snapshot_path TEXT,
    consent_accepted_at      TEXT,
    is_sandbox               INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interview_turns (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id          INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    seq                   INTEGER NOT NULL,
    question              TEXT NOT NULL,
    focus_area            TEXT NOT NULL DEFAULT '',
    source                TEXT NOT NULL DEFAULT '',
    rationale             TEXT NOT NULL DEFAULT '',
    action                TEXT NOT NULL DEFAULT 'planned',
    detected_topics       TEXT NOT NULL DEFAULT '[]',
    answer                TEXT,
    answer_words          INTEGER NOT NULL DEFAULT 0,
    answer_audio_path     TEXT,
    transcript_source     TEXT NOT NULL DEFAULT '',
    asked_at              TEXT NOT NULL,
    answered_at           TEXT,
    answer_seconds        REAL,
    guardrail             TEXT,
    rejected_attempts     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (interview_id, seq)
);

CREATE TABLE IF NOT EXISTS proctor_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id     INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    ts               TEXT NOT NULL,
    elapsed_seconds  REAL NOT NULL DEFAULT 0,
    kind             TEXT NOT NULL,
    severity         TEXT NOT NULL DEFAULT 'low',
    confidence       REAL NOT NULL DEFAULT 1.0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    detail           TEXT NOT NULL DEFAULT '{}',
    snapshot_path    TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    user_id INTEGER,
    action  TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT '{}'
);
"""

# Indexes are applied *after* the column migration, because an index on a column
# that a later release added cannot be created until its ALTER TABLE has run.
# That ordering is the difference between an in-place upgrade and a wiped
# database: `CREATE TABLE IF NOT EXISTS` leaves an old table alone, so an index
# naming a new column would fail on every start.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_users_sandbox ON users(is_sandbox);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(email, ts);
CREATE INDEX IF NOT EXISTS idx_roles_company ON roles(company_id);
CREATE INDEX IF NOT EXISTS idx_applications_role ON applications(role_id, status);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);
CREATE INDEX IF NOT EXISTS idx_interviews_status ON interviews(status);
CREATE INDEX IF NOT EXISTS idx_proctor_interview ON proctor_events(interview_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


def init_db() -> None:
    """Create the schema if absent. Safe to call on every app start."""
    conn = connect()
    conn.executescript(SCHEMA)
    _add_missing_columns()
    conn.executescript(INDEXES)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


# Columns added after a table first shipped. `CREATE TABLE IF NOT EXISTS` will
# not touch an existing table, so new columns are applied here instead. Keeping
# this list around means an old database upgrades in place rather than needing a
# wipe — cheap insurance for a project that is actively growing.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("users", "is_sandbox", "INTEGER NOT NULL DEFAULT 0"),
    ("applications", "is_sandbox", "INTEGER NOT NULL DEFAULT 0"),
    ("interviews", "is_sandbox", "INTEGER NOT NULL DEFAULT 0"),
    # Why the ATS score landed where it did. A number a candidate cannot act on,
    # and a recruiter cannot defend, is not worth storing on its own.
    ("applications", "ats_matched", "TEXT NOT NULL DEFAULT '[]'"),
    ("applications", "ats_missing", "TEXT NOT NULL DEFAULT '[]'"),
    # Guardrail findings from the resume — e.g. instructions aimed at the
    # evaluator, stripped before scoring but surfaced to the recruiter.
    ("applications", "screening_flags", "TEXT NOT NULL DEFAULT '[]'"),
)


def _add_missing_columns() -> None:
    conn = connect()
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:  # table absent entirely — SCHEMA will have made it
            continue
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def audit(user_id: int | None, action: str, **detail: Any) -> None:
    execute(
        "INSERT INTO audit_log(ts, user_id, action, detail) VALUES(?,?,?,?)",
        (utc_now_iso(), user_id, action, dumps(detail)),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return {} if row is None else dict(row)
