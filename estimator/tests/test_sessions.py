"""Integration and unit tests for Session 05: sessions, memory, attachments, sliding window.

Most endpoint tests mock SessionEstimationService or LLMWrapper so no API keys or
network are required. test_eight_turns_caps_messages_sent_to_llm uses the real
session pipeline with a recording LLM stub.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_session_estimation_service, get_session_store
from app.main import app
from app.schemas.estimation import (
    DetailLevel,
    EstimationResult,
    OutputFormat,
    ProjectType,
)
from app.schemas.session import SessionEstimateResponse
from app.services.session_estimation import SessionEstimationService
from app.services.sessions import ConversationHistory, ProjectMetadata, SessionStore


def _canned_result(*, confidence_pct: int = 70, summary: str | None = None) -> EstimationResult:
    return EstimationResult(
        summary=summary or "Mid-sized B2B SaaS for equipment loans across teams.",
        total_duration_weeks=8,
        total_cost_eur=30_000,
        confidence_pct=confidence_pct,
        phases=[
            {
                "name": "Discovery",
                "duration_weeks": 1,
                "cost_eur": 5_000,
                "summary": "Workshops, scoping and tech spike.",
            },
            {
                "name": "Implementation",
                "duration_weeks": 6,
                "cost_eur": 20_000,
                "summary": "Build the core SaaS features.",
            },
            {
                "name": "QA + launch",
                "duration_weeks": 1,
                "cost_eur": 5_000,
                "summary": "Test pass and production rollout.",
            },
        ],
    )


MAX_TURNS_FOR_TEST = 6
MAX_ESTIMATION_MESSAGES = 1 + MAX_TURNS_FOR_TEST * 2 + 1  # system + window + current user


class RecordingLLMWrapper:
    """Captures ``messages`` passed to estimation calls (no network)."""

    def __init__(self) -> None:
        self.estimation_message_lengths: list[int] = []
        self.estimation_message_snapshots: list[list[dict]] = []

    def complete_structured_messages(self, *, messages, response_model, **kwargs):
        self.estimation_message_lengths.append(len(messages))
        self.estimation_message_snapshots.append(messages)
        return _canned_result(summary="Stub estimation for sliding-window test."), {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "latency_ms": 1,
        }

    def complete_structured(
        self,
        *,
        system_prompt: str,
        user_message: str,
        response_model,
        **kwargs,
    ):
        if response_model is ProjectMetadata:
            return ProjectMetadata(project_name="RecordedProject"), {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "latency_ms": 1,
            }
        return self.complete_structured_messages(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_model=response_model,
            **kwargs,
        )


class FakeSessionEstimationService:
    """Records turns and simulates metadata / attachment behaviour."""

    def __init__(self) -> None:
        self.turn_count = 0
        self.last_attachments: list[str] = []

    async def estimate_turn(self, **kwargs) -> SessionEstimateResponse:
        self.turn_count += 1
        session_id = kwargs["session_id"]
        attachments = kwargs.get("attachments") or []
        self.last_attachments = [a.filename or "" for a in attachments]

        metadata = ProjectMetadata()
        transcript = kwargs["transcript"]
        if "BookFlow" in transcript or self.turn_count >= 2:
            metadata = ProjectMetadata(
                project_name="BookFlow",
                mentioned_technologies=["rails", "react"],
            )

        confidence = 70
        if any("high-complexity-spec" in name for name in self.last_attachments):
            confidence = 92

        result = _canned_result(
            confidence_pct=confidence,
            summary=f"Estimate for turn {self.turn_count}.",
        )
        return SessionEstimateResponse(
            result=result,
            prompt_version="v1",
            cached=False,
            session_id=session_id,
            project_metadata=metadata,
        )


@pytest.fixture
def session_store() -> SessionStore:
    store = SessionStore(max_turns=6)
    store.clear()
    app.dependency_overrides[get_session_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_session_store, None)
    store.clear()


@pytest.fixture
def fake_session_service() -> FakeSessionEstimationService:
    svc = FakeSessionEstimationService()
    app.dependency_overrides[get_session_estimation_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_session_estimation_service, None)


@pytest.fixture
def recording_session_service(
    session_store: SessionStore,
) -> tuple[SessionEstimationService, RecordingLLMWrapper]:
    """Real session pipeline with a recording LLM (exercises history + store)."""
    recorder = RecordingLLMWrapper()
    svc = SessionEstimationService(
        llm_wrapper=recorder,  # type: ignore[arg-type]
        session_store=session_store,
        openai_client=None,
        prompt_version="v1",
    )
    app.dependency_overrides[get_session_estimation_service] = lambda: svc
    yield svc, recorder
    app.dependency_overrides.pop(get_session_estimation_service, None)


def test_conversation_history_sliding_window_respects_max_turns() -> None:
    """After 8 append_turn calls, only the last 6 user/assistant pairs remain in history."""
    history = ConversationHistory(max_turns=6)
    for i in range(8):
        history.append_turn(user=f"user-{i}", assistant=f"assistant-{i}")

    assert history.turn_count == 6
    assert len(history._messages) == 12

    messages = history.to_messages_list("system prompt")
    assert len(messages) == 13
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "user-2"
    assert messages[-1]["content"] == "assistant-7"


def test_metadata_updates_across_two_turns(
    client: TestClient,
    session_store: SessionStore,
    fake_session_service: FakeSessionEstimationService,
) -> None:
    """Two estimate calls on the same session return richer project_metadata on the second turn."""
    create = client.post("/api/v1/sessions")
    assert create.status_code == 200
    session_id = create.json()["session_id"]

    payload = {
        "transcript": "We are scoping a SaaS for equipment loans across teams.",
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "phases_table",
    }
    first = client.post(f"/api/v1/sessions/{session_id}/estimate", data=payload)
    assert first.status_code == 200
    assert first.json()["project_metadata"]["project_name"] is None

    payload["transcript"] = (
        "The project is called BookFlow and we will use Rails and React."
    )
    second = client.post(f"/api/v1/sessions/{session_id}/estimate", data=payload)
    assert second.status_code == 200
    meta = second.json()["project_metadata"]
    assert meta["project_name"] == "BookFlow"
    assert "rails" in meta["mentioned_technologies"]


def test_pdf_attachment_influences_estimation_confidence(
    client: TestClient,
    session_store: SessionStore,
    fake_session_service: FakeSessionEstimationService,
) -> None:
    """Multipart upload with a PDF changes a mocked confidence field vs the same request without file."""
    create = client.post("/api/v1/sessions")
    session_id = create.json()["session_id"]

    payload = {
        "transcript": "Estimate this internal tool with the attached specification.",
        "project_type": "internal_tool",
        "detail_level": "medium",
        "output_format": "phases_table",
    }
    without_pdf = client.post(f"/api/v1/sessions/{session_id}/estimate", data=payload)
    assert without_pdf.status_code == 200
    assert without_pdf.json()["result"]["confidence_pct"] == 70

    pdf_bytes = b"%PDF-1.4 minimal"
    with_pdf = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data=payload,
        files={"attachments": ("high-complexity-spec.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert with_pdf.status_code == 200
    assert with_pdf.json()["result"]["confidence_pct"] == 92
    assert fake_session_service.last_attachments == ["high-complexity-spec.pdf"]


def test_create_session_returns_uuid(client: TestClient, session_store: SessionStore) -> None:
    """POST /api/v1/sessions returns 200 with a session_id string (UUID length)."""
    response = client.post("/api/v1/sessions")
    assert response.status_code == 200
    body = response.json()
    assert "session_id" in body
    assert len(body["session_id"]) >= 32


def test_unknown_session_returns_404(
    client: TestClient,
    fake_session_service: FakeSessionEstimationService,
    session_store: SessionStore,
) -> None:
    """Estimating against a non-existent session_id returns HTTP 404."""
    response = client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/estimate",
        data={
            "transcript": "A long enough transcript for the validation minimum length.",
            "project_type": "web_saas",
            "detail_level": "medium",
            "output_format": "phases_table",
        },
    )
    assert response.status_code == 404


def test_eight_turns_caps_messages_sent_to_llm(
    client: TestClient,
    session_store: SessionStore,
    recording_session_service: tuple[SessionEstimationService, RecordingLLMWrapper],
) -> None:
    """Eight POSTs cap the messages array sent to complete_structured_messages at 14 (6-turn window)."""
    _svc, recorder = recording_session_service
    assert session_store._max_turns == MAX_TURNS_FOR_TEST

    create = client.post("/api/v1/sessions")
    assert create.status_code == 200
    session_id = create.json()["session_id"]

    payload = {
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "phases_table",
    }

    for turn in range(8):
        payload["transcript"] = (
            f"Turn {turn + 1}: we are scoping a B2B SaaS for equipment loans "
            f"with incremental detail each iteration."
        )
        response = client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data=payload,
        )
        assert response.status_code == 200, response.text

    assert len(recorder.estimation_message_lengths) == 8

    for length in recorder.estimation_message_lengths:
        assert length <= MAX_ESTIMATION_MESSAGES

    # Turns 1–6 grow linearly; turns 7–8 stay capped at the window size.
    assert recorder.estimation_message_lengths[0] == 2
    assert recorder.estimation_message_lengths[5] == 12
    assert recorder.estimation_message_lengths[6] == MAX_ESTIMATION_MESSAGES
    assert recorder.estimation_message_lengths[7] == MAX_ESTIMATION_MESSAGES

    last_messages = recorder.estimation_message_snapshots[-1]
    assert last_messages[0]["role"] == "system"
    history_roles = [m["role"] for m in last_messages[1:-1]]
    assert len(history_roles) == MAX_TURNS_FOR_TEST * 2
    assert history_roles.count("user") == MAX_TURNS_FOR_TEST
    assert history_roles.count("assistant") == MAX_TURNS_FOR_TEST
    assert last_messages[-1]["role"] == "user"

    session = session_store.get(session_id)
    assert session is not None
    assert session.history.turn_count == MAX_TURNS_FOR_TEST
