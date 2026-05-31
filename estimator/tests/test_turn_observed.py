"""Integration tests for the unified turn_observed event."""

from __future__ import annotations

from tests.conftest import FakeLLMWrapper
from app.dependencies import get_estimation_service, get_session_store
from app.main import app
from app.schemas.estimation import EstimationResult
from app.services.estimation import EstimationService
from app.sessions.models import ProjectMetadata
from app.sessions.store import SessionStore
from fastapi.testclient import TestClient


def _wire_client(fake: FakeLLMWrapper, store: SessionStore) -> TestClient:
    service = EstimationService(
        llm_wrapper=fake,
        exact_cache=None,
        semantic_cache=None,
        openai_client=None,
    )
    app.dependency_overrides[get_estimation_service] = lambda: service
    app.dependency_overrides[get_session_store] = lambda: store
    return TestClient(app)


def test_turn_observed_exposed_via_get_session() -> None:
    fake = FakeLLMWrapper()
    fake.add_turn(
        metadata=ProjectMetadata(project_name="Nimbus", mentioned_technologies=["React"]),
    )
    store = SessionStore(max_turns=6)
    client = _wire_client(fake, store)
    try:
        sid = client.post("/sessions").json()["session_id"]
        transcript = (
            "We want a B2B CRM called Nimbus in React + Postgres for the sales team."
        )
        response = client.post(
            f"/sessions/{sid}/estimate",
            data={
                "transcript": transcript,
                "project_type": "web_saas",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
        )
        assert response.status_code == 200

        info = client.get(f"/sessions/{sid}").json()
        obs = info["last_turn_observation"]
        assert obs is not None
        assert obs["turn_index"] == 1
        assert obs["session_id"] == sid
        assert obs["enriched_transcript_chars"] == len(transcript)
        assert obs["attachments_total_chars"] == 0
        assert obs["messages_in_window"] == 2
        assert obs["anchors_count"] == 0
        assert obs["summary_chars"] == 0
        assert obs["tokens_in"] > 0
        assert obs["tokens_out"] > 0
        assert obs["cost_usd"] >= 0
        assert obs["latency_ms"] >= 0
        assert obs["cache_hit_kind"] == "none"
        assert obs["last_resolved_tier"] is not None
        assert info["summary"] is None or isinstance(info["summary"], str)
        assert isinstance(info["anchors_text"], str)
    finally:
        app.dependency_overrides.clear()


def test_turn_index_increments_on_second_turn() -> None:
    fake = FakeLLMWrapper()
    fake.add_turn(metadata=ProjectMetadata(project_name="Nimbus"))
    fake.add_turn(metadata=ProjectMetadata(project_name="Nimbus"))
    store = SessionStore(max_turns=6)
    client = _wire_client(fake, store)
    try:
        sid = client.post("/sessions").json()["session_id"]
        base = {
            "project_type": "web_saas",
            "detail_level": "medium",
            "output_format": "phases_table",
        }
        client.post(
            f"/sessions/{sid}/estimate",
            data={"transcript": "First turn about Nimbus CRM for sales.", **base},
        )
        client.post(
            f"/sessions/{sid}/estimate",
            data={"transcript": "Add Stripe billing module to Nimbus.", **base},
        )
        info = client.get(f"/sessions/{sid}").json()
        assert info["last_turn_observation"]["turn_index"] == 2
        assert info["message_count"] == 4
    finally:
        app.dependency_overrides.clear()
