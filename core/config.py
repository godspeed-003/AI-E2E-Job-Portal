"""Central configuration, read once from the environment.

Every tunable in the portal lands here so that switching the AI backend, the
speech engines or the proctoring thresholds is an ``.env`` edit rather than a
code change. Import :data:`settings` — do not read ``os.environ`` elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# `override=False` keeps genuine process env vars (CI, docker) authoritative.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    # Values pasted from other tools are frequently quoted; be forgiving.
    return value.strip().strip('"').strip("'")


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name) or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: str) -> Path:
    raw = _str(name, default) or default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "gemini"
    temperature: float = 0.0
    timeout_seconds: int = 120
    max_retries: int = 2

    gemini_api_keys: tuple[str, ...] = ()
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    openai_compat_base_url: str = "http://localhost:8080/v1"
    openai_compat_model: str = "local-model"
    openai_compat_api_key: str = "not-needed"


@dataclass(frozen=True)
class SpeechSettings:
    stt_provider: str = "faster_whisper"
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    stt_language: str = "en"

    tts_provider: str = "pyttsx3"
    piper_model_path: str = ""
    speaking_rate: int = 170


@dataclass(frozen=True)
class InterviewSettings:
    planned_questions: int = 6
    max_turns: int = 9
    duration_minutes: int = 30
    window_days: int = 7
    min_answer_words: int = 12
    max_attempts: int = 1


@dataclass(frozen=True)
class ScreeningSettings:
    ats_reject_below: int = 40
    shortlist_llm_score_min: int = 15
    llm_max_score: int = 25


@dataclass(frozen=True)
class ProctoringSettings:
    enabled: bool = True
    analyze_every_n_frames: int = 5
    record_session: bool = True
    save_snapshots: bool = True
    no_face_seconds: float = 4.0
    looking_away_seconds: float = 4.0
    yaw_limit_degrees: float = 32.0
    pitch_limit_degrees: float = 26.0
    integrity_fail_below: int = 55
    object_detection: bool = False


@dataclass(frozen=True)
class AuthSettings:
    session_ttl_hours: int = 12
    login_max_attempts: int = 8
    login_lockout_minutes: int = 15
    recruiter_invite_code: str = "change-me-recruiter"
    admin_email: str = "admin@portal.local"
    admin_password: str = ""


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    database_path: Path = PROJECT_ROOT / "data" / "app.db"
    media_dir: Path = PROJECT_ROOT / "data" / "media"
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    data_dir: Path = PROJECT_ROOT / "data"
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    log_level: str = "INFO"

    llm: LLMSettings = field(default_factory=LLMSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)
    interview: InterviewSettings = field(default_factory=InterviewSettings)
    screening: ScreeningSettings = field(default_factory=ScreeningSettings)
    proctoring: ProctoringSettings = field(default_factory=ProctoringSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.media_dir,
            self.upload_dir,
            self.database_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def _gemini_keys() -> tuple[str, ...]:
    """Collect every Gemini key the user supplied, in priority order.

    Accepts a comma-separated ``GEMINI_API_KEY`` plus numbered variants
    (``GEMINI_API_KEY2``…) and falls back to the ``GOOGLE_*`` spellings, because
    both appear in the wild. Order is preserved and duplicates dropped so the
    provider can rotate keys when one hits its quota.
    """
    candidates: list[str] = []
    for name in (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY2",
        "GEMINI_API_KEY3",
        "GOOGLE_API_KEY",
        "GOOGLE_API_KEY2",
        "GOOGLE_API_KEY3",
    ):
        raw = _str(name)
        if raw:
            candidates.extend(part.strip() for part in raw.split(",") if part.strip())

    seen: set[str] = set()
    ordered: list[str] = []
    for key in candidates:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_path=_path("DATABASE_PATH", "data/app.db"),
        media_dir=_path("MEDIA_DIR", "data/media"),
        upload_dir=_path("UPLOAD_DIR", "data/uploads"),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
        llm=LLMSettings(
            provider=_str("LLM_PROVIDER", "gemini").lower(),
            temperature=_float("LLM_TEMPERATURE", 0.0),
            timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 120),
            max_retries=_int("LLM_MAX_RETRIES", 2),
            gemini_api_keys=_gemini_keys(),
            gemini_model=_str("GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_api_base=_str(
                "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
            ).rstrip("/"),
            ollama_base_url=_str("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            ollama_model=_str("OLLAMA_MODEL", "llama3.1"),
            openai_compat_base_url=_str(
                "OPENAI_COMPAT_BASE_URL", "http://localhost:8080/v1"
            ).rstrip("/"),
            openai_compat_model=_str("OPENAI_COMPAT_MODEL", "local-model"),
            openai_compat_api_key=_str("OPENAI_COMPAT_API_KEY", "not-needed"),
        ),
        speech=SpeechSettings(
            stt_provider=_str("STT_PROVIDER", "faster_whisper").lower(),
            whisper_model=_str("WHISPER_MODEL", "base"),
            whisper_device=_str("WHISPER_DEVICE", "cpu"),
            whisper_compute_type=_str("WHISPER_COMPUTE_TYPE", "int8"),
            stt_language=_str("STT_LANGUAGE", "en"),
            tts_provider=_str("TTS_PROVIDER", "pyttsx3").lower(),
            piper_model_path=_str("PIPER_MODEL_PATH", ""),
            speaking_rate=_int("TTS_SPEAKING_RATE", 170),
        ),
        interview=InterviewSettings(
            planned_questions=_int("INTERVIEW_PLANNED_QUESTIONS", 6),
            max_turns=_int("INTERVIEW_MAX_TURNS", 9),
            duration_minutes=_int("INTERVIEW_DURATION_MINUTES", 30),
            window_days=_int("INTERVIEW_WINDOW_DAYS", 7),
            min_answer_words=_int("INTERVIEW_MIN_ANSWER_WORDS", 12),
            max_attempts=_int("INTERVIEW_MAX_ATTEMPTS", 1),
        ),
        screening=ScreeningSettings(
            ats_reject_below=_int("ATS_REJECT_BELOW", 40),
            shortlist_llm_score_min=_int("SHORTLIST_LLM_SCORE_MIN", 15),
        ),
        proctoring=ProctoringSettings(
            enabled=_bool("PROCTORING_ENABLED", True),
            analyze_every_n_frames=max(1, _int("PROCTOR_ANALYZE_EVERY_N_FRAMES", 5)),
            record_session=_bool("PROCTOR_RECORD_SESSION", True),
            save_snapshots=_bool("PROCTOR_SAVE_SNAPSHOTS", True),
            no_face_seconds=_float("PROCTOR_NO_FACE_SECONDS", 4.0),
            looking_away_seconds=_float("PROCTOR_LOOKING_AWAY_SECONDS", 4.0),
            yaw_limit_degrees=_float("PROCTOR_YAW_LIMIT_DEGREES", 32.0),
            pitch_limit_degrees=_float("PROCTOR_PITCH_LIMIT_DEGREES", 26.0),
            integrity_fail_below=_int("PROCTOR_INTEGRITY_FAIL_BELOW", 55),
            object_detection=_bool("PROCTOR_OBJECT_DETECTION", False),
        ),
        auth=AuthSettings(
            session_ttl_hours=_int("SESSION_TTL_HOURS", 12),
            login_max_attempts=_int("LOGIN_MAX_ATTEMPTS", 8),
            login_lockout_minutes=_int("LOGIN_LOCKOUT_MINUTES", 15),
            recruiter_invite_code=_str("RECRUITER_INVITE_CODE", "change-me-recruiter"),
            admin_email=_str("ADMIN_EMAIL", "admin@portal.local").lower(),
            admin_password=_str("ADMIN_PASSWORD", ""),
        ),
    )


settings = get_settings()
