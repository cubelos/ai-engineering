"""Unit tests for stress metrics."""

from __future__ import annotations

from app.schemas.estimation import EstimationResult
from app.schemas.observation import TurnObservation
from app.sessions.models import ProjectMetadata
from evals.stress.metrics import (
    AttachmentRecallMetric,
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
    SessionSnapshot,
)


def _obs(**kwargs) -> TurnObservation:
    defaults = {
        "turn_index": 1,
        "session_id": "abc",
        "enriched_transcript_chars": 100,
        "attachments_total_chars": 0,
        "messages_in_window": 2,
        "anchors_count": 0,
        "summary_chars": 0,
        "tokens_in": 500,
        "tokens_out": 200,
        "cost_usd": 0.01,
        "latency_ms": 2000,
        "cache_hit_kind": "none",
        "last_resolved_tier": "pm",
    }
    return TurnObservation(**{**defaults, **kwargs})


def test_latency_budget_passes_under_budget() -> None:
    metric = LatencyBudgetMetric(4000)
    result = metric.evaluate(_obs(latency_ms=3999))
    assert result.passed
    assert result.score == 1.0


def test_latency_budget_fails_over_budget() -> None:
    metric = LatencyBudgetMetric(4000)
    result = metric.evaluate(_obs(latency_ms=4001))
    assert not result.passed
    assert result.score == 0.0


def test_cost_budget_boundary_passes() -> None:
    metric = CostBudgetMetric(0.05)
    result = metric.evaluate(_obs(cost_usd=0.05))
    assert result.passed


def test_cost_budget_fails_over_budget() -> None:
    metric = CostBudgetMetric(0.05)
    result = metric.evaluate(_obs(cost_usd=0.050001))
    assert not result.passed


def test_memory_drift_finds_fact_in_metadata() -> None:
    snapshot = SessionSnapshot(
        summary="older context",
        anchors_text="",
        metadata=ProjectMetadata(project_name="Nimbus"),
    )
    result = MemoryDriftMetric("Nimbus").evaluate(snapshot)
    assert result.passed


def test_memory_drift_fails_when_fact_missing() -> None:
    snapshot = SessionSnapshot(
        summary="unrelated",
        anchors_text="",
        metadata=ProjectMetadata(project_name="Other"),
    )
    result = MemoryDriftMetric("Nimbus").evaluate(snapshot)
    assert not result.passed


def test_attachment_recall_passes_when_marker_present() -> None:
    metric = AttachmentRecallMetric("ATTACH_MARKER_5KB")
    result = EstimationResult(
        summary="Scope includes ATTACH_MARKER_5KB from the spec.",
        confidence_pct=70,
        phases=[
            {
                "name": "Build",
                "duration_weeks": 4,
                "cost_eur": 10_000,
                "summary": "Implement core flows.",
            }
        ],
        total_duration_weeks=4,
        total_cost_eur=10_000,
    )
    assert metric.evaluate(result).passed
