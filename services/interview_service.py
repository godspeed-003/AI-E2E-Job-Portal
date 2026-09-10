"""The AI interviewer: one tailored plan per shortlist, then a conversation.

This replaces ``modules/interviewer.py``, which asked every candidate the same
five questions from a fixed list and POSTed them to a hardcoded Ollama URL.
Three things this module owes the rest of the portal:

* **A plan that belongs to one candidate.** :func:`build_plan` runs the moment
  an application is shortlisted, against the resume that was actually scored, so
  the first question already knows what this person built and which requirement
  their resume left thin. The plan is stored rather than regenerated per turn:
  the candidate waits on one model call at shortlist time, not one per question.
* **The freedom to follow the conversation.** :func:`decide_next_turn` picks
  between digging into the answer just given (``probe``), pivoting onto
  something the candidate raised unprompted and tying it back to the job
  description (``steer``), moving on (``next_planned``) and stopping
  (``wrap_up``) — inside a hard turn budget, with the plan authoritative over
  the model's enthusiasm.
* **Nothing in session state.** Every question, answer, guardrail verdict and
  decision is a row, so a refresh, a dropped WebRTC connection or a closed
  laptop resumes on the same question with the same clock.

The module is transport-agnostic on purpose: **text answers in, questions out.**
``speech/`` and ``streamlit-webrtc`` drive it today; a LiveKit agent worker can
call these same functions later without a line changing here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from core import db
from core.config import settings
from llm import get_llm
from services import application_service as apps
from services import catalog_service as catalog
from services import guardrail_service as guardrails

log = logging.getLogger(__name__)

STATUSES = ("pending", "in_progress", "completed", "expired")
ACTIONS = ("planned", "probe", "steer")
SOURCES = ("resume", "jd_gap", "culture", "behavioural", "answer")

# The interview's criteria are deliberately not the resume's five. A transcript
# shows how someone reasons out loud, which a resume cannot; reusing the resume's
# labels would invite a recruiter to read one score as a correction of the other.
CRITERIA = (
    "technical_depth",
    "problem_solving",
    "communication",
    "culture_fit",
    "practical_impact",
)
MAX_CRITERION = 5
MAX_TOTAL_SCORE = len(CRITERIA) * MAX_CRITERION  # 25 — the column default too

# Two adaptive turns in a row is a conversation; three is a model that has
# forgotten it had a plan. After this many the next question comes from the plan.
MAX_CONSECUTIVE_ADAPTIVE = 2

# A rejected answer does not burn a turn, but it cannot loop forever either: a
# candidate whose speech-to-text keeps under-transcribing would be locked out of
# their own interview. The last attempt is accepted and flagged for the recruiter.
MAX_REJECTS_PER_TURN = 3

MAX_QUESTION_CHARS = 400
MAX_ANSWER_CHARS = 6_000
MAX_PLAN_QUESTIONS = 12

_SELECT = """
    SELECT i.*, a.user_id AS user_id, a.role_id AS role_id
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
"""


class InterviewError(Exception):
    """Something the candidate or recruiter needs to be told, in their words."""


# --------------------------------------------------------------------------- #
# Small text helpers
#
# Steering has to work with the provider down, so "did the candidate mention
# this?" is answered here rather than by a model call.
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
_STOP = frozenset(
    """
    a an and are as at be been but by can did do for from had has have how i if in
    is it its me my no not of on or our so that the their them then there they this
    to too was we were what when where which who will with would you your about
    """.split()
)


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _content_words(text: str) -> set[str]:
    """Words worth matching on: no stopwords, nothing shorter than four letters."""
    return {word for word in _words(text) if len(word) > 3 and word not in _STOP}


def mentions(text: str, keyword: str) -> bool:
    """Does ``text`` name ``keyword``, respecting word boundaries?

    Same rule as the resume ATS matcher: a substring test would let ``Java`` match
    ``JavaScript`` and steer the interview onto a language nobody mentioned. A
    multi-word keyword is matched as a phrase, which is what a topic usually is.
    """
    keyword = (keyword or "").strip().lower()
    if len(keyword) < 3:
        return False
    haystack = (text or "").lower()
    if " " in keyword:
        return keyword in haystack
    pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _clip(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _str_list(value: Any, limit: int = 8) -> tuple[str, ...]:
    """A model's idea of a list of strings, made into one."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):  # {"hint": "..."} happens
            item = next((v for v in item.values() if isinstance(v, str)), "")
        text = _clip(item, MAX_QUESTION_CHARS)
        if text and text not in out:
            out.append(text)
    return tuple(out[:limit])


def _one_of(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in allowed else default


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedQuestion:
    """One question written for this candidate, with the reason it was chosen."""

    question: str
    focus_area: str = ""
    source: str = "resume"
    rationale: str = ""
    follow_ups: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "focus_area": self.focus_area,
            "source": self.source,
            "rationale": self.rationale,
            "follow_ups": list(self.follow_ups),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> PlannedQuestion | None:
        if isinstance(payload, str):
            payload = {"question": payload}
        if not isinstance(payload, dict):
            return None
        question = _clip(
            payload.get("question") or payload.get("text"), MAX_QUESTION_CHARS
        )
        if len(question) < 10:  # not a question, whatever the model called it
            return None
        return cls(
            question=question,
            focus_area=_clip(payload.get("focus_area") or payload.get("focus"), 60),
            source=_one_of(payload.get("source"), SOURCES, "resume"),
            rationale=_clip(payload.get("rationale") or payload.get("why"), 240),
            follow_ups=_str_list(
                payload.get("follow_up_hints")
                or payload.get("follow_ups")
                or payload.get("follow_up"),
                limit=3,
            ),
        )


@dataclass(frozen=True)
class WatchTopic:
    """Something to pivot into if the candidate raises it unprompted.

    This is the "slowly steer towards that" requirement made cheap: the keywords
    are matched against the answer with a set intersection, so the *decision* to
    steer costs nothing and still works when the provider is throttled. Only the
    wording of the pivot question needs a model, and there is a template for that.
    """

    topic: str
    why: str = ""
    probe: str = ""
    keywords: tuple[str, ...] = ()

    def mentioned_in(self, text: str) -> bool:
        return any(mentions(text, keyword) for keyword in self.match_terms)

    @property
    def match_terms(self) -> tuple[str, ...]:
        return self.keywords or (self.topic,)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "why_relevant": self.why,
            "probe": self.probe,
            "keywords": list(self.keywords),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> WatchTopic | None:
        if isinstance(payload, str):
            payload = {"topic": payload}
        if not isinstance(payload, dict):
            return None
        topic = _clip(payload.get("topic") or payload.get("name"), 80)
        if len(topic) < 3:
            return None
        keywords = _str_list(payload.get("keywords") or payload.get("terms"), limit=6)
        if not keywords:
            # The model usually skips these. Deriving them from the topic keeps
            # steering keyword-driven instead of quietly needing a model call.
            keywords = tuple({topic.lower(), *_content_words(topic)})
        return cls(
            topic=topic,
            why=_clip(payload.get("why_relevant") or payload.get("why"), 240),
            probe=_clip(payload.get("probe") or payload.get("question"), MAX_QUESTION_CHARS),
            keywords=keywords,
        )


@dataclass(frozen=True)
class Plan:
    """Everything the agent decided before the candidate said a word."""

    questions: tuple[PlannedQuestion, ...] = ()
    topics_to_watch: tuple[WatchTopic, ...] = ()
    opening: str = ""
    closing: str = ""
    degraded: bool = False  # generic fallback: the model was unavailable

    def __bool__(self) -> bool:
        return bool(self.questions)

    def __len__(self) -> int:
        return len(self.questions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "opening": self.opening,
            "closing": self.closing,
            "questions": [question.as_dict() for question in self.questions],
            "topics_to_watch": [topic.as_dict() for topic in self.topics_to_watch],
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> Plan:
        if isinstance(payload, str):
            payload = db.loads(payload, {})
        if not isinstance(payload, dict):
            return cls()
        raw_questions = payload.get("questions") or payload.get("plan") or []
        questions = [PlannedQuestion.from_dict(item) for item in raw_questions]
        raw_topics = payload.get("topics_to_watch") or payload.get("topics") or []
        topics = [WatchTopic.from_dict(item) for item in raw_topics]
        return cls(
            questions=tuple(q for q in questions if q)[:MAX_PLAN_QUESTIONS],
            topics_to_watch=tuple(t for t in topics if t)[:8],
            opening=_clip(payload.get("opening"), MAX_QUESTION_CHARS),
            closing=_clip(payload.get("closing"), MAX_QUESTION_CHARS),
            degraded=bool(payload.get("degraded")),
        )

    def topics_named_in(self, text: str) -> tuple[WatchTopic, ...]:
        return tuple(topic for topic in self.topics_to_watch if topic.mentioned_in(text))


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Turn:
    """One question and, once it arrives, the answer to it."""

    id: int
    interview_id: int
    seq: int
    question: str
    focus_area: str = ""
    source: str = ""
    rationale: str = ""
    action: str = "planned"

    detected_topics: tuple[str, ...] = ()
    answer: str = ""
    answer_words: int = 0
    answer_audio_path: str = ""
    transcript_source: str = ""
    asked_at: str = ""
    answered_at: str = ""
    answer_seconds: float | None = None
    guardrail: dict[str, Any] = field(default_factory=dict)
    rejected_attempts: int = 0

    @property
    def answered(self) -> bool:
        return bool(self.answered_at)

    @property
    def is_adaptive(self) -> bool:
        return self.action in ("probe", "steer")

    @property
    def flags(self) -> tuple[str, ...]:
        return tuple(self.guardrail.get("flags") or ())

    @property
    def was_forced(self) -> bool:
        """Accepted only because the candidate ran out of retries."""
        return "forced_accept" in self.flags


@dataclass(frozen=True)
class Interview:
    """The interview row, plus the ``user_id``/``role_id`` it hangs off."""

    id: int
    application_id: int
    user_id: int
    role_id: str
    status: str
    opens_at: str = ""
    closes_at: str = ""
    duration_limit_seconds: int = 1800
    planned_questions: int = 6
    max_turns: int = 9
    max_attempts: int = 1
    attempt_count: int = 0
    started_at: str = ""
    deadline_at: str = ""
    completed_at: str = ""

    plan: Plan = field(default_factory=Plan)
    plan_generated_at: str = ""
    evaluation: dict[str, Any] = field(default_factory=dict)
    total_score: int | None = None
    max_total_score: int = MAX_TOTAL_SCORE
    integrity_score: int | None = None
    integrity_verdict: str = ""
    recording_path: str = ""
    consent_accepted_at: str = ""
    is_sandbox: bool = False
    created_at: str = ""
    updated_at: str = ""

    # -- the clock ---------------------------------------------------------- #

    @property
    def window_state(self) -> str:
        """``before`` | ``open`` | ``closed`` — the recruiter's window, not the timer."""
        now = db.utc_now()
        opens, closes = db.parse_ts(self.opens_at), db.parse_ts(self.closes_at)
        if opens and now < opens:
            return "before"
        if closes and now > closes:
            return "closed"
        return "open"

    @property
    def window_seconds_left(self) -> int:
        closes = db.parse_ts(self.closes_at)
        if closes is None:
            return 0
        return max(0, int((closes - db.utc_now()).total_seconds()))

    @property
    def ends_at(self) -> datetime | None:
        """When this attempt must stop: the earlier of the timer and the window.

        ``None`` until the candidate starts — a pending interview has no timer, and
        reporting the window's close as "time left" would start a countdown days
        before there is anything to count down to.
        """
        deadline = db.parse_ts(self.deadline_at)
        if deadline is None:
            return None
        closes = db.parse_ts(self.closes_at)
        return min(deadline, closes) if closes else deadline

    @property
    def seconds_left(self) -> int | None:
        ends = self.ends_at
        if ends is None:
            return None
        return max(0, int((ends - db.utc_now()).total_seconds()))

    @property
    def out_of_time(self) -> bool:
        return self.status == "in_progress" and self.seconds_left == 0

    # -- state -------------------------------------------------------------- #

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_live(self) -> bool:
        return self.status == "in_progress"

    @property
    def has_plan(self) -> bool:
        return bool(self.plan)

    @property
    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self.attempt_count)

    @property
    def can_start(self) -> bool:
        """Would :func:`start` succeed right now?"""
        if self.status == "in_progress":
            return not self.out_of_time
        if self.status in ("completed", "expired"):
            return False
        return self.window_state == "open" and self.attempts_left > 0

    @property
    def scored(self) -> bool:
        return self.total_score is not None

    def criteria(self) -> dict[str, int]:
        raw = self.evaluation.get("criteria")
        return dict(raw) if isinstance(raw, dict) else {}


@dataclass(frozen=True)
class Decision:
    """What to do after an answer, and who decided it."""

    action: str  # probe | steer | next_planned | wrap_up
    question: str = ""
    focus_area: str = ""
    source: str = "answer"
    rationale: str = ""
    topics: tuple[str, ...] = ()
    decided_by: str = "rule"  # rule | model

    @property
    def is_wrap_up(self) -> bool:
        return self.action == "wrap_up"

    @property
    def turn_action(self) -> str:
        """What the row records: the plan's questions are ``planned``."""
        return self.action if self.action in ("probe", "steer") else "planned"


@dataclass(frozen=True)
class AnswerOutcome:
    """The result of offering an answer — accepted, or handed back with a reason."""

    accepted: bool
    message: str = ""
    turn: Turn | None = None
    flags: tuple[str, ...] = ()
    forced: bool = False
    attempts_left: int = 0

    @property
    def retry(self) -> bool:
        """Rejected, and the candidate still has attempts on this question."""
        return not self.accepted and self.attempts_left > 0


# --------------------------------------------------------------------------- #
# Row mapping
# --------------------------------------------------------------------------- #


def _to_interview(row: Any) -> Interview:
    return Interview(
        id=row["id"],
        application_id=row["application_id"],
        user_id=row["user_id"],
        role_id=row["role_id"],
        status=row["status"],
        opens_at=row["opens_at"] or "",
        closes_at=row["closes_at"] or "",
        duration_limit_seconds=row["duration_limit_seconds"],
        planned_questions=row["planned_questions"],
        max_turns=row["max_turns"],
        max_attempts=row["max_attempts"],
        attempt_count=row["attempt_count"],
        started_at=row["started_at"] or "",
        deadline_at=row["deadline_at"] or "",
        completed_at=row["completed_at"] or "",
        plan=Plan.from_dict(db.loads(row["plan"], {})),
        plan_generated_at=row["plan_generated_at"] or "",
        evaluation=db.loads(row["evaluation"], {}) or {},
        total_score=row["total_score"],
        max_total_score=row["max_total_score"] or MAX_TOTAL_SCORE,
        integrity_score=row["integrity_score"],
        integrity_verdict=row["integrity_verdict"] or "",
        recording_path=row["recording_path"] or "",
        consent_accepted_at=row["consent_accepted_at"] or "",
        is_sandbox=bool(row["is_sandbox"]),
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


def _to_turn(row: Any) -> Turn:
    return Turn(
        id=row["id"],
        interview_id=row["interview_id"],
        seq=row["seq"],
        question=row["question"],
        focus_area=row["focus_area"] or "",
        source=row["source"] or "",
        rationale=row["rationale"] or "",
        action=row["action"] or "planned",
        detected_topics=tuple(db.loads(row["detected_topics"], []) or ()),
        answer=row["answer"] or "",
        answer_words=row["answer_words"] or 0,
        answer_audio_path=row["answer_audio_path"] or "",
        transcript_source=row["transcript_source"] or "",
        asked_at=row["asked_at"] or "",
        answered_at=row["answered_at"] or "",
        answer_seconds=row["answer_seconds"],
        guardrail=db.loads(row["guardrail"], {}) or {},
        rejected_attempts=row["rejected_attempts"] or 0,
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def get(interview_id: int) -> Interview | None:
    row = db.query_one(f"{_SELECT} WHERE i.id = ?", (interview_id,))
    return _to_interview(row) if row else None


def for_application(application_id: int) -> Interview | None:
    row = db.query_one(f"{_SELECT} WHERE i.application_id = ?", (application_id,))
    return _to_interview(row) if row else None


def for_user(user_id: int) -> list[Interview]:
    rows = db.query(
        f"{_SELECT} WHERE a.user_id = ? ORDER BY i.created_at DESC", (user_id,)
    )
    return [_to_interview(row) for row in rows]


def for_role(role_id: str, *, include_sandbox: bool = False) -> list[Interview]:
    sql = f"{_SELECT} WHERE a.role_id = ?"
    if not include_sandbox:
        sql += " AND i.is_sandbox = 0"
    sql += " ORDER BY i.total_score IS NULL, i.total_score DESC, i.completed_at"
    return [_to_interview(row) for row in db.query(sql, (role_id,))]


def require(interview_id: int, user_id: int) -> Interview:
    """The interview, or an error naming why this user cannot have it.

    Every entry point takes the candidate's id rather than trusting the caller to
    have checked: an interview id in a URL must not be enough to sit somebody
    else's interview.
    """
    interview = get(interview_id)
    if interview is None or interview.user_id != user_id:
        raise InterviewError("That interview does not exist.")
    return interview


def transcript(interview_id: int) -> list[Turn]:
    rows = db.query(
        "SELECT * FROM interview_turns WHERE interview_id = ? ORDER BY seq",
        (interview_id,),
    )
    return [_to_turn(row) for row in rows]


def current_turn(interview_id: int) -> Turn | None:
    """The question waiting for an answer, if there is one."""
    row = db.query_one(
        "SELECT * FROM interview_turns WHERE interview_id = ? AND answered_at IS NULL "
        "ORDER BY seq DESC LIMIT 1",
        (interview_id,),
    )
    return _to_turn(row) if row else None


def answered_turns(interview_id: int) -> list[Turn]:
    return [turn for turn in transcript(interview_id) if turn.answered]


def progress(interview: Interview) -> tuple[int, int]:
    """``(answered, budget)`` for the room's HUD."""
    return len(answered_turns(interview.id)), interview.max_turns


def _touch(interview_id: int, **columns: Any) -> None:
    columns["updated_at"] = db.utc_now_iso()
    assignments = ", ".join(f"{name} = ?" for name in columns)
    db.execute(
        f"UPDATE interviews SET {assignments} WHERE id = ?",
        (*columns.values(), interview_id),
    )


# --------------------------------------------------------------------------- #
# Creating the interview — the moment an application is shortlisted
# --------------------------------------------------------------------------- #


def _budget(role: catalog.Role) -> tuple[int, int]:
    """``(planned, max_turns)``.

    The ceiling always leaves room for the whole plan — a role configured with ten
    questions must not be cut off at the global nine-turn budget, or the last
    planned questions could never be asked.
    """
    planned = max(1, min(role.question_budget, MAX_PLAN_QUESTIONS))
    return planned, max(planned, settings.interview.max_turns)


def ensure_for_application(
    application: apps.Application, *, plan: bool = True
) -> Interview:
    """Create (once) the interview a shortlisted application has earned.

    Idempotent by the ``UNIQUE`` constraint on ``application_id``: a recruiter
    re-running the screening, or a retry after an outage, finds the existing row
    and its window rather than moving the candidate's deadline.

    The row is committed *before* the plan is generated, exactly as ``apply()``
    commits before ``screen()``: a shortlist is a promise to the candidate, and a
    throttled provider must not be able to swallow it. A missing plan is repaired
    on the next call or at :func:`start`.
    """
    existing = for_application(application.id)
    if existing is None:
        role = catalog.get_role(application.role_id)
        if role is None:
            raise InterviewError("That role is no longer listed.")
        planned, max_turns = _budget(role)
        now = db.utc_now()
        db.execute(
            """
            INSERT INTO interviews (
                application_id, status, opens_at, closes_at,
                duration_limit_seconds, planned_questions, max_turns,
                max_attempts, max_total_score, is_sandbox, created_at, updated_at
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (application_id) DO NOTHING
            """,
            (
                application.id,
                now.isoformat(),
                (now + timedelta(days=role.window_days)).isoformat(),
                role.duration_minutes * 60,
                planned,
                max_turns,
                settings.interview.max_attempts,
                MAX_TOTAL_SCORE,
                int(application.is_sandbox),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        existing = for_application(application.id)
        if existing is None:  # pragma: no cover - the insert just ran
            raise InterviewError("The interview could not be created.")
        db.audit(
            application.user_id,
            "interview.created",
            interview_id=existing.id,
            role_id=application.role_id,
            closes_at=existing.closes_at,
        )
    if plan and not existing.has_plan:
        try:
            existing = build_plan(existing.id)
        except InterviewError as error:
            # Not fatal, and not the candidate's problem: they are still
            # shortlisted, and start() will try again or fall back.
            log.warning("interview plan deferred for %s: %s", existing.id, error)
    return existing


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #

MAX_RESUME_CHARS = 12_000


@lru_cache(maxsize=8)
def _template(name: str) -> str:
    path = settings.prompts_dir / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InterviewError(f"Prompt template {name} is missing.") from exc


def _fill(name: str, replacements: dict[str, str]) -> str:
    """Fill a template with ``replace``, not ``format``.

    Every template here ends with a literal JSON skeleton, so ``str.format`` would
    need each brace doubled — one missed pair fails at runtime rather than review.
    """
    prompt = _template(name)
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def _context(interview: Interview) -> tuple[apps.Application, catalog.Role, catalog.Company]:
    application = apps.get(interview.application_id)
    if application is None:
        raise InterviewError("The application behind this interview is gone.")
    pair = catalog.role_with_company(application.role_id)
    if pair is None:
        raise InterviewError("That role is no longer listed.")
    role, company = pair
    return application, role, company


def build_plan(interview_id: int, *, force: bool = False) -> Interview:
    """Write this candidate's question plan. One model call, at shortlist time.

    Tagging each question with its ``source`` is what makes the plan auditable: a
    ``jd_gap`` question exists because the resume did not evidence a requirement,
    and a recruiter reading the transcript can see that was asked on purpose
    rather than wonder why the agent went there.

    Raises :class:`InterviewError` when the provider is unavailable — the caller
    decides whether that is fatal. It never is: ``ensure_for_application`` logs and
    moves on, ``start`` falls back to a plan built from the role itself.
    """
    interview = get(interview_id)
    if interview is None:
        raise InterviewError("That interview does not exist.")
    if interview.has_plan and not force:
        return interview

    application, role, company = _context(interview)
    prompt = _fill(
        "interview_plan.txt",
        {
            "{count}": str(interview.planned_questions),
            "{role_title}": role.title,
            "{company_name}": company.name,
            "{job_description}": role.job_description or "(not provided)",
            "{requirements}": role.requirements_line or "(none listed)",
            "{culture}": company.culture_line or "(not documented)",
            "{values}": ", ".join(company.core_values) or "(not documented)",
            "{matched_requirements}": _bullets(application.ats_matched),
            "{missing_requirements}": _bullets(application.ats_missing),
            "{strengths}": _bullets(application.strengths),
            "{gaps}": _bullets(application.weaknesses),
            "{resume_text}": application.resume_text[:MAX_RESUME_CHARS],
        },
    )
    try:
        payload = get_llm().generate_json(
            prompt,
            system="You are an experienced technical interviewer. Reply with JSON only.",
            temperature=0.4,
            max_output_tokens=2048,
        )
    except Exception as exc:  # LLMError and anything a provider leaks
        raise InterviewError(
            "The interviewer is busy right now — your questions will be ready "
            "before you start."
        ) from exc

    plan = _normalize_plan(payload, role=role, application=application)
    return _store_plan(interview, plan)


def _bullets(items: Any, empty: str = "- (none)") -> str:
    lines = [f"- {_clip(item, 200)}" for item in (items or []) if str(item).strip()]
    return "\n".join(lines) or empty


def _store_plan(interview: Interview, plan: Plan) -> Interview:
    _touch(
        interview.id,
        plan=db.dumps(plan.as_dict()),
        plan_generated_at=db.utc_now_iso(),
        planned_questions=len(plan),
    )
    db.audit(
        interview.user_id,
        "interview.plan",
        interview_id=interview.id,
        questions=len(plan),
        topics=len(plan.topics_to_watch),
        degraded=plan.degraded,
    )
    return get(interview.id) or interview


def _normalize_plan(
    payload: Any, *, role: catalog.Role, application: apps.Application
) -> Plan:
    """Trust the model for the questions, never for the count or the tagging.

    A short plan is topped up from the role's own requirements rather than shipped
    as-is: the turn budget is derived from the number of planned questions, so a
    model that returns two would quietly halve the interview.
    """
    plan = Plan.from_dict(payload if isinstance(payload, dict) else {})
    wanted = max(1, min(role.question_budget, MAX_PLAN_QUESTIONS))
    if len(plan) < wanted:
        filler = _fallback_plan(role, application, count=wanted).questions
        seen = {question.question.lower() for question in plan.questions}
        topped = list(plan.questions)
        for question in filler:
            if len(topped) >= wanted:
                break
            if question.question.lower() not in seen:
                topped.append(question)
        plan = Plan(
            questions=tuple(topped),
            topics_to_watch=plan.topics_to_watch,
            opening=plan.opening,
            closing=plan.closing,
            degraded=plan.degraded or not plan.questions,
        )
    if len(plan) > wanted:
        # The turn budget was sized from the role's question count, so anything
        # past it would be planned and then never reached.
        plan = replace(plan, questions=plan.questions[:wanted])
    return _with_default_topics(plan, role, application)


def _with_default_topics(
    plan: Plan, role: catalog.Role, application: apps.Application
) -> Plan:
    """Guarantee something to steer towards, even if the model listed nothing.

    Every requirement the resume did not evidence becomes a watch topic: if the
    candidate raises it themselves — "we ran that on Kubernetes" — the agent has a
    reason to follow, and the gap that nearly rejected them gets its hearing.
    """
    if plan.topics_to_watch:
        return plan
    topics = [
        WatchTopic(
            topic=requirement,
            why=f"{requirement} is required for {role.title} and the resume did not show it.",
            probe=(
                f"You mentioned {requirement} — walk me through what you did with it, "
                f"and how that would apply to this role."
            ),
            keywords=(requirement,),
        )
        for requirement in (application.ats_missing or role.requirements)[:6]
    ]
    return replace(plan, topics_to_watch=tuple(topics))


def _fallback_plan(
    role: catalog.Role, application: apps.Application, *, count: int
) -> Plan:
    """A usable interview with no model at all.

    Not a placeholder: the questions name this role's requirements and say whether
    the resume evidenced them, which is most of what tailoring buys. It exists so a
    provider outage delays nobody — the candidate's window is days long and their
    attempt is single, so "come back later" is a real cost to them.
    """
    matched = [item for item in application.ats_matched if item]
    missing = [item for item in application.ats_missing if item]
    questions = [
        PlannedQuestion(
            question=(
                "To start, walk me through the piece of work on your resume you are "
                "proudest of — what you built, and what your own contribution was."
            ),
            focus_area="experience",
            source="resume",
            rationale="Opens on the candidate's own material.",
            follow_ups=("What would you do differently now?",),
        )
    ]

    for requirement in matched:
        questions.append(
            PlannedQuestion(
                question=(
                    f"Your resume lists {requirement}. Take one time you used it on "
                    f"real work: what was the problem, and what did you actually do?"
                ),
                focus_area=requirement,
                source="resume",
                rationale=f"{requirement} is evidenced on the resume and required here.",
            )
        )
    for requirement in missing:
        questions.append(
            PlannedQuestion(
                question=(
                    f"This role leans on {requirement}, which I could not find on your "
                    f"resume. What is your exposure to it, and how would you get up to "
                    f"speed?"
                ),
                focus_area=requirement,
                source="jd_gap",
                rationale=f"The resume did not evidence {requirement}.",
            )
        )
    questions.append(
        PlannedQuestion(
            question=(
                "Tell me about a disagreement with a colleague over a technical "
                "decision. How did it end?"
            ),
            focus_area="collaboration",
            source="behavioural",
            rationale="Behavioural anchor, asked of everyone.",
        )
    )
    return Plan(
        questions=tuple(questions[: max(1, count)]),
        opening=(
            f"Thanks for making time. This is a short interview for the "
            f"{role.title} role — I have read your resume, so I will go straight to "
            f"the work. Take your time on each answer."
        ),
        closing="That is everything from me. Thank you for your time.",
        degraded=True,
    )


def ensure_plan(interview_id: int) -> Interview:
    """A plan, guaranteed — the model's if it answers, the role's if it does not."""
    interview = get(interview_id)
    if interview is None:
        raise InterviewError("That interview does not exist.")
    if interview.has_plan:
        return interview
    try:
        return build_plan(interview_id)
    except InterviewError as error:
        log.warning("falling back to a generic plan for %s: %s", interview_id, error)
    application, role, _company = _context(interview)
    plan = _fallback_plan(role, application, count=interview.planned_questions)
    return _store_plan(interview, _with_default_topics(plan, role, application))


# --------------------------------------------------------------------------- #
# The interview itself
# --------------------------------------------------------------------------- #


def start(
    interview_id: int, *, user_id: int, consent: bool = False
) -> Interview:
    """Open (or resume) the interview, enforcing the window, timer and attempt.

    Resuming is the same call as starting and does not spend an attempt: the
    candidate who reloads after a dropped WebRTC connection is not cheating, and
    losing their one attempt to a flaky network would be indefensible. What they
    cannot get back is the clock — ``deadline_at`` was written when they first
    started and is never extended.
    """
    interview = require(interview_id, user_id)
    if interview.is_completed:
        raise InterviewError("You have already completed this interview.")

    if interview.status == "in_progress":
        if interview.out_of_time:
            finish(interview.id, reason="time_limit")
            raise InterviewError(
                "Your interview time ran out. It has been submitted for review."
            )
        return interview

    state = interview.window_state
    if state == "before":
        raise InterviewError(
            f"This interview opens on {interview.opens_at[:10]}."
        )
    if state == "closed" or interview.status == "expired":
        _touch(interview.id, status="expired")
        raise InterviewError(
            f"The interview window closed on {interview.closes_at[:10]}."
        )
    if interview.attempts_left <= 0:
        raise InterviewError("This interview allows a single attempt, already used.")

    interview = ensure_plan(interview.id)
    now = db.utc_now()
    closes = db.parse_ts(interview.closes_at)
    deadline = now + timedelta(seconds=interview.duration_limit_seconds)
    if closes and closes < deadline:
        # Starting ten minutes before the window shuts buys ten minutes, not the
        # full half hour. Better that than a session the recruiter cannot explain.
        deadline = closes
    _touch(
        interview.id,
        status="in_progress",
        started_at=now.isoformat(),
        deadline_at=deadline.isoformat(),
        attempt_count=interview.attempt_count + 1,
        consent_accepted_at=(
            interview.consent_accepted_at or (now.isoformat() if consent else None)
        ),
    )
    db.audit(
        user_id,
        "interview.started",
        interview_id=interview.id,
        deadline_at=deadline.isoformat(),
        degraded_plan=interview.plan.degraded,
    )
    return get(interview.id) or interview


def ask_next(interview_id: int) -> Turn | None:
    """The question to put to the candidate now, or ``None`` when it is over.

    Idempotent: an unanswered question is returned again rather than replaced, so a
    refresh, a second browser tab or a re-render never costs the candidate a turn
    or leaves two questions on screen.
    """
    interview = get(interview_id)
    if interview is None or not interview.is_live:
        return None
    pending = current_turn(interview_id)
    if pending is not None:
        return pending
    if interview.out_of_time:
        finish(interview_id, reason="time_limit")
        return None

    decision = decide_next_turn(interview_id)
    if decision.is_wrap_up:
        finish(interview_id, reason=decision.rationale or "plan complete")
        return None
    return _record_question(interview, decision)


def _record_question(interview: Interview, decision: Decision) -> Turn:
    seq = (
        int(
            db.scalar(
                "SELECT COALESCE(MAX(seq), 0) FROM interview_turns WHERE interview_id = ?",
                (interview.id,),
            )
            or 0
        )
        + 1
    )
    db.execute(
        """
        INSERT INTO interview_turns (
            interview_id, seq, question, focus_area, source, rationale,
            action, detected_topics, asked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interview.id,
            seq,
            decision.question,
            decision.focus_area,
            decision.source,
            decision.rationale,
            decision.turn_action,
            db.dumps(list(decision.topics)),
            db.utc_now_iso(),
        ),
    )
    turn = current_turn(interview.id)
    if turn is None:  # pragma: no cover - the insert just ran
        raise InterviewError("The next question could not be saved.")
    return turn


def decide_next_turn(interview_id: int) -> Decision:
    """Probe, steer, move on, or stop.

    The rules run before the model, not after it. Every one of them protects
    something the candidate can feel — the budget they were promised, the clock,
    and the plan's coverage — so none of them is left to a temperature setting.
    The model is asked only once those are satisfied, and its answer is still
    checked against them.
    """
    interview = get(interview_id)
    if interview is None:
        raise InterviewError("That interview does not exist.")
    return _decide(interview, transcript(interview_id))


_DECISIONS = ("probe", "steer", "next_planned", "wrap_up")


def _planned(remaining: tuple[PlannedQuestion, ...], *, reason: str) -> Decision:
    if not remaining:
        return Decision("wrap_up", rationale=reason)
    question = remaining[0]
    return Decision(
        "next_planned",
        question=question.question,
        focus_area=question.focus_area,
        source=question.source,
        rationale=question.rationale or reason,
    )


def _trailing_adaptive(turns: list[Turn]) -> int:
    count = 0
    for turn in reversed(turns):
        if not turn.is_adaptive:
            break
        count += 1
    return count


def _steered_topics(turns: list[Turn]) -> set[str]:
    return {
        turn.focus_area.lower() for turn in turns if turn.action == "steer" and turn.focus_area
    }


def _decide(interview: Interview, turns: list[Turn]) -> Decision:
    plan = interview.plan
    asked_planned = sum(1 for turn in turns if turn.action == "planned")
    remaining = plan.questions[asked_planned:]
    turns_left = interview.max_turns - len(turns)

    if turns_left <= 0:
        return Decision("wrap_up", rationale="The question budget is spent.")
    if interview.out_of_time:
        return Decision("wrap_up", rationale="The time limit is up.")
    if not turns:
        return _planned(remaining, reason="Opening question.")
    if not remaining:
        return Decision("wrap_up", rationale="Every planned question has been asked.")
    if len(remaining) >= turns_left:
        # The plan comes first: spending the last turns on follow-ups would leave
        # a requirement unasked, and that is the one thing the plan exists to stop.
        return _planned(remaining, reason="Remaining budget is reserved for the plan.")
    if _trailing_adaptive(turns) >= MAX_CONSECUTIVE_ADAPTIVE:
        return _planned(remaining, reason="Back to the plan after two follow-ups.")

    last = turns[-1]
    if last.was_forced:
        # The answer was only accepted because the retries ran out; there is
        # nothing there worth digging into.
        return _planned(remaining, reason="The last answer could not be used.")

    fresh = tuple(
        topic
        for topic in plan.topics_named_in(last.answer)
        if topic.topic.lower() not in _steered_topics(turns)
    )
    try:
        return _model_decision(interview, turns, remaining, fresh)
    except InterviewError as error:
        log.warning("next-turn decision fell back for %s: %s", interview.id, error)
        return _offline_decision(last, remaining, fresh)


def _offline_decision(
    last: Turn, remaining: tuple[PlannedQuestion, ...], fresh: tuple[WatchTopic, ...]
) -> Decision:
    """Steering without a model.

    The candidate raising a watch topic is detected by keyword, so the pivot still
    happens when the provider is down — only its wording is canned.
    """
    if fresh:
        topic = fresh[0]
        return Decision(
            "steer",
            question=topic.probe
            or (
                f"You mentioned {topic.topic} — tell me what you did there, and how "
                f"it maps onto what this role needs."
            ),
            focus_area=topic.topic,
            source="answer",
            rationale=topic.why or f"The candidate raised {topic.topic} unprompted.",
            topics=(topic.topic,),
        )
    if last.answer_words < settings.interview.min_answer_words * 2:
        return Decision(
            "probe",
            question=(
                "Make that concrete for me: one specific example from your own work, "
                "what you did step by step, and how it turned out."
            ),
            focus_area=last.focus_area,
            source="answer",
            rationale="The answer stayed general.",
        )
    return _planned(remaining, reason="Moving on.")


def _model_decision(
    interview: Interview,
    turns: list[Turn],
    remaining: tuple[PlannedQuestion, ...],
    fresh: tuple[WatchTopic, ...],
) -> Decision:
    _application, role, company = _context(interview)
    last = turns[-1]
    prompt = _fill(
        "interview_next_turn.txt",
        {
            "{role_title}": role.title,
            "{company_name}": company.name,
            "{job_description}": role.job_description or "(not provided)",
            "{requirements}": role.requirements_line or "(none listed)",
            "{transcript}": _transcript_text(turns[-3:]),
            "{question}": last.question,
            "{answer}": _clip(last.answer, MAX_ANSWER_CHARS),
            "{focus_area}": last.focus_area or "(none)",
            "{planned_next}": remaining[0].question,
            "{planned_left}": str(len(remaining)),
            "{turns_left}": str(interview.max_turns - len(turns)),
            "{topics}": _bullets(
                [
                    f"{topic.topic} — {topic.why}" if topic.why else topic.topic
                    for topic in interview.plan.topics_to_watch
                ]
            ),
            "{topics_mentioned}": _bullets([topic.topic for topic in fresh]),
        },
    )
    try:
        payload = get_llm().generate_json(
            prompt,
            system=(
                "You are an experienced technical interviewer mid-conversation. "
                "Reply with JSON only."
            ),
            temperature=0.5,
            max_output_tokens=700,
        )
    except Exception as exc:
        raise InterviewError("The interviewer could not decide the next question.") from exc
    return _normalize_decision(payload, remaining, fresh)


def _normalize_decision(
    payload: Any,
    remaining: tuple[PlannedQuestion, ...],
    fresh: tuple[WatchTopic, ...],
) -> Decision:
    """Take the model's judgement, keep the plan's authority.

    Two rules the model does not get a vote on. It cannot **wrap up** while the
    plan has questions left — ending early is the cheapest way to look decisive and
    the most expensive thing to get wrong. And when it says ``next_planned`` the
    question asked is the plan's own wording, not a paraphrase: that text was
    written against this resume, and a rewrite would quietly untailor it.
    """
    if not isinstance(payload, dict):
        return _planned(remaining, reason="Unreadable decision.")
    action = _one_of(payload.get("action"), _DECISIONS, "next_planned")
    topics = _str_list(payload.get("detected_topics"), limit=5) or tuple(
        topic.topic for topic in fresh
    )
    if action == "wrap_up" and remaining:
        action = "next_planned"
    if action in ("probe", "steer"):
        question = _clip(payload.get("question"), MAX_QUESTION_CHARS)
        if len(question) >= 10:
            focus = _clip(payload.get("focus_area"), 60)
            if action == "steer" and not focus:
                focus = topics[0] if topics else ""
            return Decision(
                action,
                question=question,
                focus_area=focus,
                source="answer",
                rationale=_clip(payload.get("rationale") or payload.get("why"), 240),
                topics=topics,
                decided_by="model",
            )
        action = "next_planned"  # a probe with no question is a planned question
    if action == "wrap_up":
        return Decision(
            "wrap_up",
            rationale=_clip(payload.get("rationale"), 240) or "The interviewer wrapped up.",
            topics=topics,
            decided_by="model",
        )
    return replace(
        _planned(remaining, reason="Next planned question."),
        topics=topics,
        decided_by="model",
    )


# --------------------------------------------------------------------------- #
# Answers
# --------------------------------------------------------------------------- #


def submit_answer(
    interview_id: int,
    text: str,
    *,
    user_id: int,
    transcript_source: str = "",
    seconds: float | None = None,
    audio_path: str = "",
) -> AnswerOutcome:
    """Offer an answer to the question on screen. Guardrails run first.

    A rejected answer does **not** burn a turn: the question stays open and the
    candidate is told what to fix. That has to be bounded, though — a microphone
    that keeps producing four words would otherwise trap someone in their own
    interview — so the last attempt is accepted, flagged ``forced_accept`` for the
    recruiter, and skipped over when the next question is chosen.
    """
    interview = require(interview_id, user_id)
    if not interview.is_live:
        raise InterviewError("This interview is not open for answers.")
    turn = current_turn(interview_id)
    if turn is None:
        raise InterviewError("There is no question waiting for an answer.")
    if interview.out_of_time:
        finish(interview_id, reason="time_limit")
        raise InterviewError(
            "Your interview time ran out. It has been submitted for review."
        )

    answer = _clip(text, MAX_ANSWER_CHARS)
    hints = [turn.focus_area, *(
        term for topic in interview.plan.topics_to_watch for term in topic.match_terms
    )]
    verdict = guardrails.check_answer(
        answer,
        question=turn.question,
        topic_hints=tuple(hint for hint in hints if hint),
    )
    attempts = turn.rejected_attempts + 1
    forced = not verdict.safe and attempts >= MAX_REJECTS_PER_TURN

    if not verdict.safe and not forced:
        db.execute(
            "UPDATE interview_turns SET rejected_attempts = ?, guardrail = ? WHERE id = ?",
            (attempts, db.dumps(verdict.as_dict()), turn.id),
        )
        return AnswerOutcome(
            accepted=False,
            message=verdict.reason,
            turn=current_turn(interview_id),
            flags=verdict.flags,
            attempts_left=MAX_REJECTS_PER_TURN - attempts,
        )

    stored = verdict.text or answer
    guardrail = verdict.as_dict()
    if forced:
        guardrail["flags"] = [*verdict.flags, "forced_accept"]
        guardrail["reason"] = verdict.reason
    topics = [
        topic.topic for topic in interview.plan.topics_named_in(stored)
    ]
    db.execute(
        """
        UPDATE interview_turns
           SET answer = ?, answer_words = ?, answered_at = ?, answer_seconds = ?,
               transcript_source = ?, answer_audio_path = ?, guardrail = ?,
               detected_topics = ?, rejected_attempts = ?
         WHERE id = ?
        """,
        (
            stored,
            len(_words(stored)),
            db.utc_now_iso(),
            seconds,
            transcript_source,
            audio_path,
            db.dumps(guardrail),
            db.dumps(topics),
            attempts - 1 if verdict.safe else attempts,
            turn.id,
        ),
    )
    saved = db.query_one("SELECT * FROM interview_turns WHERE id = ?", (turn.id,))
    return AnswerOutcome(
        accepted=True,
        message="" if verdict.safe else verdict.reason,
        turn=_to_turn(saved) if saved else None,
        flags=tuple(guardrail["flags"]),
        forced=forced,
    )


# --------------------------------------------------------------------------- #
# Finishing and scoring
# --------------------------------------------------------------------------- #


def finish(interview_id: int, *, reason: str = "", score_now: bool = True) -> Interview:
    """Close the interview, then try to score it.

    Scoring is a separate function on purpose: the interview is over the moment the
    row says so, and a provider outage at that instant must not leave the candidate
    unsure whether their answers counted. An unscored transcript can be scored
    later, by a retry or by the recruiter opening it.
    """
    interview = get(interview_id)
    if interview is None:
        raise InterviewError("That interview does not exist.")
    if not interview.is_completed:
        _touch(interview.id, status="completed", completed_at=db.utc_now_iso())
        db.audit(
            interview.user_id,
            "interview.completed",
            interview_id=interview.id,
            reason=reason or "finished",
            answers=len(answered_turns(interview.id)),
        )
    if score_now and not interview.scored:
        try:
            return score(interview.id)
        except InterviewError as error:
            log.warning("scoring deferred for interview %s: %s", interview.id, error)
    return get(interview.id) or interview


def score(interview_id: int, *, force: bool = False) -> Interview:
    """Grade the transcript against the five interview criteria.

    The total is recomputed from the clamped criteria rather than taken from the
    model, for the same reason the resume evaluation does it: the number that ends
    up in front of a recruiter has to be the sum of the judgements they can read.
    """
    interview = get(interview_id)
    if interview is None:
        raise InterviewError("That interview does not exist.")
    if interview.scored and not force:
        return interview
    turns = answered_turns(interview_id)
    if not turns:
        # Nothing was said. Scoring zero is honest; inventing criteria is not.
        return _store_score(
            interview,
            {
                "criteria": {},
                "total_score": 0,
                "strengths": [],
                "weaknesses": [],
                "summary": "No answers were recorded for this interview.",
                "flags": ["no_answers"],
            },
        )

    application, role, company = _context(interview)
    prompt = _fill(
        "interview_score.txt",
        {
            "{role_title}": role.title,
            "{company_name}": company.name,
            "{job_description}": role.job_description or "(not provided)",
            "{requirements}": role.requirements_line or "(none listed)",
            "{culture}": company.culture_line or "(not documented)",
            "{candidate_name}": application.candidate_name or "the candidate",
            "{transcript}": _transcript_text(turns, numbered=True),
            "{flagged}": _bullets(
                [
                    f"Q{turn.seq}: {', '.join(turn.flags)}"
                    for turn in turns
                    if turn.flags
                ]
            ),
        },
    )
    try:
        payload = get_llm().generate_json(
            prompt,
            system=(
                "You are a fair, evidence-driven interview panellist. Reply with JSON only."
            ),
            temperature=0.2,
            max_output_tokens=1200,
        )
    except Exception as exc:
        raise InterviewError(
            "Scoring could not finish — the transcript is saved and can be scored again."
        ) from exc
    return _store_score(interview, _normalize_evaluation(payload))


def _store_score(interview: Interview, evaluation: dict[str, Any]) -> Interview:
    _touch(
        interview.id,
        evaluation=db.dumps(evaluation),
        total_score=int(evaluation.get("total_score") or 0),
        max_total_score=MAX_TOTAL_SCORE,
    )
    db.audit(
        interview.user_id,
        "interview.scored",
        interview_id=interview.id,
        total_score=evaluation.get("total_score"),
        max_total_score=MAX_TOTAL_SCORE,
    )
    return get(interview.id) or interview


def _transcript_text(turns: list[Turn], *, numbered: bool = False) -> str:
    """The conversation as the model should read it, tags and all.

    The action and source travel with each question so the grader knows a ``jd_gap``
    question was asked *because* the resume was thin there, and does not penalise the
    candidate twice for the same gap.
    """
    lines: list[str] = []
    for turn in turns:
        label = f"Q{turn.seq}" if numbered else "Q"
        tag = " / ".join(part for part in (turn.action, turn.source, turn.focus_area) if part)
        lines.append(f"{label} [{tag}]: {turn.question}")
        lines.append(f"A: {turn.answer or '(no answer)'}")
    return "\n".join(lines) or "(no conversation)"


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _normalize_evaluation(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("criteria")
    raw = raw if isinstance(raw, dict) else {}
    criteria = {
        name: _clamp(raw.get(name), 0, MAX_CRITERION) for name in CRITERIA
    }
    return {
        "criteria": criteria,
        "total_score": sum(criteria.values()),
        "max_total_score": MAX_TOTAL_SCORE,
        "strengths": list(_str_list(payload.get("strengths"), limit=5)),
        "weaknesses": list(_str_list(payload.get("weaknesses"), limit=5)),
        "summary": _clip(payload.get("summary") or payload.get("reason"), 800),
        "recommendation": _one_of(
            payload.get("recommendation"),
            ("advance", "hold", "decline"),
            "hold",
        ),
    }


# --------------------------------------------------------------------------- #
# Housekeeping
# --------------------------------------------------------------------------- #


def expire_stale() -> int:
    """Mark interviews nobody started before the window shut. No model calls.

    Cheap enough to run on a page load: one indexed UPDATE, and the status is what
    both the candidate's home page and the recruiter's list read.
    """
    now = db.utc_now_iso()
    stale = [
        row["id"]
        for row in db.query(
            "SELECT id FROM interviews WHERE status = 'pending' AND closes_at < ?",
            (now,),
        )
    ]
    if stale:
        placeholders = ",".join("?" * len(stale))
        db.execute(
            f"UPDATE interviews SET status = 'expired', updated_at = ? "
            f"WHERE id IN ({placeholders})",
            (now, *stale),
        )
    return len(stale)


def close_abandoned() -> list[int]:
    """Finish and score interviews whose timer ran out with the tab closed.

    This one **does** make a model call per interview, so it belongs in a
    maintenance run — ``scripts/healthcheck.py`` or a recruiter opening the list —
    and not in a page render.
    """
    closed: list[int] = []
    for row in db.query(f"{_SELECT} WHERE i.status = 'in_progress'"):
        interview = _to_interview(row)
        if interview.out_of_time:
            finish(interview.id, reason="abandoned")
            closed.append(interview.id)
    return closed

