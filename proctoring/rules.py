"""Debounce per-frame signals into timed events with severity weights.

Each ``Rule`` watches one signal (e.g. "no face present") across frames and
fires an event only when the signal has been continuously active for longer than
its threshold. This prevents a single dropped frame from generating noise while
still catching a candidate who looks away for four seconds.

Rules are stateless between interviews — create a fresh ``RuleEngine`` per
session so timers do not bleed across candidates.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from core.config import settings
from proctoring import audio
from proctoring.analyzer import FrameAnalysis
from proctoring.audio import AudioSummary

# ── event kinds ──────────────────────────────────────────────────────────── #
# These are the string keys stored in proctor_events.kind and shown to the
# recruiter. Keep them stable — they are also used in tests and the report UI.

KIND_NO_FACE           = "no_face"
KIND_MULTIPLE_FACES    = "multiple_faces"
KIND_LOOKING_AWAY      = "looking_away"
KIND_SUBSTITUTION      = "substitution"
KIND_PHONE             = "object_phone"
KIND_EXTRA_PERSON      = "object_extra_person"
KIND_NOTES             = "object_notes"
KIND_TAB_SWITCH        = "browser_tab_switch"
KIND_WINDOW_BLUR       = "browser_window_blur"
KIND_PASTE             = "browser_paste"
KIND_FULLSCREEN_EXIT   = "browser_fullscreen_exit"
KIND_BACKGROUND_VOICE  = "audio_background_voice"
KIND_NO_SPEECH         = "audio_no_speech"

# severity → weight used when computing the integrity score
SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 12.0,
    "high":      6.0,
    "medium":    3.0,
    "low":       1.0,
}


@dataclass
class ProctoringEvent:
    kind: str
    severity: str
    confidence: float
    detail: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    snapshot_requested: bool = False


# ── per-signal state ──────────────────────────────────────────────────────── #

@dataclass
class _SignalState:
    active: bool = False
    first_seen: float = 0.0     # monotonic time when signal started
    last_event_at: float = 0.0  # avoid duplicate events for a long-running signal


class RuleEngine:
    """Stateful per-interview rule evaluator.

    Call :meth:`evaluate` with each :class:`~proctoring.analyzer.FrameAnalysis`
    to receive a (possibly empty) list of :class:`ProctoringEvent` objects. The
    engine is not thread-safe; wrap it in a lock if the video callback runs on a
    separate thread from the Streamlit script.
    """

    def __init__(
        self,
        enrollment_cosine_threshold: float = 0.363,  # SFace recommended threshold
    ) -> None:
        self._cfg = settings.proctoring
        self._enroll_threshold = enrollment_cosine_threshold

        # One state object per sustained-signal rule.
        self._no_face = _SignalState()
        self._looking_away = _SignalState()

        # De-duplicate object / substitution events by suppressing re-fire within
        # this many seconds.
        self._object_cooldown: dict[str, float] = {}
        self._object_cooldown_secs = 15.0
        self._substitution_cooldown = 0.0

    # ------------------------------------------------------------------ #
    # Main entry point                                                    #
    # ------------------------------------------------------------------ #

    def evaluate(
        self,
        analysis: FrameAnalysis,
        analyzer: Any,          # Analyzer instance for cosine match
        elapsed_seconds: float, # seconds since interview started
    ) -> list[ProctoringEvent]:
        now = time.monotonic()
        events: list[ProctoringEvent] = []

        # --- face count ---
        if analysis.face_count == 0:
            events.extend(
                self._sustained(
                    self._no_face,
                    now,
                    threshold_secs=self._cfg.no_face_seconds,
                    kind=KIND_NO_FACE,
                    severity="high",
                    confidence=1.0,
                    detail={},
                )
            )
        else:
            self._no_face.active = False

        if analysis.face_count > 1:
            events.append(
                self._instant(
                    KIND_MULTIPLE_FACES,
                    "high",
                    confidence=1.0,
                    detail={"count": analysis.face_count},
                )
            )

        # --- head pose ---
        if analysis.head_pose is not None:
            yaw_ok = abs(analysis.head_pose.yaw) <= self._cfg.yaw_limit_degrees
            pitch_ok = abs(analysis.head_pose.pitch) <= self._cfg.pitch_limit_degrees
            looking_away = not (yaw_ok and pitch_ok)
            if looking_away:
                events.extend(
                    self._sustained(
                        self._looking_away,
                        now,
                        threshold_secs=self._cfg.looking_away_seconds,
                        kind=KIND_LOOKING_AWAY,
                        severity="medium",
                        confidence=0.9,
                        detail={
                            "yaw": round(analysis.head_pose.yaw, 1),
                            "pitch": round(analysis.head_pose.pitch, 1),
                        },
                    )
                )
            else:
                self._looking_away.active = False
        else:
            self._looking_away.active = False

        # --- candidate substitution ---
        if analysis.face_embedding is not None:
            cosine = analyzer.substitution_cosine(analysis.face_embedding)
            if cosine is not None and cosine < self._enroll_threshold:
                if now - self._substitution_cooldown > 30.0:
                    self._substitution_cooldown = now
                    events.append(
                        self._instant(
                            KIND_SUBSTITUTION,
                            "critical",
                            confidence=float(1.0 - cosine),
                            detail={"cosine_similarity": round(cosine, 3)},
                        )
                    )

        # --- objects ---
        for obj in analysis.objects:
            kind = {
                "phone":   KIND_PHONE,
                "person":  KIND_EXTRA_PERSON,
                "book":    KIND_NOTES,
                "laptop":  KIND_NOTES,
            }.get(obj.label)
            if kind is None:
                continue
            last = self._object_cooldown.get(kind, 0.0)
            if now - last > self._object_cooldown_secs:
                self._object_cooldown[kind] = now
                events.append(
                    self._instant(
                        kind,
                        severity="high" if kind in (KIND_SUBSTITUTION, KIND_EXTRA_PERSON) else "medium",
                        confidence=obj.confidence,
                        detail={"label": obj.label},
                    )
                )

        return events

    def browser_event(self, kind: str) -> ProctoringEvent:
        """Synthesize an event from a browser-side signal (tab switch, paste…)."""
        severity = {
            KIND_TAB_SWITCH:      "high",
            KIND_WINDOW_BLUR:     "medium",
            KIND_PASTE:           "medium",
            KIND_FULLSCREEN_EXIT: "low",
        }.get(kind, "low")
        return self._instant(kind, severity, confidence=1.0, detail={})

    def audio_events(self, summary: AudioSummary) -> list[ProctoringEvent]:
        """Turn one answer's audio summary into events.

        Called once per *accepted* spoken answer, not per attempt. A recording
        that failed transcription is usually a broken microphone, and a
        candidate should not accumulate integrity events for bad hardware.

        Neither event is above ``medium``. :mod:`proctoring.audio` measures
        loudness, not identity — it cannot distinguish a person feeding answers
        from a television in the next room, so both readings are evidence for a
        reviewer rather than a finding.
        """
        events: list[ProctoringEvent] = []

        if summary.has_background_speech:
            events.append(
                self._instant(
                    KIND_BACKGROUND_VOICE,
                    "medium",
                    confidence=audio.confidence(summary),
                    detail=summary.as_detail(),
                )
            )

        # An accepted transcript from a recording with nothing audible in it:
        # the words reached the transcriber without passing through this
        # microphone. Worth a look; not proof of anything on its own.
        if not summary.spoke:
            events.append(
                self._instant(
                    KIND_NO_SPEECH,
                    "medium",
                    confidence=0.6,
                    detail=summary.as_detail(),
                )
            )

        for event in events:
            event.duration_seconds = round(summary.duration_seconds, 1)
        return events


    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _instant(
        kind: str,
        severity: str,
        confidence: float,
        detail: dict[str, Any],
    ) -> ProctoringEvent:
        return ProctoringEvent(
            kind=kind,
            severity=severity,
            confidence=confidence,
            detail=detail,
            snapshot_requested=(severity in ("critical", "high")),
        )

    def _sustained(
        self,
        state: _SignalState,
        now: float,
        threshold_secs: float,
        kind: str,
        severity: str,
        confidence: float,
        detail: dict[str, Any],
    ) -> list[ProctoringEvent]:
        """Fire at most once per threshold_secs while the signal is active."""
        if not state.active:
            state.active = True
            state.first_seen = now

        elapsed = now - state.first_seen
        if elapsed < threshold_secs:
            return []

        # Re-fire at most once per threshold period to avoid flooding the log.
        if now - state.last_event_at < threshold_secs:
            return []

        state.last_event_at = now
        return [
            ProctoringEvent(
                kind=kind,
                severity=severity,
                confidence=confidence,
                detail=detail,
                duration_seconds=round(elapsed, 1),
                snapshot_requested=(severity in ("critical", "high")),
            )
        ]


# ── integrity scoring ─────────────────────────────────────────────────────── #

# How a repeated kind's cost grows. A kind costs its worst severity scaled by
# ``1 + REPEAT_GROWTH · ln(n)``: sublinear, so the first occurrence dominates
# and the twentieth barely moves the needle, but a signal that never stops does
# eventually cost enough to matter. Same sublinear shape as term-frequency
# scaling, for the same reason — the first observation is informative and the
# hundredth is nearly redundant.
REPEAT_GROWTH = 1.0

# Each additional *distinct* kind beyond the first scales the whole deduction by
# this much, capped. Distinct kinds co-occurring is the discriminator the
# per-event sum was missing: a broken camera produces one kind many times, a
# coached candidate produces several kinds once each.
CO_OCCURRENCE_STEP = 0.25
CO_OCCURRENCE_CAP = 2.0


def compute_integrity_score(events: list[dict[str, Any]]) -> tuple[int, str]:
    """Aggregate persisted event rows into a 0–100 score and a verdict.

    The score starts at 100 and has a weighted deduction subtracted, clamped to
    0. The deduction is built per *distinct kind*, not per event:

    1. **Worst severity wins.** A kind costs the weight of the most severe
       reading of it, not the sum of its readings. One signal that fired eleven
       times is still one thing that went wrong.
    2. **Persistence grows sublinearly.** Repeats scale that weight by
       ``1 + REPEAT_GROWTH · ln(n)``. The second occurrence of a kind costs
       about seven tenths of the first, the tenth about a tenth of it. A flaky
       webcam dropping six no-face events is not treated as six separate
       findings, but a candidate who turns the camera to the wall for the whole
       interview does not score the same as one who blocks it for four seconds.
    3. **Co-occurring kinds escalate.** Each distinct kind beyond the first
       scales the whole deduction by ``CO_OCCURRENCE_STEP``, capped at
       ``CO_OCCURRENCE_CAP``. Independent anomaly types appearing together is
       qualitatively different evidence from one type repeating, and this is the
       only term that distinguishes them.

    A single ``critical`` event (candidate substitution) never scores ``clean``:
    its weight alone leaves the score in the eighties, but the whole point of
    that signal is that a human should look at the recording. It floors the
    verdict at ``review`` regardless of the arithmetic.

    Why it is shaped this way
    -------------------------

    The first version accumulated a deduction per event, halving repeats of the
    same kind. Halving softened a repeated signal without bounding it, so the
    total tracked how *many* events fired rather than how serious the distinct
    signals were, and it inverted the two orderings the design exists to get
    right: six no-face events from a failing webcam scored 79 and were sent for
    human review, while a candidate with a phone, written notes, another voice
    in the room and two glances away scored 86 and was marked clean.

    Scoring each kind at its worst severity fixes that ordering, but on its own
    it makes persistence almost free and the label trivially gameable — an
    earlier draft of this function used a bounded saturation term and scored a
    candidate absent from frame for the entire interview at 88/``clean``, which
    is a worse defect than the one being repaired. Terms 2 and 3 are what make
    the fix safe: sustained absence lands in ``review``, which is the honest
    verdict for a signal that cannot distinguish a broken camera from an empty
    chair. ``metrics/m1_integrity.py`` re-runs the monotonicity sweep and both
    hand-constructed scenarios against this function and reports whether the
    inversion still reproduces, so the repair is a measurement and not a claim.

    Verdict labels are ``clean``, ``review``, or ``flag``.
    """
    worst: dict[str, float] = {}
    counts: dict[str, int] = {}
    has_critical = False

    for row in events:
        severity = row.get("severity", "low")
        kind = row.get("kind", "")
        weight = SEVERITY_WEIGHTS.get(severity, 1.0)
        worst[kind] = max(worst.get(kind, 0.0), weight)
        counts[kind] = counts.get(kind, 0) + 1
        if severity == "critical":
            has_critical = True

    deduction = 0.0
    for kind, weight in worst.items():
        deduction += weight * (1.0 + REPEAT_GROWTH * math.log(counts[kind]))

    distinct = len(worst)
    if distinct > 1:
        deduction *= min(
            CO_OCCURRENCE_CAP, 1.0 + CO_OCCURRENCE_STEP * (distinct - 1)
        )

    score = max(0, min(100, round(100 - deduction)))
    threshold = settings.proctoring.integrity_fail_below
    if score < threshold:
        verdict = "flag"
    elif score >= 80 and not has_critical:
        verdict = "clean"
    else:
        verdict = "review"

    return score, verdict
