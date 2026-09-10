"""Text-to-speech so the interviewer actually speaks.

Three backends, all free:

* ``pyttsx3`` — offline OS voices (SAPI5 on Windows, NSSpeech on macOS, espeak on
  Linux). Zero download, works immediately.
* ``piper``  — local neural voices, much nicer, needs a one-off voice download.
* ``browser`` — no server-side audio at all; the page uses the Web Speech API.

The question text is always rendered on screen as well, so a missing audio
backend degrades to a readable interview rather than a broken one.
"""

from __future__ import annotations

import abc
import hashlib
import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import settings

log = logging.getLogger(__name__)


@dataclass
class Speech:
    """Either server-rendered audio bytes, or a hint to let the browser speak."""

    audio: bytes = b""
    mime_type: str = "audio/wav"
    provider: str = "none"
    use_browser: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.audio) or self.use_browser


class TTSProvider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def synthesize(self, text: str) -> Speech: ...

    @abc.abstractmethod
    def health(self) -> dict[str, Any]: ...


class Pyttsx3TTS(TTSProvider):
    """Offline OS voices.

    pyttsx3's engine object is not safe to reuse across Streamlit's rerun
    threads on Windows, so a fresh engine is created per utterance inside a lock.
    Utterances are short (one question) and results are cached by the caller, so
    the overhead is not noticeable.
    """

    name = "pyttsx3"

    def __init__(self, *, rate: int = 170):
        self.rate = rate
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> Speech:
        if not text.strip():
            return Speech(provider=self.name, error="empty text")
        tmp_path = Path(tempfile.gettempdir()) / f"tts_{hashlib.md5(text.encode()).hexdigest()}.wav"
        try:
            with self._lock:
                import pyttsx3

                engine = pyttsx3.init()
                engine.setProperty("rate", self.rate)
                engine.save_to_file(text, str(tmp_path))
                engine.runAndWait()
                try:
                    engine.stop()
                except Exception:  # some drivers raise on double-stop
                    pass

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                return Speech(provider=self.name, error="engine produced no audio")
            audio = tmp_path.read_bytes()
            return Speech(audio=audio, mime_type="audio/wav", provider=self.name)
        except Exception as exc:
            log.warning("pyttsx3 synthesis failed: %s", exc)
            return Speech(provider=self.name, error=str(exc))
        finally:
            tmp_path.unlink(missing_ok=True)

    def health(self) -> dict[str, Any]:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            voices = engine.getProperty("voices") or []
            try:
                engine.stop()
            except Exception:
                pass
            return {
                "provider": self.name,
                "ok": bool(voices),
                "detail": f"{len(voices)} system voice(s) available",
            }
        except Exception as exc:
            return {"provider": self.name, "ok": False, "detail": str(exc)}


class PiperTTS(TTSProvider):
    """Local neural voices via the piper CLI or the piper-tts Python package."""

    name = "piper"

    def __init__(self, *, model_path: str):
        self.model_path = model_path

    def synthesize(self, text: str) -> Speech:
        if not self.model_path or not Path(self.model_path).exists():
            return Speech(
                provider=self.name,
                error="PIPER_MODEL_PATH is not set to an existing .onnx voice",
            )
        binary = shutil.which("piper")
        if not binary:
            return Speech(provider=self.name, error="piper executable not on PATH")

        out_path = Path(tempfile.gettempdir()) / "piper_out.wav"
        try:
            subprocess.run(
                [binary, "--model", self.model_path, "--output_file", str(out_path)],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
                timeout=60,
            )
            return Speech(
                audio=out_path.read_bytes(), mime_type="audio/wav", provider=self.name
            )
        except Exception as exc:
            log.warning("piper synthesis failed: %s", exc)
            return Speech(provider=self.name, error=str(exc))
        finally:
            out_path.unlink(missing_ok=True)

    def health(self) -> dict[str, Any]:
        has_binary = shutil.which("piper") is not None
        has_voice = bool(self.model_path) and Path(self.model_path).exists()
        return {
            "provider": self.name,
            "ok": has_binary and has_voice,
            "detail": f"binary={'yes' if has_binary else 'no'} voice={'yes' if has_voice else 'no'}",
        }


class BrowserTTS(TTSProvider):
    """Delegate synthesis to the candidate's browser (Web Speech API)."""

    name = "browser"

    def synthesize(self, text: str) -> Speech:
        return Speech(provider=self.name, use_browser=True)

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "ok": True,
            "detail": "spoken client-side; needs a Chromium or Safari browser",
        }


class DisabledTTS(TTSProvider):
    name = "disabled"

    def synthesize(self, text: str) -> Speech:
        return Speech(provider=self.name, error="text-to-speech disabled")

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "detail": "questions shown as text"}


_tts: TTSProvider | None = None
_tts_lock = threading.Lock()


def build_tts(name: str | None = None) -> TTSProvider:
    chosen = (name or settings.speech.tts_provider or "pyttsx3").lower()
    if chosen == "pyttsx3":
        return Pyttsx3TTS(rate=settings.speech.speaking_rate)
    if chosen == "piper":
        return PiperTTS(model_path=settings.speech.piper_model_path)
    if chosen == "browser":
        return BrowserTTS()
    return DisabledTTS()


def get_tts() -> TTSProvider:
    global _tts
    if _tts is None:
        with _tts_lock:
            if _tts is None:
                _tts = build_tts()
    return _tts


def set_tts(provider: TTSProvider | None) -> None:
    global _tts
    _tts = provider


def speak(text: str) -> Speech:
    """Synthesize, falling back to browser speech if the local engine fails."""
    result = get_tts().synthesize(text)
    if result.ok:
        return result
    log.info("TTS backend unavailable (%s); falling back to browser speech", result.error)
    return BrowserTTS().synthesize(text)
