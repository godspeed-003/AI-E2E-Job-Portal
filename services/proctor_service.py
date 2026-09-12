"""Proctoring service — persists events and computes the integrity report.

This sits between the real-time proctoring layer (``proctoring/``) and the
database. It is the only place that writes to ``proctor_events`` or the
``integrity_*`` columns on ``interviews``.

The integrity score starts at 100 and is reduced by weighted deductions for
each event. The computation is deterministic given the stored events, so
``recompute()`` can re-derive it at any time without re-running the CV
backends — useful when the recruiter views a session recorded before the
weights were tuned.

The verdict thresholds are:
    clean  — score ≥ 80
    review — score ≥ PROCTOR_INTEGRITY_FAIL_BELOW (default 55)
    flag   — below that

Only the integrity report is shown to the candidate; the raw events and
verdict are visible to the recruiter only. This is advisory evidence for a
human reviewer, not a finding of guilt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from core import db
from core.config import settings
from proctoring.rules import ProctoringEvent, compute_integrity_score

log = logging.getLogger(__name__)


# ── snapshot helpers ──────────────────────────────────────────────────────── #

def _snapshot_path(interview_id: int, label: str) -> Path:
    directory = settings.media_dir / "proctoring" / str(interview_id)
    directory.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return directory / f"{label}_{ts}.jpg"


def save_snapshot(
    interview_id: int,
    frame: np.ndarray,
    label: str = "event",
) -> Path | None:
    """Write a JPEG frame to disk and return its path; return None on failure."""
    if not settings.proctoring.save_snapshots:
        return None
    try:
        import cv2
        path = _snapshot_path(interview_id, label)
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return path
    except Exception as exc:
        log.warning("Snapshot write failed: %s", exc)
        return None


def save_enrollment_snapshot(interview_id: int, frame: np.ndarray) -> str | None:
    """Save the enrollment frame and write the path to the interview row."""
    path = save_snapshot(interview_id, frame, label="enrollment")
    if path is None:
        return None
    db.execute(
        "UPDATE interviews SET enrollment_snapshot_path = ? WHERE id = ?",
        (str(path), interview_id),
    )
    return str(path)


def purge_snapshots(interview_id: int) -> int:
    """Delete an interview's snapshots from disk. Returns how many went.

    Deleting an interview cascades its rows, including the event rows the
    snapshots belong to. The JPEGs are not rows, so they survive the cascade and
    have to be swept explicitly — otherwise a sandbox reset leaves photographs of
    somebody's face behind, which is the one kind of leftover that actually
    matters.
    """
    directory = settings.media_dir / "proctoring" / str(interview_id)
    if not directory.exists():
        return 0

    removed = 0
    for path in sorted(directory.glob("*.jpg")):
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            log.warning("Could not delete %s: %s", path, exc)
    try:
        directory.rmdir()
    except OSError:
        pass  # something else is in there; leave it alone
    return removed


# ── event persistence ─────────────────────────────────────────────────────── #

def record_event(
    interview_id: int,
    event: ProctoringEvent,
    elapsed_seconds: float,
    snapshot_frame: np.ndarray | None = None,
) -> int:
    """Persist one proctor event and optionally a snapshot.  Returns the row id."""
    snapshot_path: str | None = None
    if event.snapshot_requested and snapshot_frame is not None:
        saved = save_snapshot(interview_id, snapshot_frame, label=event.kind)
        if saved:
            snapshot_path = str(saved)

    row_id = db.execute(
        """
        INSERT INTO proctor_events
            (interview_id, ts, elapsed_seconds, kind, severity,
             confidence, duration_seconds, detail, snapshot_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interview_id,
            db.utc_now_iso(),
            round(elapsed_seconds, 2),
            event.kind,
            event.severity,
            round(event.confidence, 4),
            round(event.duration_seconds, 2),
            db.dumps(event.detail),
            snapshot_path,
        ),
    )
    return row_id


def record_browser_event(
    interview_id: int,
    kind: str,
    elapsed_seconds: float,
) -> int:
    """Convenience wrapper for browser-side signals (tab switch, paste…)."""
    from proctoring.rules import RuleEngine
    engine = RuleEngine()
    event = engine.browser_event(kind)
    return record_event(interview_id, event, elapsed_seconds)


# ── integrity scoring ─────────────────────────────────────────────────────── #

def events_for(interview_id: int) -> list[dict[str, Any]]:
    """Every event for one interview, in the order it happened.

    ``ts`` is only second-precision and a burst of signals routinely lands
    inside one second, so ``id`` breaks the tie with true insertion order.
    Without it SQLite is free to return ties in any order, which would make
    both the recruiter's timeline and :func:`recompute` non-deterministic.
    """
    rows = db.query(
        "SELECT * FROM proctor_events WHERE interview_id = ? ORDER BY ts, id",
        (interview_id,),
    )
    return [db.row_to_dict(r) for r in rows]


def recompute(interview_id: int) -> tuple[int, str]:
    """Derive integrity score and verdict from stored events and persist them."""
    events = events_for(interview_id)
    score, verdict = compute_integrity_score(events)
    report = _build_report(events, score, verdict)
    db.execute(
        """
        UPDATE interviews
           SET integrity_score   = ?,
               integrity_verdict = ?,
               integrity_report  = ?,
               updated_at        = ?
         WHERE id = ?
        """,
        (score, verdict, db.dumps(report), db.utc_now_iso(), interview_id),
    )
    return score, verdict


def _build_report(
    events: list[dict[str, Any]],
    score: int,
    verdict: str,
) -> dict[str, Any]:
    """Structured summary for the recruiter's integrity panel."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_kind.setdefault(ev["kind"], []).append(ev)

    summary_lines = []
    for kind, rows in sorted(by_kind.items()):
        count = len(rows)
        severity = rows[0]["severity"]
        summary_lines.append(
            {"kind": kind, "count": count, "severity": severity}
        )

    return {
        "score": score,
        "verdict": verdict,
        "event_count": len(events),
        "kinds": summary_lines,
        "generated_at": db.utc_now_iso(),
        "advisory": (
            "This report is advisory evidence for a human reviewer. "
            "It is not a finding of misconduct."
        ),
    }


def finalize(interview_id: int) -> tuple[int, str]:
    """Call at interview completion to lock in the integrity score."""
    return recompute(interview_id)
