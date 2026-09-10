"""Local Ollama backend — the drop-in replacement for Gemini.

Run ``ollama serve`` plus ``ollama pull llama3.1`` and set
``LLM_PROVIDER=ollama``; nothing else in the portal changes. Ollama's
``format: json`` gives the same guaranteed-JSON behaviour the interview
pipeline relies on.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from llm.base import LLMError, LLMProvider, LLMResult, LLMUnavailable


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float,
        timeout: int,
        retries: int,
    ):
        super().__init__(
            model=model, temperature=temperature, timeout=timeout, retries=retries
        )
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()

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
        options: dict[str, Any] = {"temperature": temperature}
        if max_output_tokens:
            options["num_predict"] = max_output_tokens

        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            body["system"] = system
        if json_mode:
            # Recent Ollama accepts a JSON Schema here and constrains decoding to
            # it; older builds only understand the literal string "json". Passing
            # the schema when we have one is strictly better and degrades safely.
            body["format"] = schema if schema else "json"

        started = time.time()
        try:
            response = self._session.post(
                f"{self.base_url}/api/generate", json=body, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise LLMUnavailable(
                f"cannot reach Ollama at {self.base_url} — is `ollama serve` running? ({exc})"
            ) from exc

        if response.status_code == 404:
            raise LLMError(
                f"Ollama does not have model '{self.model}'. Run: ollama pull {self.model}"
            )
        if response.status_code >= 500:
            raise LLMUnavailable(f"Ollama HTTP {response.status_code}")
        if response.status_code != 200:
            raise LLMError(f"Ollama HTTP {response.status_code}: {response.text[:300]}")

        payload = response.json()
        text = (payload.get("response") or "").strip()
        if not text:
            raise LLMError("Ollama returned an empty response")

        prompt_tokens = payload.get("prompt_eval_count") or 0
        output_tokens = payload.get("eval_count") or 0
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_seconds=time.time() - started,
            tokens=(prompt_tokens + output_tokens) or None,
            raw=payload,
        )

    def health(self) -> dict[str, Any]:
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=10)
            if response.status_code != 200:
                return {
                    "provider": self.name,
                    "model": self.model,
                    "ok": False,
                    "detail": f"HTTP {response.status_code}",
                }
            available = [m.get("name", "") for m in response.json().get("models", [])]
            # Ollama reports tagged names such as "llama3.1:latest".
            present = any(
                name == self.model or name.split(":")[0] == self.model.split(":")[0]
                for name in available
            )
            return {
                "provider": self.name,
                "model": self.model,
                "ok": present,
                "detail": (
                    "model available"
                    if present
                    else f"model missing; pull it with `ollama pull {self.model}`"
                ),
                "models_installed": available[:20],
            }
        except requests.RequestException as exc:
            return {
                "provider": self.name,
                "model": self.model,
                "ok": False,
                "detail": f"unreachable: {exc}",
            }
