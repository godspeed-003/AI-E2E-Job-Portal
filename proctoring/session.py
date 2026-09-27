"""One interview's proctoring session: analyzer, rules, and a thread hand-off.

``streamlit-webrtc`` runs its frame callback on a worker thread, and Streamlit's
``session_state`` belongs to the script thread. Anything that writes to the
database or the UI from inside the callback is a race waiting to happen, so this
module draws the line explicitly:

    worker thread   →  :meth:`ProctorSession.on_frame`  →  queue
    script thread   →  :meth:`ProctorSession.drain`     →  database

The callback only analyses the frame and pushes events onto a bounded queue; the
script thread drains it on the next rerun and does the writing. The queue is
bounded because a stalled script thread must never grow memory without limit —
if it fills, the oldest events are dropped and counted rather than blocking the
video pipeline.

Everything here is failure-tolerant by design. A missing model, a bad frame or a
CV backend that throws mid-interview costs the candidate nothing: the signal goes
quiet and the interview continues. Proctoring is advisory evidence for a human
reviewer, and a crashed detector is not evidence of anything.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

import numpy as np

from core.config import settings
from proctoring import audio
from proctoring.analyzer import Analyzer, FrameAnalysis
from proctoring.rules import ProctoringEvent, RuleEngine
from services import proctor_service as proctor

log = logging.getLogger(__name__)

# Deep enough to absorb a slow rerun, shallow enough that a wedged script thread
# cannot balloon memory. At the default stride this is a couple of minutes of
# events, far more than a real session produces.
_QUEUE_DEPTH = 256


class ProctorSession:
    """Per-interview proctoring state. Create one, keep it for the session.

    The only methods safe to call from the WebRTC worker thread are
    :meth:`on_frame` and :meth:`note_browser_event`. Everything else belongs to
    the script thread.
    """

    def __init__(self, interview_id: int) -> None:
        self.interview_id = interview_id
        self.enabled = settings.proctoring.enabled

        self._analyzer = Analyzer()
        self._rules = RuleEngine()
        self._queue: queue.Queue[tuple[ProctoringEvent, float, np.ndarray | None]] = (
            queue.Queue(maxsize=_QUEUE_DEPTH)
        )
        # The rule engine carries per-signal timers; the worker thread owns it,
        # but enroll() and browser events reach it from the script thread too.
        self._lock = threading.Lock()
        self._started = time.monotonic()

        self._dropped = 0          # events lost to a full queue
        self._persisted = 0        # events written to the database
        self._enrolled = False
        self._last_analysis: FrameAnalysis | None = None

    # ------------------------------------------------------------------ #
    # Script thread                                                       #
    # ------------------------------------------------------------------ #

    def enroll(self, frame: np.ndarray) -> bool:
        """Capture the reference face from the consent screen.

        Returns False when no usable face was found, which is a prompt to the
        candidate to re-centre — not an integrity event. Substitution detection
        simply stays off for the rest of the session.
        """
        if not self.enabled:
            return False
        try:
            with self._lock:
                ok = self._analyzer.enroll(frame)
        except Exception as exc:
            log.debug("Enrollment failed: %s", exc)
            return False

        if ok:
            self._enrolled = True
            try:
                proctor.save_enrollment_snapshot(self.interview_id, frame)
            except Exception as exc:
                log.debug("Enrollment snapshot failed: %s", exc)
        return ok

    def drain(self) -> int:
        """Persist everything the worker thread queued. Returns rows written.

        Called on every rerun. Safe to call when nothing is pending.
        """
        written = 0
        while True:
            try:
                event, elapsed, snapshot = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                proctor.record_event(
                    self.interview_id, event, elapsed, snapshot_frame=snapshot
                )
                written += 1
            except Exception as exc:
                # A failed write must not take the interview down with it.
                log.warning("Could not persist proctor event %s: %s", event.kind, exc)
        self._persisted += written
        return written

    def note_browser_event(self, kind: str) -> None:
        """Record a signal the browser reported (tab switch, paste, blur)."""
        if not self.enabled:
            return
        with self._lock:
            event = self._rules.browser_event(kind)
        self._offer(event, self._elapsed(), None)

    def note_answer_audio(self, samples: np.ndarray, sample_rate: int) -> int:
        """Analyse one accepted answer's microphone audio. Returns events queued.

        Script-thread only, and called *after* the answer is accepted: see
        :meth:`RuleEngine.audio_events` for why a failed transcription must not
        produce integrity events.

        Video rules cannot see someone sitting off-camera reading answers aloud.
        This is the only signal in the system that can, which is also why it is
        wrapped in the same "never break the interview" try as everything else —
        a numpy error on an odd buffer is not worth a candidate's session.
        """
        if not self.enabled:
            return 0
        try:
            summary = audio.analyze(samples, sample_rate)
            with self._lock:
                events = self._rules.audio_events(summary)
        except Exception as exc:
            log.debug("Answer audio analysis failed: %s", exc)
            return 0

        elapsed = self._elapsed()
        for event in events:
            self._offer(event, elapsed, None)
        return len(events)


    def finalize(self) -> tuple[int, str]:
        """Flush anything pending, then lock in the integrity score."""
        self.drain()
        return proctor.finalize(self.interview_id)

    @property
    def status(self) -> dict[str, Any]:
        """Snapshot for the UI — never raises, never blocks."""
        analysis = self._last_analysis
        return {
            "enabled": self.enabled,
            "enrolled": self._enrolled,
            "persisted": self._persisted,
            "dropped": self._dropped,
            "face_count": analysis.face_count if analysis else None,
        }

    # ------------------------------------------------------------------ #
    # Worker thread                                                       #
    # ------------------------------------------------------------------ #

    def on_frame(self, frame: Any) -> Any:
        """WebRTC video callback. Returns the frame untouched, always.

        This runs on the media thread: it must never raise, never block on the
        script thread, and never touch ``st.session_state``.
        """
        if not self.enabled:
            return frame
        try:
            image = frame.to_ndarray(format="bgr24")
        except Exception:
            return frame

        try:
            self._process(image)
        except Exception as exc:
            # Detector blew up on this frame. Skip it; keep the interview alive.
            log.debug("Frame analysis failed: %s", exc)
        return frame

    def _process(self, image: np.ndarray) -> None:
        with self._lock:
            analysis = self._analyzer.analyze(image)
            if analysis is None:
                return  # stride: this frame was skipped on purpose
            self._last_analysis = analysis
            elapsed = self._elapsed()
            events = self._rules.evaluate(analysis, self._analyzer, elapsed)

        for event in events:
            snapshot = image if event.snapshot_requested else None
            self._offer(event, elapsed, snapshot)

    # ------------------------------------------------------------------ #
    # Internal                                                            #
    # ------------------------------------------------------------------ #

    def _elapsed(self) -> float:
        return time.monotonic() - self._started

    def _offer(
        self,
        event: ProctoringEvent,
        elapsed: float,
        snapshot: np.ndarray | None,
    ) -> None:
        """Queue an event without ever blocking the caller."""
        try:
            self._queue.put_nowait((event, elapsed, snapshot))
        except queue.Full:
            self._dropped += 1
            log.warning(
                "Proctor queue full; dropped %s (total dropped: %d)",
                event.kind,
                self._dropped,
            )
