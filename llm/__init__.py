"""LLM provider factory.

``get_llm()`` is the single entry point used by every service. It caches one
provider per process and can be overridden in tests via :func:`set_provider`.
"""

from __future__ import annotations

import logging
import threading

from core.config import settings
from llm.base import (
    LLMError,
    LLMProvider,
    LLMRateLimited,
    LLMResult,
    LLMUnavailable,
    extract_json,
)

log = logging.getLogger(__name__)

_lock = threading.Lock()
_provider: LLMProvider | None = None
_override: LLMProvider | None = None

PROVIDERS = ("gemini", "ollama", "openai_compat", "fake")


def build_provider(name: str | None = None) -> LLMProvider:
    """Construct a provider by name without touching the process-wide cache."""
    chosen = (name or settings.llm.provider or "gemini").lower()
    conf = settings.llm

    if chosen == "gemini":
        from llm.gemini import GeminiProvider

        return GeminiProvider(
            api_keys=conf.gemini_api_keys,
            model=conf.gemini_model,
            api_base=conf.gemini_api_base,
            temperature=conf.temperature,
            timeout=conf.timeout_seconds,
            retries=conf.max_retries,
        )

    if chosen == "ollama":
        from llm.ollama import OllamaProvider

        return OllamaProvider(
            base_url=conf.ollama_base_url,
            model=conf.ollama_model,
            temperature=conf.temperature,
            timeout=conf.timeout_seconds,
            retries=conf.max_retries,
        )

    if chosen in ("openai_compat", "openai", "llamacpp", "vllm", "lmstudio"):
        from llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url=conf.openai_compat_base_url,
            model=conf.openai_compat_model,
            api_key=conf.openai_compat_api_key,
            temperature=conf.temperature,
            timeout=conf.timeout_seconds,
            retries=conf.max_retries,
        )

    if chosen == "fake":
        from llm.fake import FakeProvider

        return FakeProvider(temperature=conf.temperature)

    raise LLMError(
        f"unknown LLM_PROVIDER={chosen!r}; expected one of {', '.join(PROVIDERS)}"
    )


def get_llm() -> LLMProvider:
    global _provider
    if _override is not None:
        return _override
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = build_provider()
                log.info("LLM backend: %s", _provider.describe())
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Install a provider for the rest of the process (tests, admin overrides)."""
    global _override
    _override = provider


def reset() -> None:
    global _provider, _override
    with _lock:
        _provider = None
        _override = None


__all__ = [
    "LLMError",
    "LLMProvider",
    "LLMRateLimited",
    "LLMResult",
    "LLMUnavailable",
    "PROVIDERS",
    "build_provider",
    "extract_json",
    "get_llm",
    "reset",
    "set_provider",
]
