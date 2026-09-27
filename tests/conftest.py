"""Test bootstrap.

Environment variables are set *before* any project module is imported, because
:mod:`core.config` snapshots the environment at import time. That keeps the test
suite pointed at a throwaway database and at the fake LLM, so the whole suite
runs offline with no API key and never touches ``data/app.db``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="portal-tests-"))

os.environ.update(
    {
        "DATABASE_PATH": str(_TMP / "test.db"),
        "MEDIA_DIR": str(_TMP / "media"),
        "UPLOAD_DIR": str(_TMP / "uploads"),
        "LLM_PROVIDER": "fake",
        "STT_PROVIDER": "disabled",
        "TTS_PROVIDER": "disabled",
        "PROCTORING_ENABLED": "false",
        "ADMIN_EMAIL": "admin@test.local",
        "ADMIN_PASSWORD": "admin-test-1234",
        "RECRUITER_INVITE_CODE": "test-invite-code",
        "SESSION_TTL_HOURS": "12",
        "LOGIN_MAX_ATTEMPTS": "5",
        "LOGIN_LOCKOUT_MINUTES": "15",
    }
)

from core import db  # noqa: E402
from core.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test gets an empty schema; no test can leak state into the next.

    ``close_all`` rather than ``close``: an AppTest run opens its own connection
    on Streamlit's script thread, and Windows refuses to unlink a file that any
    thread still has open.

    The media directory is wiped with it. Row ids restart at 1 in every test, so
    an interview's snapshots and recordings would otherwise be inherited by the
    next test's interview 1 — the kind of leak that makes a media assertion pass
    for the wrong reason.
    """
    db.close_all()
    base = str(settings.database_path)
    for suffix in ("", "-wal", "-shm"):
        Path(base + suffix).unlink(missing_ok=True)
    shutil.rmtree(settings.media_dir, ignore_errors=True)
    db.init_db()
    yield
    db.close_all()


@pytest.fixture
def fake_llm():
    """The scripted provider, reset per test."""
    from llm import reset, set_provider
    from llm.fake import FakeProvider

    provider = FakeProvider()
    set_provider(provider)
    yield provider
    reset()
