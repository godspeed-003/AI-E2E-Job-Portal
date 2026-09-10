"""Provider-agnostic LLM interface.

Everything AI-shaped in the portal (resume scoring, interview planning, adaptive
follow-ups, guardrails, transcript grading) goes through :class:`LLMProvider`.
Swapping Gemini for a local llama.cpp or Ollama model is therefore a change to
``LLM_PROVIDER`` in ``.env`` and nothing else.

All three real backends speak HTTP+JSON, so they share the retry, JSON-repair
and error-classification logic that lives here.
"""

from __future__ import annotations

import abc
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a usable answer."""


class LLMRateLimited(LLMError):
    """Quota exhausted for the current credential."""


class LLMUnavailable(LLMError):
    """Transient server-side failure (overloaded, 5xx, connection refused)."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_seconds: float = 0.0
    tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# JSON handling
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def extract_json(text: str) -> Any:
    """Pull a JSON value out of model output.

    Local models in particular like to wrap JSON in prose or fences, emit
    trailing commas, or use smart quotes. Rather than failing the interview over
    punctuation, try progressively more forgiving repairs.
    """
    if text is None:
        raise LLMError("empty model response")

    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    # Largest balanced object/array in the response.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        for attempt in (candidate, _repair(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue

    raise LLMError(f"model did not return JSON: {text[:300]!r}")


def _repair(text: str) -> str:
    repaired = _TRAILING_COMMA_RE.sub(r"\1", text)
    return (
        repaired.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )


# --------------------------------------------------------------------------- #
# Provider base
# --------------------------------------------------------------------------- #


class LLMProvider(abc.ABC):
    """Minimal surface every backend must implement."""

    name: str = "base"

    def __init__(self, *, model: str, temperature: float, timeout: int, retries: int):
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.retries = max(0, retries)

    # -- to implement in subclasses ----------------------------------------- #

    @abc.abstractmethod
    def _generate_once(
        self,
        prompt: str,
        *,
        system: str | None,
        json_mode: bool,
        schema: dict[str, Any] | None,
        temperature: float,
        max_output_tokens: int | None,
    ) -> LLMResult: ...

    @abc.abstractmethod
    def health(self) -> dict[str, Any]:
        """Cheap reachability probe for the admin panel."""

    # -- shared behaviour ---------------------------------------------------- #

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        """Generate text, retrying transient failures with jittered backoff."""
        effective_temp = self.temperature if temperature is None else temperature
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                return self._generate_once(
                    prompt,
                    system=system,
                    json_mode=json_mode,
                    schema=schema,
                    temperature=effective_temp,
                    max_output_tokens=max_output_tokens,
                )
            except (LLMRateLimited, LLMUnavailable) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                delay = (1.6**attempt) + random.uniform(0, 0.4)
                log.warning(
                    "%s attempt %d/%d failed (%s); retrying in %.1fs",
                    self.name,
                    attempt + 1,
                    self.retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
            except LLMError as exc:
                raise exc

        raise LLMError(f"{self.name} failed after {self.retries + 1} attempts: {last_error}")

    def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any:
        """Generate and parse JSON, with one corrective re-ask on malformed output."""
        result = self.generate(
            prompt,
            system=system,
            json_mode=True,
            schema=schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        try:
            return extract_json(result.text)
        except LLMError:
            log.warning("%s returned unparseable JSON; re-asking once", self.name)

        retry = self.generate(
            f"{prompt}\n\n"
            "Your previous reply was not valid JSON. Reply with the JSON object "
            "only — no prose, no markdown fences, no trailing commas.",
            system=system,
            json_mode=True,
            schema=schema,
            temperature=0.0,
            max_output_tokens=max_output_tokens,
        )
        return extract_json(retry.text)

    def describe(self) -> str:
        return f"{self.name}:{self.model}"
