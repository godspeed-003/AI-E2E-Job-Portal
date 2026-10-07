"""The sandbox *AI answer* button, at the service layer.

Two things are worth testing here and they pull in opposite directions.

The first is that it works offline. The whole point of the button is a demo that
does not stall in front of a panel, so it has to produce a usable answer when the
provider is the ``fake`` stub, unreachable, or throttled — and "usable" means it
clears the same two gates a real answer clears: the ``min_answer_words`` floor and
the guardrail's word-overlap check against the question. A draft that gets
rejected as off-topic would make the button look broken in exactly the situation
it exists for.

The second is that it is refused everywhere else. A convenience that writes a
candidate's answer for them must not be reachable on a real interview by setting a
query parameter, so the refusal lives in the service and not only in the page that
hides the button.
"""

from __future__ import annotations

import pytest

from services import interview_service as interviews
from services import guardrail_service as guardrails
from services.interview_service import InterviewError

from test_interview import _shortlisted
from test_sandbox import _sandbox_shortlist

pytestmark = pytest.mark.usefixtures("fake_llm")

QUESTION = "Tell me about a time you debugged a production performance problem."


def _sandbox_interview() -> interviews.Interview:
    """A sandbox interview — the only kind the button appears on.

    Reuses ``test_sandbox._sandbox_shortlist``, which is what the skip-ahead
    button does minus Streamlit, rather than hand-rolling a second path to the
    same state.
    """
    interview = _sandbox_shortlist()
    assert interview.is_sandbox, "fixture did not produce a sandbox interview"
    return interview


# --------------------------------------------------------------------------- #
# It has to work
# --------------------------------------------------------------------------- #


def test_a_draft_clears_the_word_floor() -> None:
    interview = _sandbox_interview()
    draft = interviews.draft_answer(interview.id, QUESTION)
    floor = interviews.settings.interview.min_answer_words
    assert len(interviews._words(draft)) >= floor


def test_a_draft_is_short() -> None:
    """Short is the requirement, not a side effect — assert the ceiling holds."""
    interview = _sandbox_interview()
    draft = interviews.draft_answer(interview.id, QUESTION, max_words=45)
    assert len(interviews._words(draft)) <= 45


def test_a_draft_survives_the_guardrail() -> None:
    """The gate that matters: a draft must be submittable as a real answer.

    ``low_overlap`` is the failure this guards against. A generic answer with no
    words in common with the question is flagged off-topic, so the offline
    template is built out of the question's own content words.
    """
    interview = _sandbox_interview()
    draft = interviews.draft_answer(interview.id, QUESTION)
    verdict = guardrails.check_answer(draft, question=QUESTION, use_model=False)
    assert verdict.safe, f"guardrail rejected the draft: {verdict.flags}"
    assert "low_overlap" not in verdict.flags


def test_a_draft_can_actually_be_submitted() -> None:
    """End to end: draft, submit, and the turn is accepted and recorded."""
    interview = _sandbox_interview()
    started = interviews.start(
        interview.id, user_id=interview.user_id, consent=True
    )
    turn = interviews.ask_next(started.id)
    assert turn is not None

    draft = interviews.draft_answer(started.id, turn.question)
    outcome = interviews.submit_answer(
        started.id, draft, user_id=started.user_id, transcript_source="typed"
    )
    assert outcome.accepted, f"draft was refused: {outcome.message} {outcome.flags}"

    answered = interviews.answered_turns(started.id)
    assert answered and answered[-1].answer == draft


def test_the_offline_template_reuses_the_questions_words() -> None:
    """Why the overlap check passes: by construction, not by luck."""
    floor = interviews.settings.interview.min_answer_words
    draft = interviews._offline_draft(QUESTION, floor=floor)
    shared = interviews._content_words(draft) & interviews._content_words(QUESTION)
    assert shared, "the template shares no content words with the question"


def test_a_question_with_no_content_words_still_drafts() -> None:
    """Degenerate input must not produce an empty or malformed answer."""
    floor = interviews.settings.interview.min_answer_words
    draft = interviews._offline_draft("Why?", floor=floor)
    assert len(interviews._words(draft)) >= floor


# --------------------------------------------------------------------------- #
# It has to be refused
# --------------------------------------------------------------------------- #


def test_a_real_interview_refuses_a_draft() -> None:
    """The enforcement that makes hiding the button mean something."""
    _user, _application, interview = _shortlisted()
    assert not interview.is_sandbox

    with pytest.raises(InterviewError, match="sandbox"):
        interviews.draft_answer(interview.id, QUESTION)


def test_a_missing_interview_refuses_a_draft() -> None:
    with pytest.raises(InterviewError):
        interviews.draft_answer(10_000_000, QUESTION)


def test_an_empty_question_refuses_a_draft() -> None:
    interview = _sandbox_interview()
    with pytest.raises(InterviewError):
        interviews.draft_answer(interview.id, "   ")


# --------------------------------------------------------------------------- #
# Tidying the model's output
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, banned",
    [
        ("Sure! I led the migration and it worked.", "Sure"),
        ("Here's my answer: I led the migration.", "Here's"),
        ("- I led the migration\n- it worked", "-"),
        ("**I led** the `migration`.", "*"),
        ("Certainly, I led the migration.", "Certainly"),
    ],
)
def test_scaffolding_is_stripped(raw: str, banned: str) -> None:
    """Models ignore "no preamble" and "no markdown" with great consistency."""
    cleaned = interviews._tidy_draft(raw)
    assert banned not in cleaned
    assert cleaned
    assert "\n" not in cleaned


def test_trimming_prefers_a_sentence_boundary() -> None:
    text = "One two three four five. Six seven eight nine ten eleven twelve."
    trimmed = interviews._trim_words(text, 8)
    assert trimmed.endswith(".")
    assert len(interviews._words(trimmed)) <= 8


def test_trimming_a_long_first_sentence_does_not_return_a_fragment() -> None:
    """A sentence end in the first third must not win, or the answer vanishes."""
    text = "Yes. " + " ".join(["word"] * 40)
    trimmed = interviews._trim_words(text, 20)
    assert len(interviews._words(trimmed)) > 1
