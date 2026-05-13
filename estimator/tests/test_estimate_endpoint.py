from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.context.examples import CANONICAL_EXAMPLES
from app.schemas.estimation import DetailLevel, OutputFormat, ProjectType
from app.services import llm_service

WELL_FORMED_MD = CANONICAL_EXAMPLES[0].estimation_markdown


def _fake_response(*, estimation: str = WELL_FORMED_MD, finish_reason: str = "stop") -> dict:
    return {
        "estimation": estimation,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "finish_reason": finish_reason,
        "usage": {"input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801},
        "latency_ms": 12,
        "cost_usd": 0.001234,
        "cache_hit": False,
    }


@pytest.fixture
def call_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    """Replace the LLM seam with a recording fake. Returns the list of calls."""
    calls: list[dict] = []

    def fake(
        *,
        system_prompt: str,
        user_message: str,
        model_override: str | None,
        max_tokens: int,
        thinking_budget: int | None,
    ) -> dict:
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "model_override": model_override,
                "max_tokens": max_tokens,
                "thinking_budget": thinking_budget,
            }
        )
        finish_reason = "length" if max_tokens <= 200 else "stop"
        return _fake_response(finish_reason=finish_reason)

    monkeypatch.setattr(llm_service, "_invoke_llm", fake)
    yield calls


def _valid_payload(**overrides: object) -> dict:
    base = {
        "description": (
            "The client needs a reservation mobile app with login, search, "
            "notifications, and an owner admin panel."
        ),
        "project_type": ProjectType.MOBILE_APP.value,
        "detail_level": DetailLevel.MEDIUM.value,
        "output_format": OutputFormat.NARRATIVE.value,
    }
    return {**base, **overrides}


def test_estimate_returns_text_and_prompt_version(client: TestClient, call_log: list[dict]) -> None:
    response = client.post("/api/v1/estimate", json=_valid_payload())
    assert response.status_code == 200
    body = response.json()
    assert "text" in body
    assert body["prompt_version"] == "v1"
    assert len(body["text"]) > 0
    assert body["validation"] is not None
    assert body["validation"]["score"] == 1.0
    assert body["usage"]["input_tokens"] == 1234
    assert body["cache_hit"] is False
    assert body["cost_usd"] == pytest.approx(0.001234)
    assert len(call_log) == 1
    assert "<project_description>" in call_log[0]["user_message"]
    assert "reservation mobile app" in call_log[0]["user_message"]


def test_evaluate_false_omits_validation(client: TestClient, call_log: list[dict]) -> None:
    response = client.post("/api/v1/estimate", json=_valid_payload(evaluate=False))
    assert response.status_code == 200
    body = response.json()
    assert body["validation"] is None


def test_phases_table_format_propagates_to_system_prompt(
    client: TestClient, call_log: list[dict]
) -> None:
    response = client.post(
        "/api/v1/estimate",
        json=_valid_payload(output_format=OutputFormat.PHASES_TABLE.value),
    )
    assert response.status_code == 200
    assert "confidence_pct" in call_log[0]["system_prompt"]


def test_description_too_short_returns_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/estimate",
        json=_valid_payload(description="short"),
    )
    assert response.status_code == 422


def test_provider_receives_separate_system_and_user_content(
    client: TestClient, call_log: list[dict]
) -> None:
    """The fake LLM seam receives distinct system vs user bodies (no merge)."""
    response = client.post("/api/v1/estimate", json=_valid_payload())
    assert response.status_code == 200
    sys_p = call_log[0]["system_prompt"]
    usr = call_log[0]["user_message"]
    assert "<role>" in sys_p
    assert "<project_description>" in usr
    assert "<project_description>" not in sys_p
    assert "reservation mobile app" in usr
    assert sys_p != usr
