"""Free-tier LLM router with automatic provider failover.

Priority: groq → gemini → cohere (free tiers only; no paid providers, ever).
Before each call the router checks today's ``llm_usage`` row in Supabase and
skips providers that already hit quota. On 429/quota/auth failures the
provider is flagged via ``mark_quota_hit`` so other workers skip it too.

When Supabase is unreachable, the router still tries all providers (usage
tracking just becomes best-effort logging).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Free-tier daily request budgets (conservative vs. published limits).
PROVIDERS: list[dict] = [
    {"name": "groq", "model": "llama-3.3-70b-versatile", "quota": 14_000},
    {"name": "gemini", "model": "gemini-2.5-flash", "quota": 1_500},
    {"name": "cohere", "model": "command-r", "quota": 1_000},
]

_ENV_KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "cohere": "COHERE_API_KEY"}
_TIMEOUT_S = 90


@dataclass
class LLMResponse:
    """Result of a successful LLM generation."""

    text: str
    provider: str
    tokens: int
    latency_ms: int
    model: str = ""


class LLMExhaustedError(Exception):
    """Raised when every configured free-tier provider failed or is exhausted."""


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~1 token per 4 chars) when a provider gives no usage."""
    return max(1, len(text) // 4)


def _is_quota_error(exc: Exception) -> bool:
    """Heuristically detect rate-limit / quota / auth errors across SDKs."""
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return (
        status in (401, 403, 429)
        or any(
            kw in text
            for kw in (
                "rate limit",
                "ratelimit",
                "quota",
                "too many requests",
                "resource exhausted",
                "insufficient",
                "unauthorized",
                "invalid api key",
                "authentication",
            )
        )
    )


class LLMRouter:
    """Call free-tier LLMs with failover and per-day quota tracking.

    Args:
        supabase: Optional :class:`core.supabase_client.SupabaseClient` used
            for usage/quota tracking. If omitted, tracking is skipped but
            generation still works.
    """

    def __init__(self, supabase: Optional[Any] = None) -> None:
        self.supabase = supabase
        self._clients: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Lazy client construction                                             #
    # ------------------------------------------------------------------ #
    def _client(self, provider: str) -> Any:
        """Build (once) the SDK client for a provider, reading its env key lazily."""
        if provider in self._clients:
            return self._clients[provider]
        env_key = _ENV_KEYS[provider]
        api_key = os.environ.get(env_key, "").strip()
        if not api_key:
            raise RuntimeError(f"{env_key} is not set; cannot use provider '{provider}'")
        try:
            if provider == "groq":
                from groq import Groq

                client = Groq(api_key=api_key, timeout=_TIMEOUT_S)
            elif provider == "gemini":
                import google.generativeai as genai

                genai.configure(api_key=api_key)
                client = genai
            elif provider == "cohere":
                import cohere

                client = cohere.Client(api_key, timeout=_TIMEOUT_S)
            else:
                raise ValueError(f"Unknown provider '{provider}'")
        except ImportError as exc:
            raise RuntimeError(
                f"SDK for provider '{provider}' is not installed: {exc}"
            ) from exc
        self._clients[provider] = client
        return client

    # ------------------------------------------------------------------ #
    # Usage tracking helpers                                               #
    # ------------------------------------------------------------------ #
    def _usage_today(self, provider: str) -> Optional[dict]:
        if self.supabase is None:
            return None
        try:
            return self.supabase.get_llm_usage(provider, date.today())
        except Exception as exc:  # noqa: BLE001 - tracking must not break generation
            logger.warning("Could not read LLM usage for %s: %s", provider, exc)
            return None

    def _log_usage(self, provider: str, tokens: int) -> None:
        if self.supabase is None:
            return
        try:
            self.supabase.log_llm_usage(provider, tokens)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not log LLM usage for %s: %s", provider, exc)

    def _mark_quota_hit(self, provider: str) -> None:
        if self.supabase is None:
            return
        try:
            self.supabase.mark_quota_hit(provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not mark quota hit for %s: %s", provider, exc)

    def _provider_available(self, spec: dict) -> bool:
        """True when the provider has no env key problem and quota remains."""
        usage = self._usage_today(spec["name"])
        if usage:
            if usage.get("quota_hit"):
                logger.info("Skipping %s: quota already hit today", spec["name"])
                return False
            if int(usage.get("requests") or 0) >= spec["quota"]:
                logger.info(
                    "Skipping %s: daily quota exhausted (%s/%s requests)",
                    spec["name"],
                    usage.get("requests"),
                    spec["quota"],
                )
                return False
        return os.environ.get(_ENV_KEYS[spec["name"]], "").strip() != ""

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #
    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Generate text, trying providers in priority order.

        Args:
            prompt: The user prompt.
            system: Optional system/developer instruction.
            max_tokens: Max tokens to generate.
            temperature: Sampling temperature.

        Returns:
            The successful :class:`LLMResponse`.

        Raises:
            ValueError: If prompt is empty.
            LLMExhaustedError: If every provider failed or is exhausted.
        """
        if not prompt or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        failures: list[str] = []
        for spec in PROVIDERS:
            name, model = spec["name"], spec["model"]
            if not self._provider_available(spec):
                failures.append(f"{name}: skipped (no key or quota exhausted)")
                continue
            call = {
                "groq": self._call_groq,
                "gemini": self._call_gemini,
                "cohere": self._call_cohere,
            }[name]
            try:
                return call(model, prompt, system, max_tokens, temperature)
            except Exception as exc:  # noqa: BLE001 - per-provider isolation
                if _is_quota_error(exc):
                    self._mark_quota_hit(name)
                    failures.append(f"{name}: quota/rate-limit ({exc})")
                    logger.warning("Provider %s hit quota/rate limit: %s", name, exc)
                else:
                    failures.append(f"{name}: {type(exc).__name__}: {exc}")
                    logger.warning("Provider %s failed: %s", name, exc)
                continue

        raise LLMExhaustedError(
            "All free-tier LLM providers failed or are exhausted today. "
            "Details: " + " | ".join(failures)
        )

    # ------------------------------------------------------------------ #
    # Provider implementations                                             #
    # ------------------------------------------------------------------ #
    def _call_groq(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Call Groq chat completions (OpenAI-compatible)."""
        client = self._client("groq")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        start = time.monotonic()
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        text = completion.choices[0].message.content or ""
        usage = getattr(completion, "usage", None)
        tokens = (
            int(usage.total_tokens) if usage and usage.total_tokens else _estimate_tokens(text)
        )
        self._log_usage("groq", tokens)
        return LLMResponse(text=text, provider="groq", tokens=tokens, latency_ms=latency_ms, model=model)

    def _call_gemini(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Call Google Gemini via google-generativeai."""
        genai = self._client("gemini")
        from google.generativeai.types import GenerationConfig

        model_obj = genai.GenerativeModel(
            model,
            system_instruction=system or None,
            generation_config=GenerationConfig(
                max_output_tokens=max_tokens, temperature=temperature
            ),
        )
        start = time.monotonic()
        response = model_obj.generate_content(prompt)
        latency_ms = int((time.monotonic() - start) * 1000)
        text = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        tokens = (
            int(usage.total_token_count)
            if usage and getattr(usage, "total_token_count", None)
            else _estimate_tokens(text)
        )
        self._log_usage("gemini", tokens)
        return LLMResponse(
            text=text, provider="gemini", tokens=tokens, latency_ms=latency_ms, model=model
        )

    def _call_cohere(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Call Cohere chat endpoint."""
        client = self._client("cohere")
        start = time.monotonic()
        response = client.chat(
            model=model,
            message=prompt,
            preamble=system or None,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        text = response.text or ""
        meta = getattr(response, "meta", None)
        usage = getattr(meta, "tokens", None) if meta else None
        tokens = (
            int(usage.input_tokens + usage.output_tokens)
            if usage and usage.input_tokens is not None
            else _estimate_tokens(text)
        )
        self._log_usage("cohere", tokens)
        return LLMResponse(
            text=text, provider="cohere", tokens=tokens, latency_ms=latency_ms, model=model
        )
