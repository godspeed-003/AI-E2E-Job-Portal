"""OpenAI-compatible ``/v1/chat/completions`` backend.

Covers the majority of self-hosted inference servers — llama.cpp's
``llama-server``, vLLM, LM Studio, Text Generation WebUI, Ollama's compatibility
endpoint, LocalAI. Point ``OPENAI_COMPAT_BASE_URL`` at any of them and the
portal keeps working with no cloud calls at all.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from llm.base import LLMError, LLMProvider, LLMRateLimited, LLMResult, LLMUnavailable


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float,
        timeout: int,
        retries: int,
    ):
        super().__init__(
            model=model, temperature=temperature, timeout=timeout, retries=retries
        )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-needed"
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
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_output_tokens:
            body["max_tokens"] = max_output_tokens
        if json_mode:
            # Structured Outputs where the server supports it, plain JSON mode
            # otherwise. Servers that understand neither still usually obey the
            # prompt, and base.extract_json cleans up the difference.
            if schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "strict": False,
                        "schema": schema,
                    },
                }
            else:
                body["response_format"] = {"type": "json_object"}

        started = time.time()
        try:
            response = self._session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMUnavailable(
                f"cannot reach OpenAI-compatible server at {self.base_url} ({exc})"
            ) from exc

        if response.status_code == 429:
            raise LLMRateLimited("upstream rate limited (HTTP 429)")
        if response.status_code >= 500:
            raise LLMUnavailable(f"upstream HTTP {response.status_code}")
        if response.status_code == 400 and "response_format" in response.text:
            # The server does not support structured outputs — retry plainly so a
            # basic llama.cpp build still works.
            body.pop("response_format", None)
            response = self._session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
            )
        if response.status_code != 200:
            raise LLMError(f"upstream HTTP {response.status_code}: {response.text[:300]}")

        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError("upstream returned no choices")
        text = (choices[0].get("message", {}).get("content") or "").strip()
        if not text:
            raise LLMError("upstream returned an empty message")

        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_seconds=time.time() - started,
            tokens=(payload.get("usage") or {}).get("total_tokens"),
            raw=payload,
        )

    def health(self) -> dict[str, Any]:
        try:
            response = self._session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            ok = response.status_code == 200
            detail = "reachable" if ok else f"HTTP {response.status_code}"
            models: list[str] = []
            if ok:
                models = [m.get("id", "") for m in response.json().get("data", [])]
            return {
                "provider": self.name,
                "model": self.model,
                "ok": ok,
                "detail": detail,
                "models_installed": models[:20],
            }
        except requests.RequestException as exc:
            return {
                "provider": self.name,
                "model": self.model,
                "ok": False,
                "detail": f"unreachable: {exc}",
            }
