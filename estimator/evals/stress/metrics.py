"""Stress-test metrics operating on turn observations and session snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.estimation import EstimationResult
from app.schemas.observation import TurnObservation
from app.sessions.models import ProjectMetadata
from evals.metrics import MetricResult


@dataclass
class SessionSnapshot:
    summary: str | None
    anchors_text: str
    metadata: ProjectMetadata
    last_turn_observation: TurnObservation | None = None


class LatencyBudgetMetric:
    """1.0 if latency_ms <= budget_ms; 0.0 otherwise."""

    name = "latency_budget"

    def __init__(self, budget_ms: int) -> None:
        self.budget_ms = budget_ms

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        passed = observation.latency_ms <= self.budget_ms
        return MetricResult(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details=(
                f"latency {observation.latency_ms} ms within {self.budget_ms} ms budget"
                if passed
                else f"latency {observation.latency_ms} ms exceeds {self.budget_ms} ms budget"
            ),
        )


class CostBudgetMetric:
    """1.0 if cost_usd <= budget_usd; 0.0 otherwise."""

    name = "cost_budget"

    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = budget_usd

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        passed = observation.cost_usd <= self.budget_usd
        return MetricResult(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details=(
                f"cost {observation.cost_usd:.6f} USD within {self.budget_usd:.4f} USD budget"
                if passed
                else f"cost {observation.cost_usd:.6f} USD exceeds {self.budget_usd:.4f} USD budget"
            ),
        )


class MemoryDriftMetric:
    """1.0 if fact appears in summary, anchors, or metadata at turn N > k."""

    name = "memory_drift"

    def __init__(
        self,
        fact: str,
        *,
        where: list[str] | None = None,
    ) -> None:
        self.fact = fact
        self.where = where or ["summary", "anchors", "metadata"]

    def _haystacks(self, snapshot: SessionSnapshot) -> dict[str, str]:
        meta_blob = " ".join(
            filter(
                None,
                [
                    snapshot.metadata.project_name or "",
                    snapshot.metadata.agreed_scope or "",
                    " ".join(snapshot.metadata.mentioned_technologies),
                    str(snapshot.metadata.assumed_team_size or ""),
                ],
            )
        )
        return {
            "summary": snapshot.summary or "",
            "anchors": snapshot.anchors_text,
            "metadata": meta_blob,
        }

    def evaluate(self, snapshot: SessionSnapshot) -> MetricResult:
        needle = self.fact.lower()
        haystacks = self._haystacks(snapshot)
        hits = [
            field
            for field in self.where
            if needle in haystacks.get(field, "").lower()
        ]
        passed = bool(hits)
        return MetricResult(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details=(
                f"fact {self.fact!r} found in {hits}"
                if passed
                else f"fact {self.fact!r} missing from {self.where}"
            ),
        )


class AttachmentRecallMetric:
    """1.0 if marker from synthetic PDF appears in the estimation summary."""

    name = "attachment_recall"

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def evaluate(self, result: EstimationResult) -> MetricResult:
        haystack = " ".join(
            [result.summary] + [phase.summary + " " + phase.name for phase in result.phases]
        ).lower()
        passed = self.marker.lower() in haystack
        return MetricResult(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details=(
                f"marker {self.marker!r} present in response"
                if passed
                else f"marker {self.marker!r} missing from response"
            ),
        )
