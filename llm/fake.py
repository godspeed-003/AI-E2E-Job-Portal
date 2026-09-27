"""Deterministic stub provider.

Lets the whole pipeline — screening, interview planning, adaptive steering,
guardrails, grading — be tested without a network call or an API key. Tests can
either rely on the canned heuristics below or push exact scripted replies.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from llm.base import LLMProvider, LLMResult


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
