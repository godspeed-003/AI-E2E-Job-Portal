"""Audio proctoring: the energy heuristic, its rules, and the recording.

The signal these tests cover is a heuristic, so the tests are mostly about where
it is *not* allowed to fire. A false "second voice in the room" lands on a real
candidate's record as evidence a recruiter may act on, and the failure is
invisible — nobody reviewing the flag can tell it was the candidate's own
trailing syllable. So the negative cases outnumber the positive ones here on
purpose.

Signals are synthesised rather than recorded: an amplitude-modulated tone over a
noise floor reproduces the only two properties the analyser actually reads —
level relative to the room, and how long a level persists. Real speech would
make the tests slower, larger and no more discriminating.
"""

from __future__ import annotations

import numpy as np
import pytest

from proctoring import audio
from proctoring.rules import (
    KIND_BACKGROUND_VOICE,
    KIND_NO_SPEECH,
    RuleEngine,
)

SR = 16_000
NEAR = 0.30   # candidate, ~40 cm from the laptop mic
FAR = 0.02    # someone across the room, ~24 dB down
ROOM = 0.0015  # air conditioning


def _speech(seconds: float, amp: float, freq: float = 180.0) -> np.ndarray:
    """A voice-shaped burst: syllable-rate amplitude, never quite silent."""
    t = np.arange(int(seconds * SR)) / SR
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4.5 * t)
    return (amp * envelope * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _room(seconds: float, amp: float = ROOM) -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.normal(0, amp, int(seconds * SR)).astype(np.float32)


def _clip(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts)


# --------------------------------------------------------------------------- #
# The analyser
# --------------------------------------------------------------------------- #


def test_a_candidate_answering_alone_is_not_flagged():
    summary = audio.analyze(
        _clip(_room(0.5), _speech(2.0, NEAR), _room(0.8), _speech(2.5, NEAR), _room(0.5)),
        SR,
    )
    assert summary.spoke
    assert not summary.has_background_speech
    assert summary.background_seconds == 0.0


def test_a_second_distant_voice_between_answers_is_flagged():
    summary = audio.analyze(
        _clip(
            _room(0.5),
            _speech(2.0, NEAR),
            _room(0.2),
            _speech(2.0, FAR, freq=240.0),  # the prompter
            _room(0.2),
            _speech(2.0, NEAR),
            _room(0.5),
        ),
        SR,
    )
    assert summary.has_background_speech
    assert summary.background_seconds >= audio.MIN_BACKGROUND_RUN
    assert 0.0 < audio.confidence(summary) <= 0.9


def test_the_candidates_own_trailing_words_are_not_a_second_voice():
    """The failure this guards against is the one that would matter in practice.

    Everyone drops their volume at the end of a sentence or leans back from the
    mic. Without the guard band that is a quiet voice next to a loud one, which
    is exactly the shape the detector looks for.
    """
    summary = audio.analyze(
        _clip(
            _room(0.5),
            _speech(2.0, NEAR),
            _speech(0.3, 0.03),  # tailing off, immediately adjacent
            _room(1.5),
            _speech(2.0, NEAR),
            _room(0.5),
        ),
        SR,
    )
    assert summary.spoke
    assert not summary.has_background_speech


def test_a_short_noise_is_not_a_conversation():
    """A door, a cough, a chair. Under the minimum run, so it says nothing."""
    summary = audio.analyze(
        _clip(_room(0.5), _speech(2.0, NEAR), _room(0.6), _speech(0.3, FAR), _room(2.0)),
        SR,
    )
    assert not summary.has_background_speech


def test_room_tone_alone_reports_no_speech():
    summary = audio.analyze(_room(6.0), SR)
    assert not summary.spoke
    assert not summary.has_background_speech
    assert summary.dynamic_range_db < audio.MIN_DYNAMIC_RANGE_DB


def test_a_flat_clip_makes_no_claim_about_who_spoke():
    """No dynamic range means no near/far contrast, so no attribution is possible."""
    steady = _speech(5.0, NEAR)
    summary = audio.analyze(steady, SR)
    assert not summary.has_background_speech


def test_a_lone_distant_voice_reads_as_the_candidate():
    """A documented limit, pinned so it cannot change silently.

    Level is relative: the loudest voice present defines "near field". One
    distant speaker is therefore indistinguishable from a candidate sitting far
    back, and this detector must not claim otherwise — video substitution
    detection is what covers that case.
    """
    summary = audio.analyze(
        _clip(_room(0.5), _speech(3.0, 0.05, freq=240.0), _room(0.5)), SR
    )
    assert summary.spoke
    assert not summary.has_background_speech


@pytest.mark.parametrize(
    "samples, rate",
    [
        (np.zeros(0, dtype=np.float32), SR),
        (np.zeros(64, dtype=np.float32), SR),   # shorter than two frames
        (_room(1.0), 0),                        # nonsense sample rate
        (np.zeros(SR, dtype=np.float32), SR),   # digital silence: log10(0)
    ],
)
def test_degenerate_buffers_return_zeros_rather_than_raising(samples, rate):
    """The caller is a live interview. Nothing here may throw."""
    summary = audio.analyze(samples, rate)
    assert summary.background_seconds == 0.0
    assert not summary.has_background_speech
    assert np.isfinite(summary.noise_floor_db)
    assert np.isfinite(summary.peak_db)


def test_the_detail_a_reviewer_sees_is_the_measurement():
    summary = audio.analyze(_clip(_room(0.5), _speech(2.0, NEAR), _room(0.5)), SR)
    detail = summary.as_detail()
    assert set(detail) == {
        "duration_seconds",
        "voiced_seconds",
        "background_seconds",
        "noise_floor_db",
        "peak_db",
    }
    assert detail["duration_seconds"] == pytest.approx(3.0, abs=0.1)


def test_confidence_never_reaches_certainty():
    """An energy heuristic that cannot name a speaker must not report 1.0."""
    summary = audio.analyze(
        _clip(_room(0.5), _speech(2.0, NEAR), _room(0.3), _speech(20.0, FAR, 240.0)),
        SR,
    )
    assert summary.has_background_speech
    assert audio.confidence(summary) <= 0.9


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #


def test_background_speech_becomes_one_advisory_event():
    summary = audio.analyze(
        _clip(
            _room(0.5),
            _speech(2.0, NEAR),
            _room(0.2),
            _speech(2.0, FAR, 240.0),
            _room(0.2),
            _speech(2.0, NEAR),
        ),
        SR,
    )
    events = RuleEngine().audio_events(summary)

    assert [event.kind for event in events] == [KIND_BACKGROUND_VOICE]
    event = events[0]
    assert event.severity == "medium"  # never critical: it cannot identify a speaker
    assert not event.snapshot_requested  # audio events have no frame to save
    assert event.detail["background_seconds"] > 0


def test_a_transcript_from_a_silent_recording_is_reported():
    summary = audio.analyze(_room(5.0), SR)
    events = RuleEngine().audio_events(summary)
    assert [event.kind for event in events] == [KIND_NO_SPEECH]
    assert events[0].severity == "medium"


def test_an_ordinary_answer_produces_no_audio_events():
    summary = audio.analyze(
        _clip(_room(0.5), _speech(2.5, NEAR), _room(0.8), _speech(2.0, NEAR)), SR
    )
    assert RuleEngine().audio_events(summary) == []
