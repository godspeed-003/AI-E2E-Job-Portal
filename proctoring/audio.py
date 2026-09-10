"""Energy analysis of an answer's microphone audio.

The camera can see a second person only if they step into frame. A prompter
sitting off-camera reading answers aloud is invisible to every video rule in
:mod:`proctoring.rules`, and that is the gap this module covers.

**What it can and cannot do.** This is loudness arithmetic over 30 ms frames —
no model, no dependency beyond numpy, and no speaker identification of any
kind. It cannot tell you *who* spoke; it can only tell you that some of the
speech in the clip arrived at a level far below the candidate's own voice and
was not adjacent to it. A television, a housemate in the next room, or a
speakerphone all produce the same reading. Both signals it emits are therefore
advisory and neither is ``critical``: they belong in the recruiter's evidence
list next to the recording, not in an automated decision.

The near-field assumption is the whole trick. A candidate speaking into their
own laptop mic is 30–50 cm away; anyone else in the room is metres away, and
the inverse-square law puts them 15–25 dB down. So:

* frames within :data:`NEAR_FIELD_DB` of the clip's loudest speech are the
  candidate,
* frames clearly above the room's noise floor but well below the candidate are
  *distant speech*,
* distant speech only counts when it runs for over a second and is not within
  :data:`GUARD_SECONDS` of the candidate's own voice — otherwise every trailing
  syllable, breath and lean-back would register as a second speaker.

The guard band is what makes the signal usable rather than a false-positive
generator, so it is deliberately generous.

One consequence of measuring *relative* level is worth stating plainly: if the
only voice in the clip is a distant one, it becomes the peak and reads as the
candidate. A candidate sitting far back from the laptop and a prompter alone in
the room produce the same recording, and nothing in a loudness measurement can
separate them. This detects a second voice alongside the candidate's, not a
substituted one — :data:`~proctoring.rules.KIND_SUBSTITUTION` from the video
side is what covers that case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ── tuning ────────────────────────────────────────────────────────────────── #
# Deliberately module constants rather than environment settings: these are
# properties of how microphones and rooms behave, not deployment choices, and a
# half-tuned .env would quietly break the guard band above.

FRAME_SECONDS = 0.030        # analysis hop; ~1 syllable is several frames
SILENCE_DB = -60.0           # floor for log10 of an all-zero frame
VOICE_MARGIN_DB = 12.0       # above the noise floor before a frame is "sound"
NEAR_FIELD_DB = 12.0         # within this of the clip peak ⇒ the candidate
GUARD_SECONDS = 0.40         # distant speech this close to the candidate's own
                             # voice is their trailing syllable, not a second person
SPEECH_GAP_SECONDS = 0.20    # pauses this short are inside an utterance, not
                             # between two of them (must stay < 2 × GUARD_SECONDS)
MIN_BACKGROUND_RUN = 1.00    # a distant run shorter than this is a door, a
                             # cough, a chair — not someone talking
MIN_DYNAMIC_RANGE_DB = 15.0  # peak-to-floor below this means the clip is all
                             # room tone: nobody spoke, so nothing can be said
                             # about who did
MIN_VOICED_SECONDS = 0.50    # less voiced audio than this ⇒ no answer was spoken


@dataclass(frozen=True)
class AudioSummary:
    """What one answer's audio looked like. All seconds, all measured."""

    duration_seconds: float
    voiced_seconds: float
    near_field_seconds: float
    background_seconds: float
    noise_floor_db: float
    peak_db: float

    @property
    def spoke(self) -> bool:
        """Did anyone say anything into this microphone?"""
        return self.voiced_seconds >= MIN_VOICED_SECONDS

    @property
    def has_background_speech(self) -> bool:
        return self.background_seconds >= MIN_BACKGROUND_RUN

    @property
    def dynamic_range_db(self) -> float:
        return self.peak_db - self.noise_floor_db

    def as_detail(self) -> dict[str, float]:
        """The numbers a reviewer needs to judge the signal for themselves."""
        return {
            "duration_seconds": round(self.duration_seconds, 1),
            "voiced_seconds": round(self.voiced_seconds, 1),
            "background_seconds": round(self.background_seconds, 1),
            "noise_floor_db": round(self.noise_floor_db, 1),
            "peak_db": round(self.peak_db, 1),
        }


def _empty(duration: float = 0.0) -> AudioSummary:
    return AudioSummary(
        duration_seconds=duration,
        voiced_seconds=0.0,
        near_field_seconds=0.0,
        background_seconds=0.0,
        noise_floor_db=SILENCE_DB,
        peak_db=SILENCE_DB,
    )


def _frame_db(samples: np.ndarray, frame_len: int) -> np.ndarray:
    """Per-frame RMS in dBFS, floored at :data:`SILENCE_DB`."""
    usable = (len(samples) // frame_len) * frame_len
    frames = samples[:usable].reshape(-1, frame_len)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    # log10(0) is -inf and would poison every percentile below it.
    floor = 10.0 ** (SILENCE_DB / 20.0)
    return 20.0 * np.log10(np.maximum(rms, floor))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous ``True`` spans of a boolean array as ``[start, end)`` pairs."""
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Widen every ``True`` by ``radius`` frames in both directions."""
    if radius <= 0 or not mask.any():
        return mask
    widened = mask.copy()
    for shift in range(1, radius + 1):
        widened[shift:] |= mask[:-shift]
        widened[:-shift] |= mask[shift:]
    return widened


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    """Bridge ``False`` runs shorter than ``max_gap`` frames.

    Speech is not continuous — it stops between words and inside stopped
    consonants. Without this, a sentence measures as a dozen fragments of a
    fifth of a second each and never clears :data:`MIN_BACKGROUND_RUN`.

    ``max_gap`` must stay well under ``2 × GUARD_SECONDS``, or closing could
    bridge two distant fragments straddling the candidate's own speech and
    report their sum as one continuous background utterance.
    """
    if max_gap <= 0:
        return mask
    closed = mask.copy()
    for start, end in _runs(~mask):
        # Leading and trailing silence is not a gap between anything.
        if start == 0 or end == len(mask):
            continue
        if end - start <= max_gap:
            closed[start:end] = True
    return closed


def analyze(samples: np.ndarray, sample_rate: int) -> AudioSummary:
    """Summarise one answer's PCM. Never raises on odd input — returns zeros.

    ``samples`` is mono float32 in [-1, 1], the shape ``_drain_audio`` in the
    interview room produces.
    """
    if sample_rate <= 0 or samples is None or samples.size == 0:
        return _empty()

    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    duration = len(mono) / float(sample_rate)

    frame_len = max(1, int(round(FRAME_SECONDS * sample_rate)))
    if len(mono) < frame_len * 2:
        return _empty(duration)

    db = _frame_db(mono, frame_len)
    seconds_per_frame = frame_len / float(sample_rate)

    # The 10th percentile is the room with nobody talking; the 95th is the
    # candidate at their loudest. Using percentiles rather than min/max keeps a
    # single click or dropout from defining either end.
    noise_floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))

    summary_floor, summary_peak = noise_floor, peak
    if peak - noise_floor < MIN_DYNAMIC_RANGE_DB:
        # Flat clip: silence, or a fan running. There is no speech to attribute.
        return AudioSummary(
            duration_seconds=duration,
            voiced_seconds=0.0,
            near_field_seconds=0.0,
            background_seconds=0.0,
            noise_floor_db=summary_floor,
            peak_db=summary_peak,
        )

    voiced = db > noise_floor + VOICE_MARGIN_DB
    near_field = voiced & (db >= peak - NEAR_FIELD_DB)

    # Anything voiced but distant, once the candidate's own speech and its
    # trailing edge are excluded.
    guard = _dilate(near_field, int(round(GUARD_SECONDS / seconds_per_frame)))
    distant = voiced & ~guard
    distant = _close_gaps(distant, int(round(SPEECH_GAP_SECONDS / seconds_per_frame)))

    min_run_frames = int(round(MIN_BACKGROUND_RUN / seconds_per_frame))
    background_frames = sum(
        end - start for start, end in _runs(distant) if end - start >= min_run_frames
    )

    return AudioSummary(
        duration_seconds=duration,
        voiced_seconds=float(voiced.sum()) * seconds_per_frame,
        near_field_seconds=float(near_field.sum()) * seconds_per_frame,
        background_seconds=background_frames * seconds_per_frame,
        noise_floor_db=summary_floor,
        peak_db=summary_peak,
    )


def confidence(summary: AudioSummary) -> float:
    """How far past the bar the background reading is, capped at 0.9.

    Never 1.0: an energy heuristic that cannot name a speaker has no business
    reporting certainty.
    """
    if not summary.has_background_speech:
        return 0.0
    excess = summary.background_seconds / MIN_BACKGROUND_RUN
    return round(min(0.9, 0.4 + 0.1 * math.log2(max(excess, 1.0)) * 2), 2)
