"""The interview room, rendered.

The room's logic is already covered by ``tests/test_interview.py``, which drives
``interview_service`` directly. What was *not* covered was the page itself, and
that gap cost something real: the score reveal passed a ``detail=`` argument
``theme.score_ring`` does not take, so the last screen of the candidate journey
raised a ``TypeError`` the moment an interview finished. Every service test
passed the whole time, because none of them rendered anything.

So these tests are deliberately shallow and deliberately about the *screens*:
render each phase and assert the page did not raise. WebRTC cannot run under
``AppTest`` — there is no browser — and the room is written to degrade to the
typed-answer path when it is missing, which is exactly the path a test can walk.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from core import db
from services import auth_service as auth
from services import interview_service as interviews

from test_interview import PASSWORD, _interviewed, _shift, _shortlisted

pytestmark = pytest.mark.usefixtures("fake_llm")


def _room_script() -> None:
    from ui.pages import interview_room

    interview_room.render()


def _room(user: auth.User, interview_id: int | None, *, phase: str | None = None):
    """The room as ``user``, deep-linked to ``interview_id``."""
    from streamlit.testing.v1 import AppTest

    _, token = auth.login(user.email, PASSWORD)
    app = AppTest.from_function(_room_script, default_timeout=60)
    app.session_state["_auth_token"] = token
    if interview_id is not None:
        app.session_state["_room_interview_id"] = interview_id
    if phase is not None:
        app.session_state["_room_phase"] = phase
    app.run()
    return app


def _text(app) -> str:
    """Everything the page rendered, flattened, for substring assertions."""
    parts = [str(block.value) for block in app.markdown]
    parts += [str(block.value) for block in app.info]
    parts += [str(block.value) for block in app.warning]
    parts += [str(block.value) for block in app.error]
    parts += [str(block.value) for block in app.caption]
    return "\n".join(parts)


def test_the_score_reveal_renders_for_a_finished_interview(fake_llm):
    """The regression this file was written for: the last screen must not raise."""
    user, done = _interviewed(fake_llm)
    assert done.scored  # otherwise the ring below is never reached

    app = _room(user, done.id)

    assert not app.exception
    assert "Interview complete" in _text(app)


def test_the_score_reveal_draws_a_ring_for_every_criterion():
    """Five rings plus the headline one — the call that was passing a bad kwarg."""
    user, _application, interview = _shortlisted()
    live = interviews.start(interview.id, user_id=user.id)
    for _ in range(2):
        turn = interviews.ask_next(live.id)
        assert turn is not None
        interviews.submit_answer(
            live.id,
            "At Zeta Payments I owned the reconciliation service end to end. It "
            "settled twelve thousand transactions a day, and making retries "
            "idempotent was the hard part.",
            user_id=user.id,
        )
    done = interviews.finish(live.id)

    app = _room(user, done.id)

    assert not app.exception
    rendered = _text(app)
    assert "Criteria breakdown" in rendered
    for label in ("Technical depth", "Problem solving", "Communication"):
        assert label in rendered


def test_an_unscored_interview_offers_a_refresh_rather_than_a_blank_screen():
    """Scoring is best effort, so 'come back' has to be a real screen."""
    user, _application, interview = _shortlisted()
    live = interviews.start(interview.id, user_id=user.id)
    done = interviews.finish(live.id, score_now=False)
    assert not done.scored

    app = _room(user, done.id)

    assert not app.exception
    assert "Scoring is in progress" in _text(app)


def test_the_consent_screen_renders_before_the_interview_starts():
    user, _application, interview = _shortlisted()

    app = _room(user, interview.id)

    assert not app.exception
    assert "Ready for your interview?" in _text(app)


def test_a_closed_window_is_explained_instead_of_offering_a_start_button():
    """The candidate must be told why, not shown a button that will error."""
    user, _application, interview = _shortlisted()
    _shift(interview.id, closes_at=(db.utc_now() - timedelta(minutes=5)).isoformat())

    app = _room(user, interview.id)

    assert not app.exception
    assert "window has closed" in _text(app)


def test_another_candidates_interview_is_refused_not_rendered():
    """The room is deep-linked by id, so the id space must not be walkable."""
    _owner, _application, interview = _shortlisted()
    intruder = auth.register("intruder@example.com", PASSWORD, full_name="Intruder")

    app = _room(intruder, interview.id)

    assert not app.exception
    assert list(app.error), "an intruder must be turned away, not shown the room"


def test_no_interview_id_is_a_message_rather_than_a_crash():
    user = auth.register("no-interview@example.com", PASSWORD, full_name="No Interview")

    app = _room(user, None)

    assert not app.exception
    assert "No interview selected" in _text(app)


def test_sidebar_open_picks_the_candidates_pending_interview():
    """The Interview nav item has no query param — still open their live room."""
    user, _application, interview = _shortlisted()

    app = _room(user, None)

    assert not app.exception
    assert "Ready for your interview?" in _text(app)
    assert app.session_state["_room_interview_id"] == interview.id
