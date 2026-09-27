"""The sandbox boundary, and the shortcut that runs across it.

Sandbox mode makes one promise — test rows and real rows never mix — and the
skip-ahead shortcut is the feature most likely to break it, because it writes
applications and interviews on an admin's behalf. So the tests here are mostly
about the boundary: that a shortcut row is flagged, that shortening refuses to
touch anything real, and that reset takes the test data and leaves the rest.

The second thing pinned is that ``shorten`` actually shortens. A budget the
interview ignores would be worse than no shortcut at all: a tester would sit six
questions believing they had asked for two, and conclude the room was broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import resume as resume_core
from services import application_service as apps
from services import auth_service as auth
from services import interview_service as interviews

from test_interview import GOOD_ANSWER, RESUME, _role, _shortlisted

pytestmark = pytest.mark.usefixtures("fake_llm")


def _sandbox_shortlist(label: str = "candidate") -> interviews.Interview:
    """What the skip-ahead button does, minus Streamlit."""
    persona = auth.ensure_sandbox_candidate(label, full_name="Sandbox Candidate")
    role = _role()
    application = apps.apply(
        persona, role.id, resume_core.from_text(RESUME), is_sandbox=True
    )
    application = apps.set_status(application.id, "shortlisted", actor_id=None)
    interview = interviews.for_application(application.id)
    assert interview is not None
    return interview


# --------------------------------------------------------------------------- #
# The boundary
# --------------------------------------------------------------------------- #


def test_a_sandbox_shortlist_carries_the_flag_all_the_way_down():
    """The application is flagged, so the interview it creates is too."""
    interview = _sandbox_shortlist()

    assert interview.is_sandbox
    assert interview.status == "pending"


def test_sandbox_rows_stay_out_of_the_recruiter_pipeline_by_default():
    _sandbox_shortlist()
    role = _role()

    assert apps.ranked_for_role(role.id) == []
    assert len(apps.ranked_for_role(role.id, include_sandbox=True)) == 1


def test_reset_takes_the_test_data_and_leaves_the_real_candidate():
    real_user, real_application, _interview = _shortlisted()
    _sandbox_shortlist()
    assert auth.sandbox_counts()["users"] == 1

    auth.reset_sandbox()

    assert auth.sandbox_counts() == {"users": 0, "applications": 0, "interviews": 0}
    assert auth.get_user_by_email(real_user.email) is not None
    assert apps.get(real_application.id) is not None


def test_reset_sweeps_the_media_the_cascade_cannot_reach():
    """Recordings and snapshots are files, not rows. Deleting the row leaves them."""
    import numpy as np

    from services import proctor_service, recording_service

    interview = _sandbox_shortlist()
    persona = auth.get_user_by_email(auth.sandbox_email("candidate"))
    interviews.start(interview.id, user_id=persona.id)  # type: ignore[union-attr]
    turn = interviews.ask_next(interview.id)
    clip = recording_service.save_answer(
        interview.id, np.zeros(1600, dtype=np.float32) + 0.1, 16_000, seq=turn.seq  # type: ignore[union-attr]
    )
    snapshot = proctor_service.save_snapshot(
        interview.id, np.zeros((32, 32, 3), dtype=np.uint8), label="test"
    )
    assert Path(clip).exists()

    auth.reset_sandbox()

    assert not Path(clip).exists()
    assert not recording_service.session_dir(interview.id).exists()
    if snapshot is not None:  # only written when snapshots are switched on
        assert not Path(snapshot).exists()


def test_reset_leaves_a_real_candidates_recording_alone():
    """The whole promise: a sweep of test media must not reach live media."""
    import numpy as np

    from services import recording_service

    user, _application, real = _shortlisted()
    interviews.start(real.id, user_id=user.id)
    turn = interviews.ask_next(real.id)
    clip = recording_service.save_answer(
        real.id, np.zeros(1600, dtype=np.float32) + 0.1, 16_000, seq=turn.seq  # type: ignore[union-attr]
    )
    _sandbox_shortlist()

    auth.reset_sandbox()

    assert Path(clip).exists()


# --------------------------------------------------------------------------- #
# Shortening
# --------------------------------------------------------------------------- #


def test_shortening_cuts_the_budget_and_the_plan_together():
    """Both numbers move, because the room prints one and obeys the other."""
    interview = _sandbox_shortlist()
    assert len(interview.plan) == 6

    short = interviews.shorten(interview.id, 2)

    assert short.planned_questions == 2
    assert short.max_turns == 2
    assert len(short.plan) == 2
    assert short.plan.questions == interview.plan.questions[:2]


def test_a_shortened_interview_really_does_end_after_two_questions():
    """The point of the whole feature: reach the score screen in two answers."""
    interview = _sandbox_shortlist()
    persona = auth.get_user_by_email(auth.sandbox_email("candidate"))
    assert persona is not None
    interviews.shorten(interview.id, 2)
    interviews.start(interview.id, user_id=persona.id)

    for _ in range(2):
        turn = interviews.ask_next(interview.id)
        assert turn is not None
        interviews.submit_answer(interview.id, GOOD_ANSWER, user_id=persona.id)

    assert interviews.ask_next(interview.id) is None
    assert len(interviews.answered_turns(interview.id)) == 2


def test_shortening_refuses_to_touch_a_real_interview():
    """An admin poking a live candidate's budget is the failure to prevent."""
    _user, _application, interview = _shortlisted()

    with pytest.raises(interviews.InterviewError, match="sandbox"):
        interviews.shorten(interview.id, 2)

    assert interviews.get(interview.id).planned_questions == 6  # type: ignore[union-attr]


def test_shortening_refuses_once_the_interview_has_started():
    """Moving the finish line mid-answer would invalidate what was already said."""
    interview = _sandbox_shortlist()
    persona = auth.get_user_by_email(auth.sandbox_email("candidate"))
    interviews.start(interview.id, user_id=persona.id)  # type: ignore[union-attr]

    with pytest.raises(interviews.InterviewError, match="already started"):
        interviews.shorten(interview.id, 2)


def test_a_silly_question_count_is_clamped_not_obeyed():
    interview = _sandbox_shortlist()

    assert interviews.shorten(interview.id, 0).planned_questions == 1
    assert interviews.shorten(interview.id, 999).planned_questions <= (
        interviews.MAX_PLAN_QUESTIONS
    )


def test_shortening_a_missing_interview_is_an_error_not_a_crash():
    with pytest.raises(interviews.InterviewError):
        interviews.shorten(9999, 2)


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #


def _sandbox_script() -> None:
    from ui.pages import admin_sandbox

    admin_sandbox.render()


def _page():
    """The sandbox page, rendered as the bootstrap admin."""
    from streamlit.testing.v1 import AppTest

    admin = auth.ensure_admin_account()
    assert admin is not None
    _, token = auth.login(admin.email, "admin-test-1234")
    app = AppTest.from_function(_sandbox_script, default_timeout=60)
    app.session_state["_auth_token"] = token
    app.run()
    return app


def _shortcut_button(app):
    """The skip-ahead button, by label. Position would find the reset button."""
    for button in app.button:
        if "Shortlist" in str(button.label):
            return button
    raise AssertionError(f"no shortcut button among {[b.label for b in app.button]}")


def test_the_page_asks_for_a_persona_before_offering_the_shortcut():
    """No personas yet: an empty selectbox would be a trap, not a shortcut."""
    _role()
    app = _page()

    assert not app.exception
    assert any("disposable candidate" in str(block.value).lower() for block in app.info)


def test_the_button_shortlists_the_persona_and_shortens_the_interview():
    """The whole shortcut, driven through the widgets a tester would click."""
    role = _role()
    auth.ensure_sandbox_candidate("candidate", full_name="Sandbox Candidate")
    app = _page()
    assert not app.exception

    _shortcut_button(app).click().run()

    assert not app.exception
    persona = auth.get_user_by_email(auth.sandbox_email("candidate"))
    assert persona is not None
    application = apps.latest_for(persona.id, role.id)
    assert application is not None and application.is_shortlisted
    assert application.is_sandbox

    interview = interviews.for_application(application.id)
    assert interview is not None
    assert interview.planned_questions == 2  # the panel's default


def test_clicking_the_shortcut_twice_is_not_an_error():
    """apply() refuses to replace a resume behind a live shortlist, by design."""
    _role()
    auth.ensure_sandbox_candidate("candidate", full_name="Sandbox Candidate")
    for _ in range(2):
        app = _page()
        _shortcut_button(app).click().run()
        assert not app.exception
        assert not list(app.error)
