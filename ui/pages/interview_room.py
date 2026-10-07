"""Video interview room.

One screen for the whole interview: consent → live Q&A → score reveal.
The layout is transport-agnostic: ``streamlit-webrtc`` captures video and audio;
``speech/stt`` transcribes the audio buffer; ``speech/tts`` reads each question
aloud; ``services/interview_service`` owns all interview state so a browser
refresh or a dropped WebRTC connection resumes on the same question.

The page is reached by deep-linking an ``interview_id`` query parameter. Any
attempt to open another user's interview is turned away by
:func:`~services.interview_service.require`.
"""

from __future__ import annotations

import io
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import av
import numpy as np
import streamlit as st
import streamlit.components.v1 as components

from core.config import settings
from proctoring.session import ProctorSession
from services import interview_service as interviews
from services import recording_service as recordings
from services.interview_service import (
    AnswerOutcome,
    Interview,
    InterviewError,
    Turn,
)
from speech import stt as stt_module
from speech import tts as tts_module
from ui import session, theme

log = logging.getLogger(__name__)

# ---- session-state keys --------------------------------------------------- #
_KEY_IID = "_room_interview_id"       # int: which interview we are in
_KEY_PHASE = "_room_phase"            # str: consent | interview | done
_KEY_RECORDING = "_room_recording"    # bool: mic capture active
_KEY_AUDIO_BUF = "_room_audio_buf"    # list[np.ndarray]: accumulated frames
_KEY_OUTCOME = "_room_outcome"        # AnswerOutcome | None: last submit result
_KEY_TTS_TEXT = "_room_tts_text"      # str: question to speak on next render
_KEY_TYPED = "_room_typed"            # str: fallback typed answer
_KEY_WR_CTX = "_room_wr_ctx"          # WebRtcStreamerContext | None
_KEY_FLASH = "_room_flash"            # (kind, msg) | None
_KEY_PROCTOR = "_room_proctor"        # ProctorSession | None
_KEY_LAST_FRAME = "_room_last_frame"  # [np.ndarray | None]: newest preview frame


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _flash(kind: str, msg: str) -> None:
    st.session_state[_KEY_FLASH] = (kind, msg)


def _show_flash() -> None:
    item = st.session_state.pop(_KEY_FLASH, None)
    if not item:
        return
    kind, msg = item
    {"success": st.success, "warning": st.warning, "error": st.error}.get(
        kind, st.info
    )(msg)


def _phase() -> str:
    return st.session_state.get(_KEY_PHASE, "consent")


def _set_phase(p: str) -> None:
    st.session_state[_KEY_PHASE] = p


def _interview_id() -> int | None:
    return st.session_state.get(_KEY_IID)


def _fmt_seconds(s: int | None) -> str:
    if s is None:
        return "—"
    m, sec = divmod(max(0, s), 60)
    return f"{m}:{sec:02d}"


def _fmt_window(interview: Interview) -> str:
    if interview.closes_at:
        return interview.closes_at[:10]
    return "no deadline"


# --------------------------------------------------------------------------- #
# Audio processor — runs in the WebRTC callback thread
# --------------------------------------------------------------------------- #


class _AudioSink:
    """Accumulate audio frames into a shared buffer."""

    def __init__(self, buf: list[np.ndarray]) -> None:
        self._buf = buf

    def recv(self, frame: av.AudioFrame) -> av.AudioFrame:
        arr = frame.to_ndarray()
        # Flatten to mono float32 in [-1, 1]
        if arr.ndim > 1:
            arr = arr.mean(axis=0)
        self._buf.append(arr.astype(np.float32))
        return frame


def _drain_audio() -> np.ndarray | None:
    """Collect and clear the audio buffer. Returns mono float32 array or None."""
    buf: list[np.ndarray] = st.session_state.get(_KEY_AUDIO_BUF, [])
    if not buf:
        return None
    combined = np.concatenate(buf)
    st.session_state[_KEY_AUDIO_BUF] = []
    return combined


# --------------------------------------------------------------------------- #
# Proctoring — one session per interview, drained on every rerun
# --------------------------------------------------------------------------- #


def _proctor(interview_id: int) -> ProctorSession | None:
    """The proctoring session for this interview, created on first use.

    Returns None when proctoring is switched off, so every call site reads as
    ``if p:`` and the feature can be disabled entirely from the environment.
    The session is cached in ``session_state`` because its rule engine holds
    per-signal timers that must survive a rerun — rebuilding it each time would
    reset every debounce window and turn one long absence into many events.
    """
    if not settings.proctoring.enabled:
        return None

    existing: ProctorSession | None = st.session_state.get(_KEY_PROCTOR)
    if existing is not None and existing.interview_id == interview_id:
        return existing

    try:
        fresh = ProctorSession(interview_id)
    except Exception as exc:
        # Proctoring must never be the reason a candidate cannot interview.
        log.warning("Proctoring unavailable: %s", exc)
        return None

    st.session_state[_KEY_PROCTOR] = fresh
    return fresh


def _drain_proctor() -> None:
    """Persist anything the media thread queued since the last rerun."""
    p: ProctorSession | None = st.session_state.get(_KEY_PROCTOR)
    if p is None:
        return
    try:
        p.drain()
    except Exception as exc:
        log.warning("Proctor drain failed: %s", exc)


def _browser_signal_watcher() -> None:
    """Report tab switches, focus loss and pastes back to Streamlit.

    The browser is the only place that can see these, and it cannot call Python
    directly. Each signal is appended to the page URL's ``proctor`` parameter,
    which Streamlit surfaces as a query param on the next rerun; ``_collect_
    browser_signals`` reads and clears it.

    This is best-effort by nature — a candidate who disables JavaScript simply
    produces no browser signals, and the camera-side rules still apply.
    """
    components.html(
        """
        <script>
        (function () {
          const top = window.parent;
          if (!top || top.__proctorWatching) return;
          top.__proctorWatching = true;

          function report(kind) {
            try {
              const url = new URL(top.location);
              const seen = url.searchParams.get('proctor');
              url.searchParams.set('proctor', seen ? seen + ',' + kind : kind);
              top.history.replaceState({}, '', url);
            } catch (e) { /* cross-origin or sandboxed: give up quietly */ }
          }

          top.document.addEventListener('visibilitychange', function () {
            if (top.document.hidden) report('browser_tab_switch');
          });
          top.addEventListener('blur', function () { report('browser_window_blur'); });
          top.addEventListener('paste', function () { report('browser_paste'); });
        })();
        </script>
        """,
        height=0,
    )


def _collect_browser_signals(p: ProctorSession | None) -> None:
    """Drain the ``proctor`` query parameter into real events."""
    raw = st.query_params.get("proctor")
    if not raw:
        return
    del st.query_params["proctor"]
    if p is None:
        return
    for kind in (k.strip() for k in raw.split(",")):
        if kind:
            p.note_browser_event(kind)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _raw_param(name: str) -> str | None:
    raw = st.query_params.get(name)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    if raw is None or raw == "":
        return None
    return str(raw)


def _resolve_interview_id(user: Any) -> int | None:
    """Which interview this page should open.

    Sidebar navigation and ``st.switch_page`` often arrive here with no
    ``?interview_id=``. The candidate still has an interview — pick the live
    one rather than showing an empty room.
    """
    raw = _raw_param("interview_id") or st.session_state.get(_KEY_IID)
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    mine = interviews.for_user(user.id)
    live = [item for item in mine if item.status in ("pending", "in_progress")]
    if not live:
        return None
    in_progress = [item for item in live if item.status == "in_progress"]
    return (in_progress or live)[0].id


def render() -> None:
    theme.inject()
    user = session.require("candidate", "admin")
    if session.sandbox_view():
        theme.sandbox_banner("interview answers are flagged sandbox")

    iid = _resolve_interview_id(user)
    if iid is None:
        _no_interview_selected()
        return

    # Persist so a rerun, a sidebar click, or a dropped query param still lands
    # on the same interview. Only write the query param when it is missing —
    # assigning it always would trigger a Streamlit rerun loop.
    st.session_state[_KEY_IID] = iid
    if _raw_param("interview_id") != str(iid):
        st.query_params["interview_id"] = str(iid)

    try:
        interview = interviews.require(iid, user.id)
    except InterviewError as exc:
        theme.page_header("Interview", icon="🎥")
        st.error(str(exc))
        return

    _show_flash()

    phase = _phase()

    # Completed interviews jump straight to the score reveal.
    if interview.is_completed:
        _phase_done(interview)
        return

    if phase == "interview" and interview.is_live:
        _phase_interview(user, interview)
    elif phase == "done" or interview.is_completed:
        _phase_done(interview)
    else:
        _phase_consent(user, interview)


# --------------------------------------------------------------------------- #
# Phase: consent / device check
# --------------------------------------------------------------------------- #


def _no_interview_selected() -> None:
    theme.page_header("Interview room", "No interview selected.", icon="🎥")
    st.info(
        "Return to your home page or the apply page to find your interview."
    )


def _phase_consent(user: Any, interview: Interview) -> None:
    theme.page_header(
        "Ready for your interview?",
        "Check your camera and microphone, then agree to the recording policy.",
        icon="🎥",
    )

    # Window guard — show a friendly message rather than letting start() error.
    ws = interview.window_state
    if ws == "before":
        opens = interview.opens_at[:10] if interview.opens_at else "soon"
        st.warning(
            f"Your interview window has not opened yet. Come back on **{opens}**."
        )
        return
    if ws == "closed":
        st.error(
            "The interview window has closed. "
            "Contact the recruiter if you believe this is an error."
        )
        return
    if interview.attempts_left <= 0:
        st.error(
            "You have already used your interview attempt. "
            "Contact the recruiter if you believe this is an error."
        )
        return

    col_info, col_prep = st.columns([3, 2], gap="large")

    with col_info:
        st.markdown("##### About this interview")
        theme.html_block(
            theme.kv(
                [
                    ("Questions", str(interview.planned_questions)),
                    ("Max duration", f"{interview.duration_limit_seconds // 60} min"),
                    ("Window closes", _fmt_window(interview)),
                    ("Attempts left", str(interview.attempts_left)),
                ]
            )
        )
        st.write("")
        st.markdown("##### How it works")
        st.markdown(
            "- The AI interviewer reads each question aloud and shows it on screen.\n"
            "- Record your answer with the microphone button, or type it below.\n"
            "- The transcript is shown to you before you submit.\n"
            "- Your camera is recorded for integrity review — look straight ahead.\n"
            "- Do not switch tabs or leave the page during the interview."
        )

    with col_prep:
        st.markdown("##### Device check")
        _webrtc_preview_only(_proctor(interview.id))

    st.divider()

    consent = st.checkbox(
        "I consent to this session being recorded for review purposes.",
        key="_room_consent_box",
    )

    col_go, _ = st.columns([2, 3])
    with col_go:
        if st.button(
            "Start interview",
            disabled=not consent,
            type="primary",
            use_container_width=True,
        ):
            try:
                interviews.start(iid := interview.id, user_id=user.id, consent=True)
                _enroll_from_preview(_proctor(iid))
                _set_phase("interview")
                st.session_state[_KEY_AUDIO_BUF] = []
                st.session_state[_KEY_OUTCOME] = None
                st.session_state[_KEY_TYPED] = ""
                # Trigger the first question on the very next render.
                st.rerun()
            except InterviewError as exc:
                st.error(str(exc))


def _webrtc_preview_only(p: ProctorSession | None) -> None:
    """Camera/mic preview for the consent screen.

    Doubles as the enrollment step: the callback keeps the most recent frame so
    that pressing *Start interview* can capture a reference face without making
    the candidate pose for a separate photo. The holder is a one-slot list
    because the callback runs on the media thread — it only ever assigns, and
    the script thread only ever reads, so no lock is needed.
    """
    try:
        from streamlit_webrtc import webrtc_streamer, WebRtcMode

        holder: list[Any] = st.session_state.setdefault(_KEY_LAST_FRAME, [None])

        def _keep(frame: Any) -> Any:
            try:
                holder[0] = frame.to_ndarray(format="bgr24")
            except Exception:
                pass
            return frame

        ctx = webrtc_streamer(
            key="room_preview",
            mode=WebRtcMode.SENDONLY,
            media_stream_constraints={"video": True, "audio": True},
            video_frame_callback=(_keep if p else None),
            rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
            video_html_attrs={"style": "width:100%;border-radius:12px"},
        )
        if ctx and ctx.state.playing:
            st.success("Camera and microphone detected.")
        else:
            st.info("Allow camera/microphone access to check your setup.")
    except Exception as exc:
        log.debug("webrtc preview unavailable: %s", exc)
        st.info("Camera preview requires browser access. You can still type answers.")


def _enroll_from_preview(p: ProctorSession | None) -> None:
    """Capture the reference face, if there is one to capture.

    Deliberately non-blocking: a candidate whose camera is off, or whose face
    the detector cannot find, still starts the interview. They simply lose the
    substitution check, which is a weaker signal than a refused interview.
    """
    if p is None:
        return
    frame = st.session_state.get(_KEY_LAST_FRAME, [None])[0]
    if frame is None:
        return
    try:
        p.enroll(frame)
    except Exception as exc:
        log.debug("Enrollment skipped: %s", exc)


# --------------------------------------------------------------------------- #
# Phase: live interview
# --------------------------------------------------------------------------- #


def _phase_interview(user: Any, interview: Interview) -> None:
    answered, budget = interviews.progress(interview)

    # Proctoring housekeeping, before anything can rerun out from under us:
    # write what the media thread queued, then fold in the browser's signals.
    p = _proctor(interview.id)
    _drain_proctor()
    _collect_browser_signals(p)
    if p is not None:
        _browser_signal_watcher()

    # Out-of-time guard — the service auto-finishes on ask_next, but catch
    # a stale render before any submission.
    if interview.out_of_time:
        try:
            interviews.finish(interview.id, reason="time_limit")
        except InterviewError:
            pass
        _finalize_proctoring(interview.id)
        _set_phase("done")
        _flash("warning", "Time ran out — your interview has been submitted.")
        st.rerun()
        return

    theme.page_header(
        "Interview in progress",
        "Answer every question. Your progress is saved after each answer.",
        icon="🎤",
    )

    # ---- HUD ----------------------------------------------------------------
    seconds = interview.seconds_left
    time_tone = "warning" if (seconds is not None and seconds < 120) else "info"
    theme.html_block(
        theme.hud(
            [
                ("Question", f"{answered + 1} of {budget}"),
                ("Time left", _fmt_seconds(seconds)),
            ]
        )
    )

    # Low-time alert.
    if seconds is not None and seconds < 60:
        st.warning("⚠ Less than 1 minute remaining — wrap up your current answer.")

    st.write("")

    # ---- Get or create the current question ---------------------------------
    turn = interviews.current_turn(interview.id)
    if turn is None:
        # No unanswered turn — ask next.
        try:
            turn = interviews.ask_next(interview.id)
        except InterviewError as exc:
            st.error(str(exc))
            return

    if turn is None:
        # ask_next returned None ⇒ interview finished.
        _finalize_proctoring(interview.id)
        _set_phase("done")
        st.rerun()
        return

    # ---- Display question ---------------------------------------------------
    theme.html_block(theme.question_block(turn.question))

    # Speak the question once per new turn — detected by comparing the turn id
    # to the last spoken id.
    _maybe_speak(turn)

    st.write("")

    # ---- Prior outcome message from last submit -----------------------------
    outcome: AnswerOutcome | None = st.session_state.get(_KEY_OUTCOME)
    if outcome is not None and not outcome.accepted:
        if "too_short" in outcome.flags:
            st.warning(outcome.message)
        else:
            st.error(outcome.message)
        # Wipe so it doesn't repeat on the next rerun.
        st.session_state[_KEY_OUTCOME] = None

    # ---- WebRTC capture + text fallback ------------------------------------
    col_capture, col_typed = st.columns([1, 1], gap="large")

    with col_capture:
        _webrtc_capture_panel(interview)

    with col_typed:
        _typed_answer_panel(user, interview, turn)

    # ---- Transcript / previous turns ---------------------------------------
    st.divider()
    _transcript_panel(interview)


def _maybe_speak(turn: Turn) -> None:
    """Speak the question once when it first appears."""
    last_spoken_key = "_room_last_spoken_turn"
    if st.session_state.get(last_spoken_key) == turn.id:
        return
    st.session_state[last_spoken_key] = turn.id
    try:
        tts_module.speak(turn.question)
    except Exception as exc:
        log.debug("TTS unavailable: %s", exc)


def _finalize_proctoring(interview_id: int) -> None:
    """Flush and score the session once, at the end. Never fatal.

    The candidate's interview is already submitted by the time this runs, so a
    failure here costs the recruiter an integrity report — not the candidate
    their interview.
    """
    p: ProctorSession | None = st.session_state.get(_KEY_PROCTOR)
    if p is None or p.interview_id != interview_id:
        return
    try:
        p.finalize()
    except Exception as exc:
        log.warning("Could not finalize proctoring for %s: %s", interview_id, exc)
    finally:
        st.session_state.pop(_KEY_PROCTOR, None)
        st.session_state.pop(_KEY_LAST_FRAME, None)


def _webrtc_capture_panel(interview: Interview) -> None:
    st.markdown("##### 🎙 Spoken answer")
    st.caption(
        "Press **Start** to record. Press **Stop**, then **Submit recording** "
        "when you are done."
    )
    p = _proctor(interview.id)
    try:
        from streamlit_webrtc import webrtc_streamer, WebRtcMode, AudioProcessorBase

        buf: list[np.ndarray] = st.session_state.setdefault(_KEY_AUDIO_BUF, [])

        class _Sink(AudioProcessorBase):
            def recv(self, frame: av.AudioFrame) -> av.AudioFrame:  # type: ignore[override]
                arr = frame.to_ndarray()
                if arr.ndim > 1:
                    arr = arr.mean(axis=0)
                buf.append(arr.astype(np.float32))
                return frame

        ctx = webrtc_streamer(
            key="room_capture",
            mode=WebRtcMode.SENDONLY,
            media_stream_constraints={"video": True, "audio": True},
            audio_processor_factory=_Sink,
            # Runs on the media thread: analyses the frame and queues events.
            # Returns the frame untouched, so the video path is unaffected.
            video_frame_callback=(p.on_frame if p else None),
            rtc_configuration={
                "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]
            },
            video_html_attrs={"style": "width:100%;border-radius:12px"},
        )
        st.session_state[_KEY_WR_CTX] = ctx

        recording = ctx is not None and ctx.state.playing
        if recording:
            st.info("🔴 Recording…")
        elif buf:
            st.success(f"Recording ready ({len(buf)} frames captured).")

        if p is not None and not recording:
            # Camera off mid-interview is worth saying plainly — it is the one
            # thing a candidate can fix, and every rule depends on it.
            st.caption("Camera off — start it so your session can be verified.")

    except Exception as exc:
        log.debug("webrtc unavailable: %s", exc)
        st.info("Camera/microphone not available — use the text box on the right.")


def _typed_answer_panel(user: Any, interview: Interview, turn: Turn) -> None:
    st.markdown("##### ⌨ Type your answer")
    st.caption("Use this if you prefer to type, or if your microphone is unavailable.")

    typed = st.text_area(
        "Your answer",
        key="_room_answer_textarea",
        height=160,
        label_visibility="collapsed",
        placeholder="Write your answer here…",
    )

    col_sub, col_send = st.columns([1, 1], gap="small")

    with col_sub:
        if st.button(
            "Submit recording",
            key="_room_submit_audio",
            use_container_width=True,
            disabled=not st.session_state.get(_KEY_AUDIO_BUF),
        ):
            _submit_audio(user, interview, turn)

    with col_send:
        if st.button(
            "Submit typed answer",
            key="_room_submit_typed",
            type="primary",
            use_container_width=True,
            disabled=not (typed or "").strip(),
        ):
            _submit_text(user, interview, turn, typed)


def _submit_audio(user: Any, interview: Interview, turn: Turn) -> None:
    samples = _drain_audio()
    if samples is None or len(samples) == 0:
        _flash("error", "No audio captured. Try recording again.")
        st.rerun()
        return

    sample_rate = stt_module.TARGET_SAMPLE_RATE
    with st.spinner("Transcribing your answer…"):
        try:
            wav = stt_module.pcm_to_wav_bytes(samples, sample_rate)
            provider = stt_module.get_stt()
            transcript = provider.transcribe(wav)
        except Exception as exc:
            log.warning("STT failed: %s", exc)
            _flash("error", "Transcription failed. Please type your answer instead.")
            st.rerun()
            return

    if not transcript.ok:
        _flash("warning", "Could not hear a clear answer. Please try again or type.")
        st.rerun()
        return

    # Keep the clip and read it for background speech, both only once the answer
    # is real. A failed attempt is usually a bad microphone, and neither storing
    # it nor holding it against the candidate would be right.
    audio_path = recordings.save_answer(
        interview.id, samples, sample_rate, seq=turn.seq
    )
    p = _proctor(interview.id)
    if p is not None:
        p.note_answer_audio(samples, sample_rate)

    _do_submit(
        user,
        interview,
        turn,
        transcript.text,
        transcript_source="whisper",
        audio_path=audio_path,
    )


def _submit_text(user: Any, interview: Interview, turn: Turn, text: str) -> None:
    _do_submit(user, interview, turn, text, transcript_source="typed")


def _do_submit(
    user: Any,
    interview: Interview,
    turn: Turn,
    text: str,
    transcript_source: str,
    audio_path: str = "",
) -> None:
    with st.spinner("Checking your answer…"):
        try:
            outcome = interviews.submit_answer(
                interview.id,
                text,
                user_id=user.id,
                transcript_source=transcript_source,
                audio_path=audio_path,
            )
        except InterviewError as exc:
            _flash("error", str(exc))
            st.rerun()
            return

    st.session_state[_KEY_OUTCOME] = outcome

    if outcome.accepted:
        # Move to the next question (or finish).
        try:
            next_turn = interviews.ask_next(interview.id)
        except InterviewError:
            next_turn = None

        if next_turn is None:
            # Interview finished.
            _finalize_proctoring(interview.id)
            _set_phase("done")
            _flash("success", "All done — your interview has been submitted.")
        else:
            # Speak immediately; the rerun will show the new question.
            try:
                tts_module.speak(next_turn.question)
            except Exception:
                pass
            # Reset last-spoken so _maybe_speak triggers on the new turn id.
            st.session_state["_room_last_spoken_turn"] = None

    st.rerun()


def _transcript_panel(interview: Interview) -> None:
    turns = interviews.answered_turns(interview.id)
    if not turns:
        return
    with st.expander(f"Transcript so far ({len(turns)} answered)", expanded=False):
        for t in turns:
            theme.html_block(theme.question_block(t.question, who="AI interviewer"))
            theme.html_block(theme.answer_block(t.answer or "—", who="You"))
            if t.flags:
                st.caption(f"ℹ flags: {', '.join(t.flags)}")
            st.write("")


# --------------------------------------------------------------------------- #
# Phase: done / score reveal
# --------------------------------------------------------------------------- #


def _phase_done(interview: Interview) -> None:
    theme.page_header(
        "Interview complete",
        "Here is how you did. The recruiter will follow up with next steps.",
        icon="✅",
    )

    if not interview.scored:
        st.info(
            "Your transcript has been submitted. Scoring is in progress — "
            "check back shortly or refresh the page."
        )
        if st.button("Refresh", key="_room_refresh_done"):
            st.rerun()
        return

    # ---- Score summary -----------------------------------------------------
    total = interview.total_score or 0
    maximum = interview.max_total_score or 25
    pct = round(total / maximum * 100) if maximum else 0

    col_ring, col_summary = st.columns([1, 2], gap="large")

    with col_ring:
        theme.html_block(
            theme.score_ring(total, maximum, label="Interview score")
        )
        st.caption(f"{total}/{maximum} · {pct}%")

    with col_summary:
        ev = interview.evaluation
        strengths = ev.get("strengths") or []
        weaknesses = ev.get("weaknesses") or []
        summary = ev.get("summary") or ""
        recommendation = ev.get("recommendation") or ""

        if summary:
            st.markdown(f"**Summary:** {summary}")
        if recommendation:
            tone = (
                "success"
                if recommendation == "hire"
                else "warning"
                if recommendation == "hold"
                else "error"
            )
            theme.html_block(
                theme.pill(f"Recommendation: {recommendation}", tone)
            )

        if strengths:
            st.markdown("**Strengths**")
            for s in strengths:
                st.markdown(f"- {s}")
        if weaknesses:
            st.markdown("**Areas to develop**")
            for w in weaknesses:
                st.markdown(f"- {w}")

    # ---- Criteria breakdown ------------------------------------------------
    criteria = interview.criteria()
    if criteria:
        st.write("")
        st.markdown("##### Criteria breakdown")
        labels = {
            "technical_depth": "Technical depth",
            "problem_solving": "Problem solving",
            "communication": "Communication",
            "culture_fit": "Culture fit",
            "practical_impact": "Practical impact",
        }
        cols = st.columns(len(criteria))
        for col, (key, val) in zip(cols, criteria.items()):
            with col:
                theme.html_block(
                    # Smaller than the headline ring: five of these sit side by
                    # side, and the same convention is used on the pipeline's
                    # secondary scores.
                    theme.score_ring(val, 5, label=labels.get(key, key), size=92)
                )

    # ---- Transcript --------------------------------------------------------
    turns = interviews.transcript(interview.id)
    if turns:
        st.write("")
        with st.expander("Full transcript", expanded=False):
            for t in turns:
                theme.html_block(
                    theme.question_block(t.question, who="AI interviewer")
                )
                if t.answered:
                    theme.html_block(
                        theme.answer_block(t.answer or "—", who="You")
                    )
                    if t.flags:
                        st.caption(f"ℹ flags: {', '.join(t.flags)}")
                st.write("")

    st.divider()
    if st.button("Back to home", key="_room_back_home"):
        home_page = st.session_state.get("_pages", {}).get("home")
        if home_page:
            st.switch_page(home_page)
        else:
            st.rerun()
