"""One health report, two front ends.

``collect()`` returns plain dictionaries so the admin page can render it and
``scripts/healthcheck.py`` can print it without duplicating any probe logic.
Every probe is wrapped: a broken backend must show as a red row, never as a
traceback that hides the other twelve rows.
"""

from __future__ import annotations

import importlib
import logging
import platform
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from core import db, model_assets
from core.config import settings

log = logging.getLogger(__name__)

Status = str  # "ok" | "warn" | "fail"


@dataclass
class Check:
    name: str
    status: Status
    detail: str = ""
    group: str = "general"
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(name: str, group: str, probe: Callable[[], Check]) -> Check:
    try:
        return probe()
    except Exception as exc:  # a probe must never break the report
        log.warning("Health probe %s failed: %s", name, exc)
        return Check(name=name, status="fail", detail=str(exc), group=group)


# --------------------------------------------------------------------------- #
# Individual probes
# --------------------------------------------------------------------------- #


def _check_python() -> Check:
    return Check(
        name="Python",
        status="ok",
        detail=f"{platform.python_version()} on {platform.system()} {platform.release()}",
        group="environment",
        extra={"executable": sys.executable},
    )


def _check_database() -> Check:
    path = settings.database_path
    db.init_db()
    tables = [
        row["name"]
        for row in db.query(
            # `sqlite_%` is SQLite's own bookkeeping — `sqlite_sequence` appears
            # the moment an AUTOINCREMENT column does. Counting it reports one
            # more table than the schema declares, which reads as a migration
            # having gone wrong when it has not.
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    size_mb = round(path.stat().st_size / 1_048_576, 2) if path.exists() else 0.0
    counts = {
        table: int(db.scalar(f"SELECT COUNT(*) FROM {table}") or 0)
        for table in ("users", "companies", "roles", "applications", "interviews")
        if table in tables
    }
    return Check(
        name="SQLite database",
        status="ok",
        detail=f"{len(tables)} tables, {size_mb} MB at {path}",
        group="storage",
        extra={"rows": counts, "journal": db.scalar("PRAGMA journal_mode")},
    )


def _check_llm() -> Check:
    from llm import get_llm

    provider = get_llm()
    report = provider.health()
    ok = bool(report.get("ok"))
    return Check(
        name=f"LLM · {report.get('provider', provider.name)}",
        status="ok" if ok else "fail",
        detail=str(report.get("detail", "")),
        group="ai",
        extra=report,
    )


def _check_stt() -> Check:
    from speech import get_stt

    report = get_stt().health()
    return Check(
        name=f"Speech-to-text · {report.get('provider')}",
        status="ok" if report.get("ok") else "warn",
        detail=str(report.get("detail", "")),
        group="ai",
        extra=report,
    )


def _check_tts() -> Check:
    from speech import get_tts

    report = get_tts().health()
    return Check(
        name=f"Text-to-speech · {report.get('provider')}",
        status="ok" if report.get("ok") else "warn",
        detail=str(report.get("detail", "")),
        group="ai",
        extra=report,
    )


def _check_package(module: str, label: str, *, required: bool = True) -> Check:
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return Check(
            name=label,
            status="fail" if required else "warn",
            detail=f"not importable: {exc}",
            group="packages",
        )
    version = getattr(mod, "__version__", "") or "installed"
    return Check(name=label, status="ok", detail=str(version), group="packages")


def _check_models() -> list[Check]:
    checks: list[Check] = []
    for entry in model_assets.status():
        cached = bool(entry["cached"])
        checks.append(
            Check(
                name=f"Model · {entry['key']}",
                # Not cached is not a failure: it downloads on first use.
                status="ok" if cached else "warn",
                detail=(
                    f"{entry['size_mb']} MB · {entry['licence']}"
                    if cached
                    # Saying "not downloaded yet" and stopping there left the
                    # reader with nothing to do about it — the lazy download
                    # happens mid-interview and fails quietly, so a missing
                    # weight looked like a dead end. Name the command.
                    else (
                        f"missing ({entry['size_mb']} MB, {entry['licence']}) — "
                        "run: python scripts/download_models.py"
                    )
                ),
                group="models",
                extra=dict(entry),
            )
        )
    return checks


def _check_secrets() -> Check:
    """Flag the two settings that must not stay at their shipped defaults."""
    problems: list[str] = []
    if settings.auth.recruiter_invite_code in ("", "change-me-recruiter"):
        problems.append("RECRUITER_INVITE_CODE is still the default")
    if settings.auth.admin_password in ("", "admin-dev-2026"):
        problems.append("ADMIN_PASSWORD is a development value")
    if settings.llm.provider == "gemini" and not settings.llm.gemini_api_keys:
        problems.append("LLM_PROVIDER=gemini but no GEMINI_API_KEY is set")
    return Check(
        name="Configuration",
        status="ok" if not problems else "warn",
        detail="; ".join(problems) or "no obvious placeholder values left",
        group="environment",
    )


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

_PACKAGES: tuple[tuple[str, str, bool], ...] = (
    ("streamlit", "streamlit", True),
    ("streamlit_webrtc", "streamlit-webrtc", True),
    ("aiortc", "aiortc", True),
    ("av", "PyAV", True),
    ("cv2", "OpenCV", True),
    ("numpy", "NumPy", True),
    ("pymupdf", "PyMuPDF", True),
    ("faster_whisper", "faster-whisper", False),
    ("pyttsx3", "pyttsx3", False),
    ("mediapipe", "MediaPipe", False),
    ("ultralytics", "Ultralytics YOLO", False),
    ("torch", "PyTorch", False),
)


def collect(*, include_ai: bool = True) -> list[dict[str, Any]]:
    """Run every probe. ``include_ai=False`` skips anything that may hit the network."""
    checks: list[Check] = [
        _run("Python", "environment", _check_python),
        _run("Configuration", "environment", _check_secrets),
        _run("SQLite database", "storage", _check_database),
    ]
    if include_ai:
        checks += [
            _run("LLM", "ai", _check_llm),
            _run("STT", "ai", _check_stt),
            _run("TTS", "ai", _check_tts),
        ]
    for module, label, required in _PACKAGES:
        checks.append(
            _run(label, "packages", lambda m=module, l=label, r=required: _check_package(m, l, required=r))
        )
    checks += _check_models()
    return [check.as_dict() for check in checks]


def summarize(report: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for entry in report:
        counts[entry.get("status", "fail")] = counts.get(entry.get("status", "fail"), 0) + 1
    return counts
