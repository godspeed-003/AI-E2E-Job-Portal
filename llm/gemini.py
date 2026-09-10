"""Google Gemini backend, spoken over the plain REST API.

Deliberately no ``google-genai`` SDK dependency: the REST shape mirrors the
Ollama and OpenAI-compatible backends, so all three providers are ~100 lines
each and behave identically. That is what makes "switch to a local model later"
a one-line change instead of a refactor.

Multiple API keys are supported and rotated when one hits its free-tier quota.
"""

from __future__ import annotations

import logging
import time
from itertools import cycle
from typing import Any

import requests

from llm.base import LLMError, LLMProvider, LLMRateLimited, LLMResult, LLMUnavailable

log = logging.getLogger(__name__)

# Gemini rejects unknown JSON-Schema keywords, so schemas are filtered down to
# the subset it documents support for.
_ALLOWED_SCHEMA_KEYS = {
    "type",
    "format",
    "description",
    "nullable",
    "enum",
    "items",
    "properties",
    "required",
    "propertyOrdering",
    "minItems",
    "maxItems",
}


def sanitize_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        cleaned: dict[str, Any] = {}
        for key, value in schema.items():
            if key not in _ALLOWED_SCHEMA_KEYS:
                continue
            cleaned[key] = sanitize_schema(value)
        return cleaned
    if isinstance(schema, list):
        return [sanitize_schema(item) for item in schema]
    return schema


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_keys: tuple[str, ...] | list[str],
        model: str,
        api_base: str,
        temperature: float,
        timeout: int,
        retries: int,
    ):
        super().__init__(
            model=model, temperature=temperature, timeout=timeout, retries=retries
        )
        self.api_keys = [key for key in api_keys if key]
        if not self.api_keys:
            raise LLMError(
                "LLM_PROVIDER=gemini but no GEMINI_API_KEY is set. Add a key to .env "
                "or switch LLM_PROVIDER to ollama / openai_compat."
            )
        self.api_base = api_base.rstrip("/")
        self._key_cycle = cycle(range(len(self.api_keys)))
        self._key_index = next(self._key_cycle)
        self._session = requests.Session()

    # -- key rotation -------------------------------------------------------- #

    @property
    def _key(self) -> str:
        return self.api_keys[self._key_index]

    def _rotate_key(self) -> bool:
        """Move to the next credential. Returns False if there is only one."""
        if len(self.api_keys) < 2:
            return False
        self._key_index = next(self._key_cycle)
        log.info("Rotated to Gemini API key #%d", self._key_index + 1)
        return True

    # -- request ------------------------------------------------------------- #

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
        generation_config: dict[str, Any] = {"temperature": temperature}
        if max_output_tokens:
            generation_config["maxOutputTokens"] = max_output_tokens
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
            if schema:
                generation_config["responseSchema"] = sanitize_schema(schema)

        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{self.api_base}/models/{self.model}:generateContent"
        started = time.time()
        try:
            response = self._session.post(
                url,
                headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMUnavailable(f"cannot reach Gemini: {exc}") from exc

        if response.status_code == 429:
            # Try the next key immediately; the retry loop handles backoff.
            self._rotate_key()
            raise LLMRateLimited("Gemini quota exceeded (HTTP 429)")
        if response.status_code in (500, 502, 503, 504):
            raise LLMUnavailable(f"Gemini HTTP {response.status_code}")
        if response.status_code == 400 and "API key not valid" in response.text:
            raise LLMError("Gemini rejected the API key — check GEMINI_API_KEY in .env")
        if response.status_code != 200:
            raise LLMError(f"Gemini HTTP {response.status_code}: {response.text[:300]}")

        payload = response.json()
        return LLMResult(
            text=self._first_text(payload),
            provider=self.name,
            model=self.model,
            latency_seconds=time.time() - started,
            tokens=payload.get("usageMetadata", {}).get("totalTokenCount"),
            raw=payload,
        )

    @staticmethod
    def _first_text(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            blocked = payload.get("promptFeedback", {}).get("blockReason")
            if blocked:
                raise LLMError(f"Gemini blocked the prompt ({blocked})")
            raise LLMError("Gemini returned no candidates")

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if text:
            return text

        reason = candidate.get("finishReason", "UNKNOWN")
        if reason == "MAX_TOKENS":
            raise LLMError(
                "Gemini hit the output token limit before producing text — "
                "raise max_output_tokens"
            )
        raise LLMError(f"Gemini returned an empty candidate (finishReason={reason})")

    def health(self) -> dict[str, Any]:
        try:
            response = self._session.get(
                f"{self.api_base}/models/{self.model}",
                headers={"x-goog-api-key": self._key},
                timeout=15,
            )
            return {
                "provider": self.name,
                "model": self.model,
                "ok": response.status_code == 200,
                "detail": (
                    "reachable"
                    if response.status_code == 200
                    else f"HTTP {response.status_code}"
                ),
                "keys_configured": len(self.api_keys),
            }
        except requests.RequestException as exc:
            return {
                "provider": self.name,
                "model": self.model,
                "ok": False,
                "detail": str(exc),
                "keys_configured": len(self.api_keys),
            }
