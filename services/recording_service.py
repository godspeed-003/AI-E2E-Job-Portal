"""Keep the audio a candidate actually spoke.

Three columns have been carrying this feature's shape since the schema was
written and nothing ever filled them: ``interview_turns.answer_audio_path``,
``interviews.recording_path``, and the ``PROCTOR_RECORD_SESSION`` flag. What
follows is the missing half — the part that writes the file.

The reason to keep it is narrower than "recording the interview". Every score
in the portal is computed from a *transcript*, and a transcript is a lossy guess
produced by a speech model. When a recruiter disputes a low score, or a
candidate does, the recording is the only artefact that can settle whether the
model heard them correctly. Proctoring's background-voice signal has the same
problem in sharper form: it is advisory precisely because a human is expected
to listen to the clip, and that is impossible if the clip was thrown away.

**Audio only.** Video is captured for proctoring but never written to disk here.
Storing it would mean muxing a WebRTC video track for every candidate — a large
dependency, large files, and a much heavier consent conversation — to serve a
review need the audio already covers.

Files live at ``<media_dir>/interviews/<interview_id>/answer_<seq>.wav``, one per
answered turn. Writing is best-effort: a full disk or a read-only volume costs
the candidate nothing, because the interview does not depend on the file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from core import db
from core.config import settings

log = logging.getLogger(__name__)


def enabled() -> bool:
    """Whether answers are being kept. ``PROCTOR_RECORD_SESSION=false`` to stop."""
    return bool(settings.proctoring.record_session)


def session_dir(interview_id: int) -> Path:
    return settings.media_dir / "interviews" / str(interview_id)


def save_answer(
    interview_id: int,
    samples: np.ndarray,
    sample_rate: int,
    *,
    seq: int,
) -> str:
    """Write one answer's audio; return its path, or ``""`` if nothing was kept.

    Returns a string rather than a ``Path`` because the caller's next move is
    ``submit_answer(audio_path=...)`` and the column is TEXT. An empty string is
    the "no recording" value already stored in every existing row.
    """
    if not enabled() or samples is None or len(samples) == 0:
        return ""

    try:
        from speech import stt as stt_module

        directory = session_dir(interview_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"answer_{seq:02d}.wav"
        wav = stt_module.pcm_to_wav_bytes(samples, sample_rate)
        path.write_bytes(wav)
    except Exception as exc:
        # Losing a recording is a diagnostic problem, not a candidate problem.
        log.warning("Could not save answer audio for interview %s: %s", interview_id, exc)
        return ""

    _remember_session_dir(interview_id, directory)
    return str(path)


def _remember_session_dir(interview_id: int, directory: Path) -> None:
    """Point ``interviews.recording_path`` at the folder, once.

    The column holds the directory rather than a file list: turns already carry
    their own paths, and a directory stays correct as more answers arrive.
    """
    try:
        db.execute(
            "UPDATE interviews SET recording_path = ? "
            " WHERE id = ? AND (recording_path IS NULL OR recording_path = '')",
            (str(directory), interview_id),
        )
    except Exception as exc:
        log.warning("Could not record session path for interview %s: %s", interview_id, exc)


def clips_for(interview_id: int) -> list[tuple[int, Path]]:
    """``(seq, path)`` for every answer clip still on disk, in order.

    Read from the turn rows rather than by globbing the directory: a row is the
    record of what was said, and a stray file in the folder is not.
    """
    rows = db.query(
        "SELECT seq, answer_audio_path FROM interview_turns "
        " WHERE interview_id = ? AND answer_audio_path IS NOT NULL "
        "   AND answer_audio_path != '' ORDER BY seq",
        (interview_id,),
    )
    clips: list[tuple[int, Path]] = []
    for row in rows:
        path = Path(row["answer_audio_path"])
        if path.exists():
            clips.append((int(row["seq"]), path))
    return clips


def purge(interview_id: int) -> int:
    """Delete an interview's clips from disk. Returns how many went.

    Deleting a sandbox account cascades its database rows; the media it wrote
    is not in the database, so it needs saying out loud. Also the honest answer
    to "delete my recording" from a candidate.
    """
    directory = session_dir(interview_id)
    if not directory.exists():
        return 0

    removed = 0
    for path in sorted(directory.glob("answer_*.wav")):
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            log.warning("Could not delete %s: %s", path, exc)
    try:
        directory.rmdir()
    except OSError:
        pass  # something else is in there; leave it alone

    db.execute(
        "UPDATE interviews SET recording_path = '' WHERE id = ?", (interview_id,)
    )
    db.execute(
        "UPDATE interview_turns SET answer_audio_path = '' WHERE interview_id = ?",
        (interview_id,),
    )
    return removed
