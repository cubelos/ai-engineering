"""LiteLLM-backed wrapper that adds provider fallback, exact-match cache, cost tracking,
and structured logging to every LLM call in the estimator.

Design notes
------------
- The wrapper exposes two primitives:
  - ``complete()``: legacy free-text answer (kept for tests that depend on it).
  - ``complete_structured()``: returns a validated Pydantic model via Instructor,
    re-prompting on validator errors up to ``max_retries`` times.
- The Router is configured with two deployments under the same ``model_name``
  ("estimator") so LiteLLM can switch from primary to fallback transparently.
  When the caller overrides the model per-request we bypass the Router and call
  ``litellm.completion`` directly — that path has no fallback by design.
"""

from __future__ import annotations

import re
import time
from typing import Any, TypeVar

import instructor
import litellm
import structlog
from litellm import Router
from pydantic import BaseModel

from app.services.cache import EstimationCache

log = structlog.get_logger()


# Cost per 1M tokens (USD). Update as pricing changes.
MODEL_COSTS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
}


T = TypeVar("T", bound=BaseModel)


def _normalise_model_name(model: str) -> str:
    """Strip provider prefixes like ``anthropic/`` that LiteLLM may emit."""
    return model.split("/", 1)[1] if "/" in model else model


def _cost_rates_for_model(model: str) -> dict[str, float]:
    """Resolve pricing table entry for provider-specific model ids.

    OpenAI often returns dated ids (``gpt-4o-mini-2024-07-18``) that are not
    literal keys in ``MODEL_COSTS``. We fall back to prefix / family matching
    before defaulting to zero — an unknown model must not silently look free.
    """
    candidates = [_normalise_model_name(model), model]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate in MODEL_COSTS:
            return MODEL_COSTS[candidate]
        # OpenAI dated snapshot suffix, e.g. gpt-4o-mini-2024-07-18
        stripped = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", candidate)
        if stripped in MODEL_COSTS:
            return MODEL_COSTS[stripped]
    # Longest-prefix wins: gpt-4o-mini-2024-07-18 → gpt-4o-mini
    normalised = _normalise_model_name(model)
    for key in sorted(MODEL_COSTS, key=len, reverse=True):
        if normalised.startswith(key):
            return MODEL_COSTS[key]
    log.warning("model_cost_unknown", model=model)
    return {"input": 0.0, "output": 0.0}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    costs = _cost_rates_for_model(model)
    return round((tokens_in * costs["input"] + tokens_out * costs["output"]) / 1_000_000, 6)


def _provider_from_model(model: str) -> str:
    name = _normalise_model_name(model).lower()
    if name.startswith("claude"):
        return "anthropic"
    if name.startswith("gpt") or name.startswith("o1") or name.startswith("o3"):
        return "openai"
    return "unknown"


def _usage_from_response(response: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a LiteLLM/OpenAI response."""
    if response is None:
        return 0, 0
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        return int(input_tokens), int(output_tokens)
    input_tokens = getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0
    output_tokens = (
        getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0
    )
    return int(input_tokens), int(output_tokens)


def _raw_response_from_instructor_result(result: Any) -> Any:
    """Best-effort access to the underlying completion behind an Instructor model."""
    for attr in ("_raw_response", "raw_response", "_response"):
        raw = getattr(result, attr, None)
        if raw is not None:
            return raw
    return None


def _aggregate_usage_from_instructor_result(result: Any) -> tuple[int, int, Any | None]:
    """Sum token usage across Instructor retry attempts when available."""
    total_in = 0
    total_out = 0
    last_raw: Any | None = None

    attempts = getattr(result, "_attempts", None)
    if attempts:
        for attempt in attempts:
            raw = getattr(attempt, "completion", None) or getattr(attempt, "response", None)
            if raw is None:
                continue
            last_raw = raw
            tin, tout = _usage_from_response(raw)
            total_in += tin
            total_out += tout
        if total_in or total_out:
            return total_in, total_out, last_raw

    last_raw = _raw_response_from_instructor_result(result)
    tin, tout = _usage_from_response(last_raw)
    return tin, tout, last_raw


def _meta_from_structured_result(
    result: Any, *, target_model: str, latency_ms: int
) -> dict[str, Any]:
    """Build observability meta for Instructor structured calls."""
    tokens_in, tokens_out, raw = _aggregate_usage_from_instructor_result(result)
    model = _normalise_model_name(getattr(raw, "model", None) or target_model)
    cost_usd = _estimate_cost(model, tokens_in, tokens_out)
    if (tokens_in or tokens_out) and cost_usd == 0.0:
        # Dated / unknown id on the response — price against configured target.
        cost_usd = _estimate_cost(target_model, tokens_in, tokens_out)
    return {
        "model": model,
        "provider": _provider_from_model(model),
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
    }


class LLMWrapper:
    """Unified LLM client with cache, fallback, and cost tracking."""

    def __init__(
        self,
        *,
        openai_api_key: str | None,
        anthropic_api_key: str | None,
        primary_model: str,
        fallback_model: str,
        timeout: int,
        num_retries: int,
        cache: EstimationCache,
    ):
        self.openai_api_key = openai_api_key
        self.anthropic_api_key = anthropic_api_key
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.num_retries = num_retries
        self.cache = cache

        self.router = Router(
            model_list=[
                {
                    "model_name": "estimator",
                    "litellm_params": {
                        "model": primary_model,
                        "api_key": openai_api_key,
                        "timeout": timeout,
                    },
                },
                {
                    "model_name": "estimator",
                    "litellm_params": {
                        "model": fallback_model,
                        "api_key": anthropic_api_key,
                        "timeout": timeout,
                    },
                },
            ],
            fallbacks=[{"estimator": ["estimator"]}],
            num_retries=num_retries,
        )

        # Instructor wraps ``litellm.completion`` so we can call any of the
        # underlying providers with the same ``response_model=`` API.
        self._instructor = instructor.from_litellm(litellm.completion)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model_override: str | None = None,
        max_tokens: int = 4000,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        """Single LLM call returning a free-text answer. Kept for tests."""
        cache_key_model = model_override or self.primary_model
        cache_key = EstimationCache.make_key(
            system_prompt=system_prompt,
            user_message=user_message,
            model=cache_key_model,
            max_tokens=max_tokens,
            thinking_budget=thinking_budget,
        )
        cached = self.cache.get(cache_key)
        if cached:
            return {**cached, "cache_hit": True}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        kwargs = self._build_call_kwargs(
            messages=messages,
            max_tokens=max_tokens,
            thinking_budget=thinking_budget,
            model_override=model_override,
        )

        log.info(
            "llm_call_started",
            mode="blocking",
            model=model_override or self.primary_model,
        )
        t0 = time.perf_counter()
        try:
            response = self._dispatch(model_override=model_override, **kwargs)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            log.error(
                "llm_call_failed",
                error_type=type(exc).__name__,
                error=str(exc),
                latency_ms=latency_ms,
            )
            raise

        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = self._normalise_response(response, latency_ms=latency_ms)
        log.info(
            "llm_call_completed",
            model=result["model"],
            provider=result["provider"],
            input_tokens=result["usage"]["input_tokens"],
            output_tokens=result["usage"]["output_tokens"],
            cost_usd=result["cost_usd"],
            latency_ms=latency_ms,
            finish_reason=result["finish_reason"],
        )
        self.cache.set(cache_key, result)
        return {**result, "cache_hit": False}

    def complete_structured_chat(
        self,
        *,
        messages: list[dict[str, str]],
        response_model: type[T],
        model_override: str | None = None,
        max_tokens: int = 4000,
        max_retries: int = 6,
    ) -> tuple[T, dict[str, Any]]:
        """Conversational variant of :meth:`complete_structured`.

        Accepts a pre-built ``messages`` list (system + N user/assistant pairs +
        current user). Bypasses the Router for deterministic routing — same
        rationale as ``complete_structured``: the LiteLLM Router would
        round-robin between deployments and could non-deterministically pick
        the fallback. Instructor handles re-prompts when Pydantic validators
        raise.
        """
        target_model = model_override or self.primary_model
        api_key = (
            self.anthropic_api_key
            if _provider_from_model(target_model) == "anthropic"
            else self.openai_api_key
        )

        log.info(
            "llm_structured_chat_started",
            model=target_model,
            response_model=response_model.__name__,
            messages=len(messages),
        )
        t0 = time.perf_counter()
        try:
            result = self._instructor.chat.completions.create(
                model=target_model,
                api_key=api_key,
                timeout=self.timeout,
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            log.error(
                "llm_structured_chat_failed",
                error_type=type(exc).__name__,
                error=str(exc),
                latency_ms=latency_ms,
            )
            raise

        latency_ms = int((time.perf_counter() - t0) * 1000)
        meta = _meta_from_structured_result(
            result, target_model=target_model, latency_ms=latency_ms
        )
        log.info(
            "llm_structured_chat_completed",
            model=meta["model"],
            provider=meta["provider"],
            latency_ms=latency_ms,
            tokens_in=meta["tokens_in"],
            tokens_out=meta["tokens_out"],
            cost_usd=meta["cost_usd"],
        )
        return result, meta

    def complete_structured(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_model: type[T],
        model_override: str | None = None,
        max_tokens: int = 4000,
        max_retries: int = 6,
    ) -> tuple[T, dict[str, Any]]:
        """Run the LLM with Instructor and return ``(model_instance, meta)``.

        ``meta`` includes ``model``, ``provider`` and ``latency_ms``. Instructor
        re-prompts the LLM up to ``max_retries`` times when a Pydantic validator
        raises, feeding the ``ValueError`` message back to the model.

        Streaming bypasses are not relevant here — the entire model is built
        atomically by Instructor before this function returns.
        """
        target_model = model_override or self.primary_model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        api_key = (
            self.anthropic_api_key
            if _provider_from_model(target_model) == "anthropic"
            else self.openai_api_key
        )

        log.info(
            "llm_structured_call_started",
            model=target_model,
            response_model=response_model.__name__,
        )
        t0 = time.perf_counter()
        try:
            result = self._instructor.chat.completions.create(
                model=target_model,
                api_key=api_key,
                timeout=self.timeout,
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            log.error(
                "llm_structured_call_failed",
                error_type=type(exc).__name__,
                error=str(exc),
                latency_ms=latency_ms,
            )
            raise

        latency_ms = int((time.perf_counter() - t0) * 1000)
        meta = _meta_from_structured_result(
            result, target_model=target_model, latency_ms=latency_ms
        )
        log.info(
            "llm_structured_call_completed",
            model=meta["model"],
            provider=meta["provider"],
            latency_ms=latency_ms,
            tokens_in=meta["tokens_in"],
            tokens_out=meta["tokens_out"],
            cost_usd=meta["cost_usd"],
        )
        return result, meta

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_call_kwargs(
        self,
        *,
        messages: list[dict],
        max_tokens: int,
        thinking_budget: int | None,
        model_override: str | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
        }

        if thinking_budget is not None:
            target_model = model_override or self.primary_model
            if _provider_from_model(target_model) == "anthropic":
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
                kwargs["max_tokens"] = max(max_tokens, thinking_budget + 1024)
            else:
                log.warning(
                    "thinking_budget_ignored_for_provider",
                    provider=_provider_from_model(target_model),
                    model=target_model,
                )
        return kwargs

    def _dispatch(self, *, model_override: str | None, **kwargs: Any) -> Any:
        """Call the Router (with fallback) or LiteLLM directly when the caller
        wants a specific model."""
        if model_override:
            api_key = (
                self.anthropic_api_key
                if _provider_from_model(model_override) == "anthropic"
                else self.openai_api_key
            )
            return litellm.completion(
                model=model_override,
                api_key=api_key,
                timeout=self.timeout,
                num_retries=self.num_retries,
                **kwargs,
            )
        return self.router.completion(model="estimator", **kwargs)

    @staticmethod
    def _normalise_response(response: Any, *, latency_ms: int) -> dict[str, Any]:
        choice = response.choices[0]
        finish_reason = (choice.finish_reason or "stop").lower()
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", input_tokens + output_tokens) or (
            input_tokens + output_tokens
        )

        model = _normalise_model_name(response.model)
        return {
            "estimation": choice.message.content or "",
            "model": model,
            "provider": _provider_from_model(model),
            "finish_reason": finish_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
            "latency_ms": latency_ms,
            "cost_usd": _estimate_cost(model, input_tokens, output_tokens),
        }
