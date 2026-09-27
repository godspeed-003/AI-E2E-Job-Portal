"""Speech backends for the video interview (STT in, TTS out)."""

from speech.stt import (
    TARGET_SAMPLE_RATE,
    Transcript,
    build_stt,
    get_stt,
    pcm_to_wav_bytes,
    set_stt,
)
from speech.tts import Speech, build_tts, get_tts, set_tts, speak

__all__ = [
    "Speech",
    "TARGET_SAMPLE_RATE",
    "Transcript",
    "build_stt",
    "build_tts",
    "get_stt",
    "get_tts",
    "pcm_to_wav_bytes",
    "set_stt",
    "set_tts",
    "speak",
]
