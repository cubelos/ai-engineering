"""FastAPI dependency factories for process-wide singletons (cache, LLM, services).

All ``get_*`` functions are ``@lru_cache``d: restart uvicorn after changing ``.env``.
"""

from __future__ import annotations

from functools import lru_cache

import redis
import structlog
from openai import OpenAI

from app.cache.semantic import EstimationSemanticCache
from app.config import get_settings
from app.services.cache import EstimationCache
from app.services.estimation import EstimationService
from app.services.llm_wrapper import LLMWrapper
from app.services.session_estimation import SessionEstimationService
from app.services.sessions import SessionStore

log = structlog.get_logger()


@lru_cache
def get_cache() -> EstimationCache:
    """Redis-backed exact-match cache for LLM responses."""
    settings = get_settings()
    return EstimationCache.from_url(settings.REDIS_URL, ttl=settings.CACHE_TTL)


@lru_cache
def get_llm_wrapper() -> LLMWrapper:
    """LiteLLM + Instructor wrapper shared by transactional and session pipelines."""
    settings = get_settings()
    return LLMWrapper(
        openai_api_key=settings.OPENAI_API_KEY,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        primary_model=settings.PRIMARY_MODEL,
        fallback_model=settings.FALLBACK_MODEL,
        timeout=settings.LLM_TIMEOUT,
        num_retries=settings.LLM_RETRIES,
        cache=get_cache(),
    )


@lru_cache
def get_openai_client() -> OpenAI | None:
    """Lazy OpenAI client used by ``check_input`` (Moderation API) and the
    semantic cache (Embeddings API)."""
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        return None
    return OpenAI(api_key=settings.OPENAI_API_KEY)


@lru_cache
def get_semantic_cache() -> EstimationSemanticCache | None:
    """Build the semantic cache, swallowing setup errors so the rest of the
    pipeline keeps working if Redis Stack / RediSearch is not available
    (e.g. running on vanilla redis:7-alpine)."""
    settings = get_settings()
    openai_client = get_openai_client()
    if openai_client is None:
        log.warning("semantic_cache_disabled", reason="no_openai_key")
        return None

    # We use redisvl's OpenAITextVectorizer; it lazy-loads the OpenAI client.
    try:
        from redisvl.utils.vectorize import OpenAITextVectorizer

        vectorizer = OpenAITextVectorizer(
            model=settings.EMBEDDING_MODEL,
            api_config={"api_key": settings.OPENAI_API_KEY},
        )
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)
        return EstimationSemanticCache(
            redis_client=redis_client,
            vectorizer=vectorizer,
            threshold=settings.SEMANTIC_CACHE_THRESHOLD,
            ttl=settings.SEMANTIC_CACHE_TTL,
            log_only=settings.SEMANTIC_CACHE_LOG_ONLY,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "semantic_cache_disabled",
            reason="setup_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return None


@lru_cache
def get_estimation_service() -> EstimationService:
    """Transactional pipeline for ``POST /api/v1/estimate`` (guardrails + caches + LLM)."""
    return EstimationService(
        llm_wrapper=get_llm_wrapper(),
        exact_cache=get_cache(),
        semantic_cache=get_semantic_cache(),
        openai_client=get_openai_client(),
    )


@lru_cache
def get_session_store() -> SessionStore:
    """In-memory registry of conversational sessions (``MAX_CONVERSATION_TURNS`` from settings)."""
    settings = get_settings()
    return SessionStore(max_turns=settings.MAX_CONVERSATION_TURNS)


@lru_cache
def get_session_estimation_service() -> SessionEstimationService:
    """Multi-turn pipeline for ``POST /api/v1/sessions/{id}/estimate`` (no Redis cache)."""
    return SessionEstimationService(
        llm_wrapper=get_llm_wrapper(),
        session_store=get_session_store(),
        openai_client=get_openai_client(),
        prompt_version="v1",
    )
