"""Speech-to-text for spoken interview answers.

Default backend is faster-whisper: MIT licensed, runs entirely on the CPU, no
account and no per-minute cost. Gemini is available as a fallback for machines
that cannot spare the RAM, and a typed-answer path always exists so a broken
microphone never traps a candidate mid-interview.
"""

from __future__ import annotations

import abc
import io
import logging
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from core.config import settings

log = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16_000


@dataclass
class Transcript:
    text: str
    provider: str
    language: str = ""
    duration_seconds: float = 0.0
    latency_seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


def pcm_to_wav_bytes(samples: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Mono 16-bit WAV container around raw float or int16 samples."""
    if samples.dtype != np.int16:
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        scaled = samples / peak if peak > 1.0 else samples
        samples = np.clip(scaled, -1.0, 1.0)
        samples = (samples * 32767.0).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


class STTProvider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def transcribe(self, wav_bytes: bytes) -> Transcript: ...

    @abc.abstractmethod
    def health(self) -> dict[str, Any]: ...


class FasterWhisperSTT(STTProvider):
    """Local Whisper via CTranslate2.

    The model is loaded lazily and only once — a cold load of ``base`` costs a
    few seconds and roughly 150 MB, so paying for it at import time would slow
    every page load in the app.
    """

    name = "faster_whisper"

    def __init__(
        self,
        *,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language or None
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info(
                    "Loading faster-whisper %s (%s/%s)",
                    self.model_size,
                    self.device,
                    self.compute_type,
                )
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                )
        return self._model

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        started = time.time()
        try:
            model = self._ensure_model()
            segments, info = model.transcribe(
                io.BytesIO(wav_bytes),
                language=self.language,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return Transcript(
                text=text,
                provider=self.name,
                language=getattr(info, "language", "") or "",
                duration_seconds=float(getattr(info, "duration", 0.0) or 0.0),
                latency_seconds=time.time() - started,
            )
        except Exception as exc:
            log.warning("faster-whisper transcription failed: %s", exc)
            return Transcript(
                text="",
                provider=self.name,
                latency_seconds=time.time() - started,
                error=str(exc),
            )

    def health(self) -> dict[str, Any]:
        try:
            import faster_whisper  # noqa: F401

            return {
                "provider": self.name,
                "ok": True,
                "detail": f"model={self.model_size} device={self.device}",
                "loaded": self._model is not None,
            }
        except Exception as exc:
            return {"provider": self.name, "ok": False, "detail": str(exc)}


class GeminiSTT(STTProvider):
    """Cloud fallback — sends the answer audio inline to Gemini."""

    name = "gemini"

    def __init__(self, *, model: str, api_keys: tuple[str, ...], api_base: str):
        self.model = model
        self.api_keys = [key for key in api_keys if key]
        self.api_base = api_base.rstrip("/")

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        import base64

        import requests

        if not self.api_keys:
            return Transcript(text="", provider=self.name, error="no Gemini API key")

        started = time.time()
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": "Transcribe this interview answer verbatim. "
                            "Return only the transcript text."
                        },
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": base64.b64encode(wav_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.0},
        }
        try:
            response = requests.post(
                f"{self.api_base}/models/{self.model}:generateContent",
                headers={
                    "x-goog-api-key": self.api_keys[0],
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=120,
            )
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts).strip()
            return Transcript(
                text=text, provider=self.name, latency_seconds=time.time() - started
            )
        except Exception as exc:
            log.warning("Gemini transcription failed: %s", exc)
            return Transcript(text="", provider=self.name, error=str(exc))

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "ok": bool(self.api_keys),
            "detail": "uses the Gemini API key" if self.api_keys else "no API key set",
        }


class DisabledSTT(STTProvider):
    name = "disabled"

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        return Transcript(
            text="",
            provider=self.name,
            error="Speech-to-text is disabled; type your answer instead.",
        )

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "detail": "typed answers only"}


_stt: STTProvider | None = None
_stt_lock = threading.Lock()


def build_stt(name: str | None = None) -> STTProvider:
    chosen = (name or settings.speech.stt_provider or "faster_whisper").lower()
    conf = settings.speech
    if chosen in ("faster_whisper", "whisper", "local"):
        return FasterWhisperSTT(
            model_size=conf.whisper_model,
            device=conf.whisper_device,
            compute_type=conf.whisper_compute_type,
            language=conf.stt_language,
        )
    if chosen == "gemini":
        return GeminiSTT(
            model=settings.llm.gemini_model,
            api_keys=settings.llm.gemini_api_keys,
            api_base=settings.llm.gemini_api_base,
        )
    return DisabledSTT()


def get_stt() -> STTProvider:
    global _stt
    if _stt is None:
        with _stt_lock:
            if _stt is None:
                _stt = build_stt()
    return _stt


def set_stt(provider: STTProvider | None) -> None:
    global _stt
    _stt = provider


def save_wav(wav_bytes: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)
    return path
