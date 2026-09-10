"""Phase 5 — video proctoring: debounce, severity, integrity scoring, persistence.

Offline like the rest of the suite. No CV model is downloaded and no frame is ever
decoded: the rules engine is fed hand-built ``FrameAnalysis`` objects and a stub
analyzer, which is the whole point of keeping ``analyzer`` and ``rules`` apart.
``Analyzer`` itself is not exercised here — it is a thin wrapper over four
third-party backends, and asserting on their output would be testing OpenCV.

Three things these tests exist to pin:

* debounce is the feature, not an optimisation — a dropped frame or a glance at
  the keyboard must not become an event on a candidate's record;
* the score is a pure function of the stored rows, so ``recompute`` can re-derive
  it later without the video;
* proctoring is advisory. A critical signal escalates to human review; nothing
  here decides an outcome on its own.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core import db
from proctoring.analyzer import DetectedObject, FrameAnalysis, HeadPose
from proctoring.rules import (
    KIND_EXTRA_PERSON,
    KIND_LOOKING_AWAY,
    KIND_MULTIPLE_FACES,
    KIND_NO_FACE,
    KIND_PASTE,
    KIND_PHONE,
    KIND_SUBSTITUTION,
    KIND_TAB_SWITCH,
    KIND_WINDOW_BLUR,
    ProctoringEvent,
    RuleEngine,
    compute_integrity_score,
)
from services import proctor_service as proctor

# Reuse Phase 4's pipeline seeding rather than restating the schema in raw SQL:
# an interview that proctoring can attach to is a shortlisted candidate's interview.
from test_interview import _shortlisted

# Defaults from ProctoringSettings, restated so a config change breaks a test
# rather than silently changing what these assertions mean.
NO_FACE_SECONDS = 4.0
LOOKING_AWAY_SECONDS = 4.0
YAW_LIMIT = 32.0

# SFace's recommended same-person cutoff is 0.363.
SAME_PERSON = 0.9
DIFFERENT_PERSON = 0.2


# --------------------------------------------------------------------------- #
# Frame fixtures — what the analyzer would have returned
# --------------------------------------------------------------------------- #


def _no_face() -> FrameAnalysis:
    return FrameAnalysis(face_count=0)


def _one_face(yaw: float = 0.0, pitch: float = 0.0) -> FrameAnalysis:
    return FrameAnalysis(
        face_count=1,
        face_boxes=[(0, 0, 100, 100)],
        head_pose=HeadPose(yaw=yaw, pitch=pitch),
    )


def _two_faces() -> FrameAnalysis:
    return FrameAnalysis(
        face_count=2,
        face_boxes=[(0, 0, 100, 100), (200, 0, 100, 100)],
    )


def _holding(label: str) -> FrameAnalysis:
    analysis = _one_face()
    analysis.objects = [DetectedObject(label=label, confidence=0.85, box=(10, 10, 50, 50))]
    return analysis


def _stranger(cosine: float) -> tuple[FrameAnalysis, MagicMock]:
    """A face plus an analyzer that reports how well it matches enrollment."""
    analysis = _one_face()
    analysis.face_embedding = np.zeros(128, dtype=np.float32)
    analyzer = MagicMock()
    analyzer.substitution_cosine.return_value = cosine
    return analysis, analyzer


def _analyzer() -> MagicMock:
    """No enrollment captured, so substitution can never fire."""
    analyzer = MagicMock()
    analyzer.substitution_cosine.return_value = None
    return analyzer


def _at(engine: RuleEngine, seconds: float, analysis: FrameAnalysis, analyzer=None):
    """Evaluate one frame at a fixed point on the monotonic clock."""
    with patch("time.monotonic", return_value=seconds):
        return engine.evaluate(analysis, analyzer or _analyzer(), elapsed_seconds=seconds)


def _kinds(events: list[ProctoringEvent]) -> set[str]:
    return {event.kind for event in events}


# --------------------------------------------------------------------------- #
# Debounce: a signal has to persist before it costs the candidate anything
# --------------------------------------------------------------------------- #


def test_a_face_missing_for_one_frame_is_not_an_event():
    engine = RuleEngine()
    assert not _at(engine, 100.0, _no_face())


def test_a_face_missing_past_the_threshold_is_an_event():
    engine = RuleEngine()
    _at(engine, 100.0, _no_face())
    events = _at(engine, 100.0 + NO_FACE_SECONDS + 1, _no_face())

    assert KIND_NO_FACE in _kinds(events)


def test_the_absence_is_reported_with_how_long_it_lasted():
    engine = RuleEngine()
    _at(engine, 100.0, _no_face())
    events = _at(engine, 107.0, _no_face())

    absence = next(e for e in events if e.kind == KIND_NO_FACE)
    assert absence.duration_seconds == pytest.approx(7.0)
    assert absence.severity == "high"


def test_a_face_that_comes_back_clears_the_timer():
    engine = RuleEngine()
    _at(engine, 100.0, _no_face())
    events = _at(engine, 102.0, _one_face())

    assert not events
    # Leaving early would let the next gap inherit this one's start time.
    assert not engine._no_face.active


def test_a_long_absence_is_logged_once_per_threshold_not_once_per_frame():
    engine = RuleEngine()
    _at(engine, 100.0, _no_face())
    first = _at(engine, 105.0, _no_face())
    immediately_after = _at(engine, 106.0, _no_face())
    later = _at(engine, 110.0, _no_face())

    assert KIND_NO_FACE in _kinds(first)
    assert KIND_NO_FACE not in _kinds(immediately_after)
    assert KIND_NO_FACE in _kinds(later)


def test_a_glance_away_is_not_an_event_but_a_stare_is():
    engine = RuleEngine()
    glance = _at(engine, 200.0, _one_face(yaw=YAW_LIMIT + 8))
    stare = _at(engine, 200.0 + LOOKING_AWAY_SECONDS + 1, _one_face(yaw=YAW_LIMIT + 8))

    assert KIND_LOOKING_AWAY not in _kinds(glance)
    assert KIND_LOOKING_AWAY in _kinds(stare)


def test_facing_the_camera_is_never_an_event():
    engine = RuleEngine()
    assert not _at(engine, 200.0, _one_face(yaw=5.0, pitch=3.0))


def test_looking_back_at_the_camera_clears_the_timer():
    engine = RuleEngine()
    _at(engine, 200.0, _one_face(yaw=YAW_LIMIT + 8))
    _at(engine, 204.0, _one_face(yaw=5.0))

    assert not engine._looking_away.active


def test_a_frame_with_no_head_pose_does_not_count_as_looking_away():
    engine = RuleEngine()
    # MediaPipe absent: face detected, pose is None. Silence, not a guess.
    blind = FrameAnalysis(face_count=1, face_boxes=[(0, 0, 100, 100)])
    _at(engine, 200.0, blind)
    events = _at(engine, 210.0, blind)

    assert KIND_LOOKING_AWAY not in _kinds(events)


# --------------------------------------------------------------------------- #
# Signals that fire on sight
# --------------------------------------------------------------------------- #


def test_a_second_face_is_reported_immediately_with_its_count():
    engine = RuleEngine()
    events = _at(engine, 100.0, _two_faces())

    extra = next(e for e in events if e.kind == KIND_MULTIPLE_FACES)
    assert extra.severity == "high"
    assert extra.detail["count"] == 2


def test_a_phone_and_a_second_person_are_both_recognised():
    assert KIND_PHONE in _kinds(_at(RuleEngine(), 100.0, _holding("phone")))
    assert KIND_EXTRA_PERSON in _kinds(_at(RuleEngine(), 100.0, _holding("person")))


def test_the_same_object_is_not_logged_on_every_frame_it_appears_in():
    engine = RuleEngine()
    first = _at(engine, 100.0, _holding("phone"))
    same_phone = _at(engine, 101.0, _holding("phone"))
    picked_up_again = _at(engine, 120.0, _holding("phone"))

    assert KIND_PHONE in _kinds(first)
    assert KIND_PHONE not in _kinds(same_phone)
    assert KIND_PHONE in _kinds(picked_up_again)


def test_an_object_nobody_cares_about_is_ignored():
    engine = RuleEngine()
    assert not _at(engine, 100.0, _holding("teddy bear"))


# --------------------------------------------------------------------------- #
# Substitution — the one signal that outranks the others
# --------------------------------------------------------------------------- #


def test_the_enrolled_candidate_is_not_accused_of_substitution():
    analysis, analyzer = _stranger(SAME_PERSON)
    events = _at(RuleEngine(), 100.0, analysis, analyzer)

    assert KIND_SUBSTITUTION not in _kinds(events)


def test_a_different_face_is_a_critical_event():
    analysis, analyzer = _stranger(DIFFERENT_PERSON)
    events = _at(RuleEngine(), 100.0, analysis, analyzer)

    swap = next(e for e in events if e.kind == KIND_SUBSTITUTION)
    assert swap.severity == "critical"
    assert swap.detail["cosine_similarity"] == pytest.approx(DIFFERENT_PERSON)
    assert swap.snapshot_requested


def test_substitution_is_not_re_reported_every_frame():
    engine = RuleEngine()
    analysis, analyzer = _stranger(DIFFERENT_PERSON)
    first = _at(engine, 100.0, analysis, analyzer)
    within_cooldown = _at(engine, 110.0, analysis, analyzer)

    assert KIND_SUBSTITUTION in _kinds(first)
    assert KIND_SUBSTITUTION not in _kinds(within_cooldown)


def test_without_an_enrollment_photo_substitution_stays_silent():
    analysis = _one_face()
    analysis.face_embedding = np.zeros(128, dtype=np.float32)
    # substitution_cosine returns None when nothing was enrolled.
    events = _at(RuleEngine(), 100.0, analysis, _analyzer())

    assert KIND_SUBSTITUTION not in _kinds(events)


# --------------------------------------------------------------------------- #
# Browser-side signals
# --------------------------------------------------------------------------- #


def test_leaving_the_tab_outranks_merely_losing_focus():
    engine = RuleEngine()
    assert engine.browser_event(KIND_TAB_SWITCH).severity == "high"
    assert engine.browser_event(KIND_WINDOW_BLUR).severity == "medium"
    assert engine.browser_event(KIND_PASTE).severity == "medium"


def test_an_unrecognised_browser_signal_is_recorded_at_the_lowest_severity():
    # Forward compatibility: a new signal from the JS side must not crash or
    # cost the candidate a real deduction until it is weighted deliberately.
    assert RuleEngine().browser_event("some_future_signal").severity == "low"


# --------------------------------------------------------------------------- #
# Integrity score — a pure function of the stored rows
# --------------------------------------------------------------------------- #


def _rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"kind": kind, "severity": severity} for kind, severity in pairs]


def test_a_clean_session_scores_full_marks():
    assert compute_integrity_score([]) == (100, "clean")


def test_repeats_of_one_kind_cost_less_than_the_first():
    once = compute_integrity_score(_rows((KIND_TAB_SWITCH, "high")))[0]
    twice = compute_integrity_score(_rows((KIND_TAB_SWITCH, "high")) * 2)[0]

    assert once == 94                       # 100 - 6
    assert twice == 91                      # a second switch costs half, not another 6


def test_different_kinds_each_cost_full_weight():
    score, _ = compute_integrity_score(
        _rows((KIND_TAB_SWITCH, "high"), (KIND_PASTE, "medium"))
    )
    assert score == 91                      # 100 - 6 - 3


def test_the_score_never_falls_below_zero():
    score, verdict = compute_integrity_score(_rows((KIND_NO_FACE, "critical")) * 40)

    assert score == 0
    assert verdict == "flag"


def test_a_handful_of_minor_slips_still_reads_as_clean():
    score, verdict = compute_integrity_score(
        _rows((KIND_PASTE, "low"), (KIND_WINDOW_BLUR, "low"))
    )

    assert score >= 80
    assert verdict == "clean"


def test_a_middling_score_asks_for_review_rather_than_flagging():
    score, verdict = compute_integrity_score(
        _rows(
            (KIND_TAB_SWITCH, "high"),
            (KIND_NO_FACE, "high"),
            (KIND_LOOKING_AWAY, "high"),
            (KIND_MULTIPLE_FACES, "high"),
            (KIND_PHONE, "high"),
        )
    )

    assert 55 <= score < 80
    assert verdict == "review"


def test_one_substitution_goes_to_review_even_though_the_score_looks_fine():
    # 100 - 12 = 88 would read as "clean" on the arithmetic alone, but somebody
    # else may have sat this interview. That is exactly the case a human must see.
    score, verdict = compute_integrity_score(_rows((KIND_SUBSTITUTION, "critical")))

    assert score == 88
    assert verdict == "review"


def test_a_low_enough_score_is_flagged_outright():
    score, verdict = compute_integrity_score(
        _rows((KIND_NO_FACE, "critical"), (KIND_SUBSTITUTION, "critical")) * 3
    )

    assert score < 55
    assert verdict == "flag"


# --------------------------------------------------------------------------- #
# Persistence — every event is a row, so the report survives a refresh
# --------------------------------------------------------------------------- #


@pytest.fixture
def interview_id(fake_llm) -> int:
    """A real shortlisted candidate's interview, through the real pipeline."""
    _, _, interview = _shortlisted()
    return interview.id


def _event(kind: str, severity: str = "high") -> ProctoringEvent:
    return ProctoringEvent(kind=kind, severity=severity, confidence=1.0)


def test_an_event_is_stored_with_its_severity_and_offset(interview_id):
    proctor.record_event(interview_id, _event(KIND_TAB_SWITCH), elapsed_seconds=5.0)

    rows = proctor.events_for(interview_id)
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_TAB_SWITCH
    assert rows[0]["severity"] == "high"
    assert rows[0]["elapsed_seconds"] == pytest.approx(5.0)


def test_a_browser_signal_is_weighted_the_same_as_one_from_the_engine(interview_id):
    proctor.record_browser_event(interview_id, KIND_PASTE, elapsed_seconds=3.0)

    row = proctor.events_for(interview_id)[0]
    assert row["kind"] == KIND_PASTE
    assert row["severity"] == RuleEngine().browser_event(KIND_PASTE).severity


def test_no_snapshot_is_written_when_there_is_no_frame_to_write(interview_id):
    proctor.record_event(
        interview_id, _event(KIND_SUBSTITUTION, "critical"), elapsed_seconds=9.0
    )

    assert proctor.events_for(interview_id)[0]["snapshot_path"] is None


def test_events_come_back_in_the_order_they_happened(interview_id):
    # All three land in the same second: ts alone cannot order them, so this is
    # really asserting the id tie-break in events_for.
    kinds = [KIND_TAB_SWITCH, KIND_PASTE, KIND_WINDOW_BLUR, KIND_NO_FACE, KIND_PHONE]
    for kind in kinds:
        proctor.record_event(interview_id, _event(kind), elapsed_seconds=1.0)

    stored = proctor.events_for(interview_id)
    assert len({row["ts"] for row in stored}) == 1
    assert [row["kind"] for row in stored] == kinds


def test_recompute_writes_the_score_and_verdict_onto_the_interview(interview_id):
    proctor.record_event(interview_id, _event(KIND_TAB_SWITCH), elapsed_seconds=5.0)
    score, verdict = proctor.recompute(interview_id)

    row = db.query_one(
        "SELECT integrity_score, integrity_verdict FROM interviews WHERE id = ?",
        (interview_id,),
    )
    assert (row["integrity_score"], row["integrity_verdict"]) == (score, verdict)
    assert score == 94


def test_the_report_summarises_the_events_for_the_recruiter(interview_id):
    proctor.record_event(interview_id, _event(KIND_TAB_SWITCH), elapsed_seconds=5.0)
    proctor.record_event(interview_id, _event(KIND_TAB_SWITCH), elapsed_seconds=9.0)
    proctor.record_event(interview_id, _event(KIND_PASTE, "medium"), elapsed_seconds=12.0)
    proctor.recompute(interview_id)

    report = db.loads(
        db.scalar("SELECT integrity_report FROM interviews WHERE id = ?", (interview_id,))
    )
    assert report["event_count"] == 3
    counted = {entry["kind"]: entry["count"] for entry in report["kinds"]}
    assert counted == {KIND_TAB_SWITCH: 2, KIND_PASTE: 1}
    assert "advisory" in report


def test_recompute_is_derived_from_the_rows_so_it_can_run_again_later(interview_id):
    proctor.record_event(interview_id, _event(KIND_TAB_SWITCH), elapsed_seconds=5.0)

    assert proctor.recompute(interview_id) == proctor.recompute(interview_id)


def test_the_same_kind_at_two_severities_scores_the_same_every_time(interview_id):
    # Diminishing returns make the score order-sensitive when one kind arrives at
    # mixed severities, so this only holds while events_for is deterministic.
    proctor.record_event(interview_id, _event(KIND_NO_FACE, "high"), elapsed_seconds=5.0)
    proctor.record_event(
        interview_id, _event(KIND_NO_FACE, "critical"), elapsed_seconds=9.0
    )

    assert proctor.recompute(interview_id) == (88, "review")  # 100 - 6 - 12/2


def test_an_interview_nobody_flagged_finalises_as_clean(interview_id):
    assert proctor.finalize(interview_id) == (100, "clean")


def test_finalising_locks_in_whatever_was_recorded(interview_id):
    proctor.record_event(
        interview_id, _event(KIND_SUBSTITUTION, "critical"), elapsed_seconds=20.0
    )
    score, verdict = proctor.finalize(interview_id)

    assert (score, verdict) == (88, "review")


def test_one_candidates_events_never_appear_on_another(interview_id, fake_llm):
    _, _, other = _shortlisted(email="raj@example.com", role_id="zeta-platform")
    proctor.record_event(interview_id, _event(KIND_TAB_SWITCH), elapsed_seconds=5.0)

    assert proctor.events_for(other.id) == []
    assert proctor.finalize(other.id) == (100, "clean")


# --------------------------------------------------------------------------- #
# The thread hand-off — the media thread analyses, the script thread writes
# --------------------------------------------------------------------------- #


class _Frame:
    """Stand-in for an ``av.VideoFrame``."""

    def __init__(self, decodes: bool = True) -> None:
        self._decodes = decodes

    def to_ndarray(self, format: str = "bgr24") -> np.ndarray:
        if not self._decodes:
            raise RuntimeError("corrupt frame")
        return np.zeros((48, 64, 3), dtype=np.uint8)


@pytest.fixture
def live(interview_id):
    """A proctoring session with the CV backends stubbed out."""
    from proctoring import session as proctor_session

    live = proctor_session.ProctorSession(interview_id)
    live.enabled = True
    live._analyzer = MagicMock()
    live._analyzer.analyze.return_value = _no_face()
    live._analyzer.substitution_cosine.return_value = None
    return live


def test_a_frame_is_handed_back_untouched_so_the_stream_is_never_altered(live):
    frame = _Frame()
    assert live.on_frame(frame) is frame


def test_a_detector_that_throws_mid_interview_does_not_break_the_stream(live):
    live._analyzer.analyze.side_effect = RuntimeError("model exploded")
    frame = _Frame()

    assert live.on_frame(frame) is frame
    assert live.drain() == 0


def test_a_frame_that_will_not_decode_is_skipped_quietly(live):
    frame = _Frame(decodes=False)

    assert live.on_frame(frame) is frame
    live._analyzer.analyze.assert_not_called()


def test_a_skipped_frame_produces_nothing(live):
    # analyze() returns None for frames the stride drops.
    live._analyzer.analyze.return_value = None
    live.on_frame(_Frame())

    assert live.drain() == 0


def test_the_media_thread_queues_and_the_script_thread_writes(live):
    # A second face fires on sight, so these frames really do produce events.
    live._analyzer.analyze.return_value = _two_faces()
    live.on_frame(_Frame())
    live.on_frame(_Frame())

    # Nothing is in the database while only the callback has run…
    assert proctor.events_for(live.interview_id) == []
    assert live.status["face_count"] == 2

    # …until the script thread drains on the next rerun.
    assert live.drain() == 2
    assert {row["kind"] for row in proctor.events_for(live.interview_id)} == {
        KIND_MULTIPLE_FACES
    }


def test_draining_an_idle_session_is_harmless(live):
    assert live.drain() == 0
    assert live.drain() == 0


def test_a_full_queue_drops_events_instead_of_stalling_the_video(live):
    from proctoring.session import _QUEUE_DEPTH

    for _ in range(_QUEUE_DEPTH + 5):
        live.note_browser_event(KIND_TAB_SWITCH)

    assert live.status["dropped"] == 5
    assert live.drain() == _QUEUE_DEPTH


def test_a_write_failure_is_swallowed_rather_than_ending_the_interview(live):
    live.note_browser_event(KIND_TAB_SWITCH)
    with patch.object(proctor, "record_event", side_effect=RuntimeError("disk full")):
        assert live.drain() == 0

    # The event is gone, but the session is still usable.
    live.note_browser_event(KIND_PASTE)
    assert live.drain() == 1


def test_disabling_proctoring_records_nothing_at_all(interview_id):
    from proctoring import session as proctor_session

    with patch("proctoring.session.settings") as stub:
        stub.proctoring.enabled = False
        off = proctor_session.ProctorSession(interview_id)

    frame = _Frame()
    assert off.on_frame(frame) is frame
    off.note_browser_event(KIND_TAB_SWITCH)
    assert not off.enroll(np.zeros((48, 64, 3), dtype=np.uint8))

    off.drain()
    assert proctor.events_for(interview_id) == []


def test_a_face_the_camera_cannot_find_leaves_substitution_off(live):
    live._analyzer.enroll.return_value = False

    assert not live.enroll(np.zeros((48, 64, 3), dtype=np.uint8))
    assert not live.status["enrolled"]
    # Not an integrity event — the candidate is asked to re-centre instead.
    assert proctor.events_for(live.interview_id) == []


def test_enrolling_records_the_reference_face(live):
    live._analyzer.enroll.return_value = True

    assert live.enroll(np.zeros((48, 64, 3), dtype=np.uint8))
    assert live.status["enrolled"]


def test_finalising_flushes_what_is_still_queued(live):
    live.note_browser_event(KIND_TAB_SWITCH)
    # finalize() must not score a session while an event is still in the queue.
    score, verdict = live.finalize()

    assert (score, verdict) == (94, "clean")
    assert len(proctor.events_for(live.interview_id)) == 1


# --------------------------------------------------------------------------- #
# The room itself — the rest of the suite runs with proctoring switched off,
# so without these the wiring in interview_room.py is never executed at all.
# --------------------------------------------------------------------------- #


def _room_script() -> None:
    """Rendered inside AppTest.

    The room is reached through ``st.navigation``, which AppTest cannot select
    for callable pages (``switch_page`` only handles file-based ones), so the
    page function is rendered directly. ``render`` still calls ``session.require``
    itself, so the auth check is exercised rather than bypassed.
    """
    from ui.pages import interview_room

    interview_room.render()


def _room(interview_id: int, *, proctoring: bool):
    """Render the interview room for a signed-in candidate."""
    from streamlit.testing.v1 import AppTest

    from services import auth_service as auth
    from ui.pages import interview_room

    _, token = auth.login("priya@example.com", "candidate-pass-1234")

    app = AppTest.from_function(_room_script, default_timeout=60)
    app.session_state["_auth_token"] = token
    app.session_state["_room_interview_id"] = interview_id

    with patch.object(interview_room, "settings") as stub:
        stub.proctoring.enabled = proctoring
        app.run()
    return app


def _rendered(app) -> str:
    return " ".join(block.value for block in app.markdown)


def test_the_room_renders_with_proctoring_switched_on(interview_id):
    app = _room(interview_id, proctoring=True)

    assert not app.exception
    # Pin the page: `not app.exception` would pass just as happily on the home
    # page if the deep link ever stopped resolving.
    assert "Ready for your interview?" in _rendered(app)


def test_the_room_still_renders_with_proctoring_switched_off(interview_id):
    app = _room(interview_id, proctoring=False)

    assert not app.exception
    assert "Ready for your interview?" in _rendered(app)


def test_a_browser_signal_in_the_url_is_recorded_and_the_parameter_cleared(
    interview_id,
):
    from ui.pages import interview_room

    live = interview_room.ProctorSession(interview_id)
    live.enabled = True

    with patch.object(interview_room, "st") as stub_st:
        stub_st.query_params = {"proctor": f"{KIND_TAB_SWITCH},{KIND_PASTE}"}
        interview_room._collect_browser_signals(live)
        assert "proctor" not in stub_st.query_params

    assert live.drain() == 2
    assert {row["kind"] for row in proctor.events_for(interview_id)} == {
        KIND_TAB_SWITCH,
        KIND_PASTE,
    }


def test_a_browser_signal_with_no_proctoring_session_is_dropped_not_crashed():
    from ui.pages import interview_room

    with patch.object(interview_room, "st") as stub_st:
        stub_st.query_params = {"proctor": KIND_TAB_SWITCH}
        interview_room._collect_browser_signals(None)
        assert "proctor" not in stub_st.query_params

