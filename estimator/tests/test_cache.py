"""Unit tests for the Redis exact-match cache (EstimationCache)."""

import fakeredis
import pytest

from app.services.cache import EstimationCache


@pytest.fixture
def cache() -> EstimationCache:
    return EstimationCache(fakeredis.FakeRedis(decode_responses=True), ttl=60)


def test_make_key_is_deterministic() -> None:
    """The same prompt inputs always produce the same cache key."""
    args = dict(
        system_prompt="hello",
        user_message="world",
        model="gpt-4o-mini",
        max_tokens=4000,
        thinking_budget=None,
    )
    assert EstimationCache.make_key(**args) == EstimationCache.make_key(**args)


def test_make_key_changes_when_inputs_change() -> None:
    """Any change to prompt, model, or generation params yields a different key."""
    base = dict(
        system_prompt="hello",
        user_message="world",
        model="gpt-4o-mini",
        max_tokens=4000,
        thinking_budget=None,
    )
    key_base = EstimationCache.make_key(**base)
    assert EstimationCache.make_key(**{**base, "system_prompt": "hi"}) != key_base
    assert EstimationCache.make_key(**{**base, "model": "gpt-4o"}) != key_base
    assert EstimationCache.make_key(**{**base, "max_tokens": 1000}) != key_base
    assert EstimationCache.make_key(**{**base, "thinking_budget": 2048}) != key_base


def test_set_then_get_roundtrips_payload(cache: EstimationCache) -> None:
    """A stored payload is returned verbatim on cache hit."""
    payload = {"estimation": "...", "model": "gpt-4o-mini", "cost_usd": 0.001}
    key = EstimationCache.make_key(
        system_prompt="s", user_message="u", model="m", max_tokens=10, thinking_budget=None
    )
    cache.set(key, payload)
    assert cache.get(key) == payload


def test_get_returns_none_on_miss(cache: EstimationCache) -> None:
    """An unknown key returns None instead of raising."""
    key = EstimationCache.make_key(
        system_prompt="s", user_message="u", model="m", max_tokens=10, thinking_budget=None
    )
    assert cache.get(key) is None


def test_set_applies_ttl(cache: EstimationCache) -> None:
    """Redis TTL is set on write so entries expire automatically."""
    key = EstimationCache.make_key(
        system_prompt="s", user_message="u", model="m", max_tokens=10, thinking_budget=None
    )
    cache.set(key, {"x": 1})
    ttl = cache.redis.ttl(key)
    assert 0 < ttl <= 60
