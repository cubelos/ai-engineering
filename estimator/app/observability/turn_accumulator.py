"""Aggregate LLM usage across all sub-calls within one conversational turn."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TurnAccumulator:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    _calls: int = field(default=0, repr=False)

    def add(self, meta: dict) -> None:
        """Fold one ``complete_structured*`` meta dict into the running total."""
        self.tokens_in += int(meta.get("tokens_in") or 0)
        self.tokens_out += int(meta.get("tokens_out") or 0)
        self.cost_usd = round(self.cost_usd + float(meta.get("cost_usd") or 0.0), 6)
        self.latency_ms += int(meta.get("latency_ms") or 0)
        self._calls += 1
