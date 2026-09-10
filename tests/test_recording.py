"""Keeping the audio behind a transcript.

The reason to test this at all: every score in the portal is computed from text
a speech model guessed at, and the recording is the only way to check the guess.
A silent failure here is not "a missing file" — it is a scored decision with no
way left to audit it.

So these tests pin two things. First, that a saved clip is actually reachable
from the turn that produced it, since a file on disk nobody can find is the same
as no file. Second, that every failure mode degrades to "no recording" rather
than to an exception — the caller is a live interview, and a full disk must
cost the candidate nothing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from core.config import settings
from services import interview_service as interviews
from services import recording_service as recordings

from test_interview import _answer_current, _shortlisted

SR = 16_000


def _tone(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


@pytest.fixture
def live(fake_llm):
    """A started interview with its first question on screen.

    ``start`` opens the interview; ``ask_next`` is what puts a turn row behind
    the question, and a turn is what a recording attaches to.
    """
    user, application, interview = _shortlisted()
    interviews.start(interview.id, user_id=user.id)
    interviews.ask_next(interview.id)
    return user, interview


def test_a_saved_clip_lands_where_the_turn_can_find_it(live):
    _user, interview = live
    path = recordings.save_answer(interview.id, _tone(), SR, seq=1)

    assert path
    on_disk = Path(path)
    assert on_disk.exists() and on_disk.suffix == ".wav"
    assert on_disk.stat().st_size > 44  # a WAV header alone would be 44 bytes
    assert on_disk.parent == recordings.session_dir(interview.id)


def test_the_interview_row_points_at_the_folder(live):
    _user, interview = live
    recordings.save_answer(interview.id, _tone(), SR, seq=1)

    reloaded = interviews.get(interview.id)
    assert reloaded is not None
    assert reloaded.recording_path == str(recordings.session_dir(interview.id))


def test_the_folder_is_recorded_once_and_not_rewritten_per_answer(live):
    """``recording_path`` is the session's directory, not the newest clip."""
    _user, interview = live
    recordings.save_answer(interview.id, _tone(), SR, seq=1)
    first = interviews.get(interview.id).recording_path  # type: ignore[union-attr]
    recordings.save_answer(interview.id, _tone(), SR, seq=2)

    assert interviews.get(interview.id).recording_path == first  # type: ignore[union-attr]


def test_a_clip_is_reachable_from_the_answer_it_belongs_to(live):
    """The whole point: a recruiter reading a transcript can hear it."""
    user, interview = live
    turn = interviews.current_turn(interview.id)
    assert turn is not None
    path = recordings.save_answer(interview.id, _tone(), SR, seq=turn.seq)
    _answer_current(interview, user, audio_path=path)

    answered = interviews.transcript(interview.id)[0]
    assert answered.answer_audio_path == path
    assert recordings.clips_for(interview.id) == [(answered.seq, Path(path))]


def test_clips_read_from_the_turn_rows_not_from_the_directory(live):
    """A stray file in the folder is not a record of anything being said."""
    _user, interview = live
    stray = recordings.session_dir(interview.id)
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "answer_09.wav").write_bytes(b"not a real clip")

    assert recordings.clips_for(interview.id) == []


def test_a_clip_deleted_from_disk_stops_being_offered(live):
    """Retention policy, a moved media directory, a manual cleanup — all the
    same to a recruiter, who must not be handed a dead player."""
    user, interview = live
    turn = interviews.current_turn(interview.id)
    path = recordings.save_answer(interview.id, _tone(), SR, seq=turn.seq)  # type: ignore[union-attr]
    _answer_current(interview, user, audio_path=path)
    Path(path).unlink()

    assert recordings.clips_for(interview.id) == []


def test_an_empty_buffer_saves_nothing_and_says_so(live):
    _user, interview = live
    assert recordings.save_answer(interview.id, np.zeros(0, dtype=np.float32), SR, seq=1) == ""
    assert recordings.save_answer(interview.id, None, SR, seq=1) == ""  # type: ignore[arg-type]


def test_a_write_failure_costs_the_candidate_nothing(live, monkeypatch):
    """The interview does not depend on the file, so a disk error is a log line."""
    _user, interview = live
    monkeypatch.setattr(
        Path, "write_bytes", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )

    assert recordings.save_answer(interview.id, _tone(), SR, seq=1) == ""
    assert interviews.get(interview.id).recording_path == ""  # type: ignore[union-attr]


def test_recording_can_be_switched_off_entirely(live, monkeypatch):
    """``PROCTOR_RECORD_SESSION=false``. Settings are frozen, so the whole
    settings object is swapped rather than one field poked."""
    _user, interview = live
    monkeypatch.setattr(
        recordings,
        "settings",
        replace(settings, proctoring=replace(settings.proctoring, record_session=False)),
    )

    assert not recordings.enabled()
    assert recordings.save_answer(interview.id, _tone(), SR, seq=1) == ""
    assert not recordings.session_dir(interview.id).exists()


def test_purge_removes_the_media_and_the_paths_that_point_at_it(live):
    """Deleting a user cascades their rows; the files they wrote are not rows."""
    user, interview = live
    turn = interviews.current_turn(interview.id)
    path = recordings.save_answer(interview.id, _tone(), SR, seq=turn.seq)  # type: ignore[union-attr]
    _answer_current(interview, user, audio_path=path)

    assert recordings.purge(interview.id) == 1
    assert not Path(path).exists()
    assert not recordings.session_dir(interview.id).exists()
    assert interviews.get(interview.id).recording_path == ""  # type: ignore[union-attr]
    assert interviews.transcript(interview.id)[0].answer_audio_path == ""


def test_purging_an_interview_that_never_recorded_is_not_an_error(live):
    _user, interview = live
    assert recordings.purge(interview.id) == 0
