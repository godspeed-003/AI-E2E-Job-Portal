"""The spoken-answer path: transcribe on Stop, review, then submit.

Why this file is separate from ``test_interview_room.py``: those tests render
phases and assert the page did not raise. These are about one specific sequence
of state transitions that WebRTC drives and ``AppTest`` cannot — Start, audio
arriving, Stop, transcript, edit, submit — so they drive that state directly
and assert on what the page then does with it.

The guard worth having a test for is the stale take. A transcript is produced
on Stop and held in session state until the candidate approves it, which means
there is a window where a take belonging to question *n* is in memory while the
page has moved to question *n+1*. Submitting it there would record the
candidate answering a question they were never asked.
"""

from __future__ import annotations

import numpy as np
import pytest

from services import auth_service as auth
from services import interview_service as interviews

from test_interview import PASSWORD, _shortlisted
from test_interview_room import _room, _text

pytestmark = pytest.mark.usefixtures("fake_llm")

TRANSCRIPT = (
    "I owned the billing pipeline. I reproduced the duplicate-charge bug, "
    "measured it, and shipped an idempotency key behind a test."
)


def _started(email: str = "priya@example.com"):
    user, _application, interview = _shortlisted(email)
    live = interviews.start(interview.id, user_id=user.id, consent=True)
    turn = interviews.ask_next(live.id)
    assert turn is not None
    return user, live, turn


def _pending(turn, text: str = TRANSCRIPT, seconds: float = 3.0) -> dict:
    """A take as ``_transcribe_now`` would have left it."""
    from speech import stt as stt_module

    rate = stt_module.TARGET_SAMPLE_RATE
    return {
        "text": text,
        "samples": np.zeros(int(rate * seconds), dtype=np.float32),
        "rate": rate,
        "seconds": seconds,
        "seq": turn.seq,
    }


def _room_with_take(user, interview_id, take):
    from streamlit.testing.v1 import AppTest
    from test_interview_room import _room_script

    _, token = auth.login(user.email, PASSWORD)
    app = AppTest.from_function(_room_script, default_timeout=60)
    app.session_state["_auth_token"] = token
    app.session_state["_room_interview_id"] = interview_id
    app.session_state["_room_phase"] = "interview"
    app.session_state["_room_pending"] = take
    app.run()
    return app


# --------------------------------------------------------------------------- #
# The review step
# --------------------------------------------------------------------------- #


def test_a_transcribed_take_is_shown_for_review(fake_llm) -> None:
    user, live, turn = _started()
    app = _room_with_take(user, live.id, _pending(turn))

    assert not app.exception
    body = _text(app)
    assert "transcribed answer" in body.lower()
    # The transcript itself is in an editable box, not just printed at them.
    values = [area.value for area in app.text_area]
    assert any(TRANSCRIPT in str(v) for v in values)


def test_the_voice_submit_button_is_labelled_and_present(fake_llm) -> None:
    """Renamed from "Submit recording", and it lives with the recording now."""
    user, live, turn = _started()
    app = _room_with_take(user, live.id, _pending(turn))

    labels = [str(b.label) for b in app.button]
    assert "Submit voice answer" in labels
    assert "Submit recording" not in labels
    # The typed path keeps its own button.
    assert "Submit typed answer" in labels


def test_record_again_discards_without_spending_an_attempt(fake_llm) -> None:
    user, live, turn = _started()
    app = _room_with_take(user, live.id, _pending(turn))

    before = interviews.get(live.id).attempt_count
    app.button(key="_room_rerecord").click().run()

    assert not app.exception
    assert ("_room_pending" not in app.session_state
            or not app.session_state["_room_pending"])
    assert interviews.get(live.id).attempt_count == before
    # And nothing was recorded against the turn.
    assert interviews.answered_turns(live.id) == []


# --------------------------------------------------------------------------- #
# The stale-take guard
# --------------------------------------------------------------------------- #


def test_a_take_from_a_previous_question_is_discarded(fake_llm) -> None:
    """The reason the pending take carries its ``seq``.

    A transcript produced for question *n* must never be submittable against
    question *n+1*. Without the guard the candidate is recorded answering a
    question they were not asked.
    """
    user, live, turn = _started()
    stale = _pending(turn)

    # Answer the question by typing, which moves the interview to the next turn
    # while the spoken take for the previous one is still in memory.
    interviews.submit_answer(
        live.id,
        "I rebuilt the ingestion path, measured the regression, and shipped the "
        "fix with a test around it.",
        user_id=user.id,
        transcript_source="typed",
    )
    nxt = interviews.ask_next(live.id)
    assert nxt is not None and nxt.seq != stale["seq"]

    app = _room_with_take(user, live.id, stale)

    assert not app.exception
    assert ("_room_pending" not in app.session_state
            or not app.session_state["_room_pending"])
    labels = [str(b.label) for b in app.button]
    assert "Submit voice answer" not in labels


# --------------------------------------------------------------------------- #
# Provenance of an edited transcript
# --------------------------------------------------------------------------- #


def test_an_unedited_transcript_is_recorded_as_whisper(fake_llm) -> None:
    user, live, turn = _started()
    app = _room_with_take(user, live.id, _pending(turn))

    app.button(key="_room_submit_voice").click().run()
    assert not app.exception

    answered = interviews.answered_turns(live.id)
    assert answered, "the answer was not recorded"
    assert answered[-1].answer == TRANSCRIPT
    assert answered[-1].transcript_source == "whisper"


def test_a_corrected_transcript_says_so(fake_llm) -> None:
    """Whisper mishears names and acronyms, so the box is editable.

    An edit changes who wrote the words, so the stored provenance has to change
    with it — the recruiter reads what the candidate approved, and the audit
    trail still distinguishes the two.
    """
    user, live, turn = _started()
    app = _room_with_take(user, live.id, _pending(turn))

    corrected = TRANSCRIPT.replace("idempotency key", "idempotency key in Postgres")
    app.text_area(key="_room_review").set_value(corrected).run()
    app.button(key="_room_submit_voice").click().run()
    assert not app.exception

    answered = interviews.answered_turns(live.id)
    assert answered and answered[-1].answer == corrected
    assert answered[-1].transcript_source == "whisper_edited"


# --------------------------------------------------------------------------- #
# The cue
# --------------------------------------------------------------------------- #


def test_the_cue_waits_for_real_audio_not_just_a_connection() -> None:
    """The threshold exists and is small enough not to add felt delay.

    ``ctx.state.playing`` flips as soon as the peer connection is up, which is
    before the browser has finished its getUserMedia prompt and device warm-up.
    Going green on that would put the cue exactly where the lost first sentence
    already was, so the cue waits on buffered samples instead.
    """
    from ui.pages import interview_room

    assert 0 < interview_room.CUE_WARMUP_SECONDS < interview_room.MIN_ANSWER_SECONDS
