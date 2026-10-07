"""Microphone capture: the audio actually handed to Whisper.

This file exists because of a bug that every other test missed. The capture
sink converted WebRTC frames like this::

    arr = frame.to_ndarray()
    if arr.ndim > 1:
        arr = arr.mean(axis=0)

and the result was then written under a 16 kHz mono WAV header. Both lines look
right and both are wrong. ``to_ndarray()`` returns *packed* audio — a 960-sample
48 kHz stereo frame comes back with shape ``(1, 1920)``, both channels
interleaved along the second axis — so ``mean(axis=0)`` averages an axis of
length one and does nothing at all. The interleaved 48 kHz data went into a
16 kHz container, stretching every clip 6x and dropping it by the same factor.

Whisper was handed a subsonic drone and returned empty strings, so the symptom
reported was "the microphone does not work" when capture was fine the whole
time.

Nothing in the suite caught it because WebRTC cannot run under ``AppTest`` and
the room degrades to the typed path, which is the path the room tests walk. So
these tests skip the browser entirely and assert on signal: build a frame shaped
exactly the way PyAV shapes a real one, push it through the conversion, and
check the duration, the pitch and the amplitude of what comes out.
"""

from __future__ import annotations

import io
import wave

import av
import numpy as np
import pytest

from speech import stt as stt_module

SOURCE_RATE = 48_000  # what WebRTC hands us, regardless of what we ask for
TONE_HZ = 440.0
TONE_PEAK = 0.5


def _packed_stereo_frames(seconds: float, *, block: int = 960):
    """A 440 Hz tone, framed the way aiortc frames a real microphone.

    Packed s16 stereo interleaves as ``[L0, R0, L1, R1, ...]``. Writing the
    channels channel-major instead is a mistake worth naming, because it makes
    a broken pipeline look merely out of tune rather than six times too slow.
    """
    total = int(SOURCE_RATE * seconds)
    t = np.arange(total) / SOURCE_RATE
    tone = (TONE_PEAK * np.sin(2 * np.pi * TONE_HZ * t) * 32767).astype(np.int16)

    for start in range(0, total, block):
        left = tone[start : start + block]
        if left.size == 0:
            break
        interleaved = np.empty((1, left.size * 2), dtype=np.int16)
        interleaved[0, 0::2] = left
        interleaved[0, 1::2] = left
        frame = av.AudioFrame.from_ndarray(
            interleaved, format="s16", layout="stereo"
        )
        frame.sample_rate = SOURCE_RATE
        yield frame


def _capture(seconds: float) -> np.ndarray:
    """Run the shipped conversion over a synthetic microphone stream."""
    from ui.pages.interview_room import _new_audio_resampler, _resample_to_mono16k

    resampler = _new_audio_resampler()
    chunks: list[np.ndarray] = []
    for frame in _packed_stereo_frames(seconds):
        chunks.extend(_resample_to_mono16k(frame, resampler))
    assert chunks, "conversion produced no audio at all"
    return np.concatenate(chunks)


def _dominant_hz(samples: np.ndarray, rate: int) -> float:
    wav = stt_module.pcm_to_wav_bytes(samples, rate)
    with wave.open(io.BytesIO(wav)) as handle:
        count = handle.getnframes()
        actual_rate = handle.getframerate()
        pcm = np.frombuffer(handle.readframes(count), dtype=np.int16)
    spectrum = np.abs(np.fft.rfft(pcm.astype(np.float64) * np.hanning(len(pcm))))
    return float(np.fft.rfftfreq(len(pcm), 1 / actual_rate)[spectrum.argmax()])


@pytest.mark.parametrize("seconds", [0.5, 1.0, 3.0])
def test_capture_preserves_duration(seconds: float) -> None:
    """A clip must last as long as the person spoke.

    This is the assertion that fails loudly on the original bug: it reported
    6x the real duration, so a three-second answer arrived as eighteen seconds.
    """
    samples = _capture(seconds)
    measured = samples.size / stt_module.TARGET_SAMPLE_RATE
    assert measured == pytest.approx(seconds, abs=0.05)


def test_capture_preserves_pitch() -> None:
    """440 Hz in, 440 Hz out.

    Duration alone is not enough: a pipeline that resampled correctly but
    mislabelled the rate would still pass the duration check on some inputs.
    Pitch pins down the rate itself. The old path put this at 73.3 Hz.
    """
    samples = _capture(3.0)
    hz = _dominant_hz(samples, stt_module.TARGET_SAMPLE_RATE)
    assert hz == pytest.approx(TONE_HZ, rel=0.02)


def test_capture_is_normalised_not_raw_int16() -> None:
    """Samples arrive as float32 in [-1, 1], carrying their true amplitude.

    ``pcm_to_wav_bytes`` peak-normalises anything above 1.0, so raw int16 would
    still produce audible output — and that is the trap. Rescuing every clip to
    full scale also lifts the noise floor of a near-silent one, which turns a
    candidate who said nothing into a candidate with a loud room.
    """
    samples = _capture(1.0)
    assert samples.dtype == np.float32
    assert np.abs(samples).max() <= 1.0
    assert np.abs(samples).max() == pytest.approx(TONE_PEAK, abs=0.02)


def test_capture_downmixes_to_one_channel() -> None:
    """Stereo in, mono out — at the mono sample count, not double it."""
    samples = _capture(1.0)
    assert samples.ndim == 1
    assert samples.size == pytest.approx(stt_module.TARGET_SAMPLE_RATE, rel=0.02)


def test_a_silent_resampler_flush_is_not_an_error() -> None:
    """An empty return must yield no chunks rather than raising.

    The resampler buffers a filter delay line, so the first call can legitimately
    produce nothing. Treating that as a failure would drop the connection on the
    first frame of every interview.
    """
    from ui.pages.interview_room import _new_audio_resampler, _resample_to_mono16k

    class _Empty:
        def resample(self, frame):  # noqa: ANN001
            return []

    frame = next(_packed_stereo_frames(0.1))
    assert _resample_to_mono16k(frame, _Empty()) == []

    class _None:
        def resample(self, frame):  # noqa: ANN001
            return None

    assert _resample_to_mono16k(frame, _None()) == []
    # And the real one still works afterwards, i.e. nothing above mutated state.
    assert _resample_to_mono16k(frame, _new_audio_resampler()) != []


def test_too_short_a_clip_is_refused_before_transcription() -> None:
    """The guard exists and is set to something a person could actually trip.

    Whisper on a fraction of a second returns either nothing or a hallucinated
    stock phrase, and the hallucination is the dangerous one because it would be
    stored as the candidate's answer.
    """
    from ui.pages import interview_room

    assert 0 < interview_room.MIN_ANSWER_SECONDS <= 2.0
    brief = _capture(0.5)
    assert brief.size / stt_module.TARGET_SAMPLE_RATE < interview_room.MIN_ANSWER_SECONDS
