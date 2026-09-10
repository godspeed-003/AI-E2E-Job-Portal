"""Debounce per-frame signals into timed events with severity weights.

Each ``Rule`` watches one signal (e.g. "no face present") across frames and
fires an event only when the signal has been continuously active for longer than
its threshold. This prevents a single dropped frame from generating noise while
still catching a candidate who looks away for four seconds.

Rules are stateless between interviews — create a fresh ``RuleEngine`` per
session so timers do not bleed across candidates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.config import settings
from proctoring.analyzer import FrameAnalysis

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

def compute_integrity_score(events: list[dict[str, Any]]) -> tuple[int, str]:
    """Aggregate persisted event rows into a 0–100 score and a verdict.

    The score starts at 100 and has weighted deductions subtracted; it is
    clamped to 0. Repeats of the same *kind* compound with diminishing returns —
    the first costs full weight, every later one costs half — so a candidate on
    a flaky webcam that drops six no-face events is not punished as if six
    separate things went wrong, while a genuinely repeated signal still climbs.

    A single ``critical`` event (candidate substitution) never scores ``clean``:
    its weight alone leaves the score in the eighties, but the whole point of
    that signal is that a human should look at the recording. It floors the
    verdict at ``review`` regardless of the arithmetic.

    Verdict labels are ``clean``, ``review``, or ``flag``.
    """
    deduction = 0.0
    seen: set[str] = set()
    has_critical = False

    for row in events:
        severity = row.get("severity", "low")
        weight = SEVERITY_WEIGHTS.get(severity, 1.0)
        kind = row.get("kind", "")
        if kind in seen:
            weight /= 2.0
        seen.add(kind)
        deduction += weight
        if severity == "critical":
            has_critical = True

    score = max(0, min(100, round(100 - deduction)))
    threshold = settings.proctoring.integrity_fail_below
    if score < threshold:
        verdict = "flag"
    elif score >= 80 and not has_critical:
        verdict = "clean"
    else:
        verdict = "review"

    return score, verdict
