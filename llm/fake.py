"""Deterministic stub provider.

Lets the whole pipeline — screening, interview planning, adaptive steering,
guardrails, grading — be tested without a network call or an API key. Tests can
either rely on the canned heuristics below or push exact scripted replies.
"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from llm.base import LLMProvider, LLMResult

# Words that carry no topic. Short, because this only has to be good enough to
# pick a noun out of an interview question for the draft-answer stub.
_FILLER = frozenset(
    """
    a an and about are as at be been can could describe did do does experience
    for from give had has have how in into is it its me my of on or tell than
    that the their them then there these they this time to told took up us use
    used was we were what when where which who why will with work would you
    your
    """.split()
)


def _topic_from_question(prompt: str) -> str:
    """Pull a topic phrase out of the question block of a filled template.

    The templates wrap the question in ``<<<QUESTION ... QUESTION>>>``, so the
    stub can answer the question that was actually asked rather than returning
    one fixed sentence. Falls back to a neutral phrase when the markers are
    missing, because a stub that raises is worse than a stub that is bland.
    """
    block = re.search(r"<<<QUESTION(.*?)QUESTION>>>", prompt, re.DOTALL)
    question = (block.group(1) if block else prompt).lower()
    words = [w for w in re.findall(r"[a-z][a-z-]{3,}", question) if w not in _FILLER]
    if not words:
        return "this"
    # Longest two words, in the order they appeared: long words in a question
    # are the domain nouns, and keeping source order reads less like a bag.
    # The secondary sort on the word itself keeps ties deterministic — without
    # it, two words of equal length made the stub's output vary between runs,
    # which is the one thing a deterministic provider must never do.
    picked = set(sorted(set(words), key=lambda w: (-len(w), w))[:2])
    ordered = [w for w in dict.fromkeys(words) if w in picked]
    return " and ".join(ordered)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(
        self,
        *,
        model: str = "fake-1",
        temperature: float = 0.0,
        timeout: int = 5,
        retries: int = 0,
        scripted: list[str] | None = None,
    ):
        super().__init__(
            model=model, temperature=temperature, timeout=timeout, retries=retries
        )
        self.scripted: deque[str] = deque(scripted or [])
        self.calls: list[dict[str, Any]] = []

    def push(self, *responses: str | dict[str, Any]) -> None:
        """Queue exact replies, consumed in order by subsequent generate calls."""
        for response in responses:
            self.scripted.append(
                response if isinstance(response, str) else json.dumps(response)
            )

    def _generate_once(
        self,
        prompt: str,
        *,
        system: str | None,
        json_mode: bool,
        schema: dict[str, Any] | None,
        temperature: float,
        max_output_tokens: int | None,
    ) -> LLMResult:
        self.calls.append(
            {"prompt": prompt, "system": system, "json_mode": json_mode, "schema": schema}
        )
        text = self.scripted.popleft() if self.scripted else self._canned(prompt)
        return LLMResult(
            text=text, provider=self.name, model=self.model, latency_seconds=0.0
        )

    # The markers below match the headings used by the real prompt templates.
    def _canned(self, prompt: str) -> str:
        lowered = prompt.lower()

        if "interview_plan" in lowered or "question plan" in lowered:
            return json.dumps(
                {
                    "opening": "Thanks for joining. Let's talk through your background.",
                    "questions": [
                        {
                            "question": f"Tell me about your experience with topic {i}.",
                            "focus_area": f"area-{i}",
                            "source": "resume" if i % 2 else "jd_gap",
                            "rationale": "Covers a requirement the resume left thin.",
                            "follow_up_hints": ["Ask for metrics."],
                        }
                        for i in range(1, 7)
                    ],
                    "topics_to_watch": [
                        {
                            "topic": "payment pipeline",
                            "why_relevant": "The role owns billing services.",
                            "probe": "Ask how they handled idempotency.",
                        }
                    ],
                }
            )

        if "decide the next question" in lowered or "next_turn" in lowered:
            return json.dumps(
                {
                    "action": "next_planned",
                    "question": "What was the hardest constraint you had to design around?",
                    "focus_area": "system-design",
                    "source": "jd_gap",
                    "rationale": "Moving to the next planned area.",
                    "detected_topics": [],
                    "answer_quality": {"specificity": 3, "relevance": 4},
                }
            )

        if "guardrail" in lowered or "prompt injection" in lowered:
            return json.dumps({"safe": True, "reason": "Relevant, on-topic answer."})

        if "interview transcript" in lowered or "interview_scoring" in lowered:
            return json.dumps(
                {
                    "criteria": {
                        "technical_depth": 4,
                        "problem_solving": 4,
                        "communication": 3,
                        "culture_fit": 4,
                        "practical_impact": 3,
                    },
                    "total_score": 18,
                    "strengths": ["Concrete examples", "Clear ownership"],
                    "weaknesses": ["Light on metrics"],
                    "summary": "Solid hands-on candidate; quantify impact more.",
                }
            )

        if "interview_draft_answer" in lowered:
            # Prose, not JSON — this is the one call site that wants plain text,
            # and it echoes the question's own wording so the result clears the
            # guardrail's overlap check the way a real answer would. Falling
            # through to the resume-evaluation default instead would hand the
            # sandbox's "AI answer" button a JSON blob to paste into the box.
            topic = _topic_from_question(prompt)
            return (
                f"In my last role {topic} was something I owned directly. "
                "I reproduced the problem first, measured it before changing "
                "anything, and shipped the fix behind a test so it would stay "
                "fixed."
            )

        # Default: resume evaluation.
        return json.dumps(
            {
                "candidate_name": "Test Candidate",
                "criteria": {
                    "skill_match": 4,
                    "experience": 3,
                    "projects": 4,
                    "communication": 4,
                    "culture_fit": 3,
                },
                "total_score": 18,
                "alignment_score": 0.72,
                "strengths": ["Relevant stack", "Shipped production work"],
                "weaknesses": ["No cloud exposure", "Limited scale"],
                "reason": "Good technical overlap with a gap in cloud tooling.",
            }
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "ok": True,
            "detail": "in-process stub; no network used",
        }
