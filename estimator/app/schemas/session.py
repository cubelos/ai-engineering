"""API models for conversational session endpoints."""

from pydantic import BaseModel, Field

from app.services.sessions import ProjectMetadata
from app.schemas.estimation import EstimationResponse


class CreateSessionResponse(BaseModel):
    """Response body for ``POST /api/v1/sessions``."""

    session_id: str


class SessionEstimateResponse(EstimationResponse):
    """Estimation result plus the session's distilled project memory."""

    session_id: str
    project_metadata: ProjectMetadata = Field(default_factory=ProjectMetadata)
