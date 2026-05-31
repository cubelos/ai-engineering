"""Per-turn observability payload for CAG stress instrumentation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CacheHitKind = Literal["none", "exact", "semantic"]


class TurnObservation(BaseModel):
    turn_index: int = Field(ge=1, description="1-based turn counter within the session.")
    session_id: str
    enriched_transcript_chars: int = Field(ge=0)
    attachments_total_chars: int = Field(ge=0)
    messages_in_window: int = Field(ge=0, description="len(history.messages) after compression.")
    anchors_count: int = Field(ge=0)
    summary_chars: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    latency_ms: int = Field(ge=0)
    cache_hit_kind: CacheHitKind = "none"
    last_resolved_tier: str | None = None
