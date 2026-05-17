"""In-process session state: conversation history and project metadata.

Sessions live in a process-local dict (no Redis/Postgres). That volatility is
deliberate for this course phase: we focus on separating *history* (the raw
message array sent to the LLM) from *memory* (distilled project facts in
``ProjectMetadata``). Persistence and horizontal scaling belong to a later
deployment module; the two structures serialize independently when we add a
store later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ProjectMetadata(BaseModel):
    """Distilled facts about the project under estimation.

    Survives history truncation. Updated after each turn via the metadata
    extractor.
    """

    project_name: str | None = None
    assumed_team_size: int | None = Field(default=None, ge=1, le=500)
    mentioned_technologies: list[str] = Field(default_factory=list)
    agreed_scope: str | None = None
    explicit_constraints: list[str] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)


class ConversationHistory:
    """Sliding window over user/assistant message pairs.

    The system prompt is NOT stored here; callers regenerate it from the
    current ``ProjectMetadata`` on every LLM call via ``to_messages_list``.
    """

    def __init__(self, *, max_turns: int = 6) -> None:
        self.max_turns = max_turns
        self._messages: list[dict[str, str]] = []

    def append_turn(self, user: str, assistant: str) -> None:
        """Append one user+assistant pair and drop oldest pairs if over limit."""
        self._messages.append({"role": "user", "content": user})
        self._messages.append({"role": "assistant", "content": assistant})
        max_messages = self.max_turns * 2
        if len(self._messages) > max_messages:
            self._messages = self._messages[-max_messages:]

    def to_messages_list(self, system_prompt: str) -> list[dict[str, Any]]:
        """Build the messages array for the LLM API (system + windowed history)."""
        return [{"role": "system", "content": system_prompt}, *self._messages]

    @property
    def turn_count(self) -> int:
        """Number of complete user/assistant pairs currently stored."""
        return len(self._messages) // 2


@dataclass
class Session:
    """All conversational state for one estimation session."""

    session_id: str = field(default_factory=lambda: str(uuid4()))
    history: ConversationHistory = field(default_factory=ConversationHistory)
    project_metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    attachment_file_ids: list[str] = field(default_factory=list)
    attachment_provider: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def touch(self) -> None:
        """Refresh ``updated_at`` after a successful turn."""
        self.updated_at = datetime.now(UTC)


class SessionStore:
    """Process-local registry of active sessions keyed by ``session_id``."""

    def __init__(self, *, max_turns: int = 6) -> None:
        self._max_turns = max_turns
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        """Allocate a new session with empty history and metadata."""
        session = Session(
            history=ConversationHistory(max_turns=self._max_turns),
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Return the session or ``None`` if the id is unknown or expired from memory."""
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        """Remove a session from the in-memory registry."""
        return self._sessions.pop(session_id, None) is not None

    def clear(self) -> None:
        """Reset all sessions (used in tests)."""
        self._sessions.clear()
