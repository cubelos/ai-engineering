"""Session lifecycle and multi-turn estimation endpoints.

Error mapping:

- Missing or unknown ``session_id`` → HTTP 404.
- ``InputGuardrailViolation`` or ``ValueError`` (e.g. invalid attachment) → HTTP 400.
- Upstream LLM / Instructor failures → HTTP 502.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.dependencies import get_session_estimation_service, get_session_store
from app.guardrails.input import InputGuardrailViolation
from app.schemas.estimation import DetailLevel, OutputFormat, ProjectType
from app.schemas.session import CreateSessionResponse, SessionEstimateResponse
from app.services.session_estimation import SessionEstimationService
from app.services.sessions import SessionStore

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["sessions"])


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session(
    store: SessionStore = Depends(get_session_store),
) -> CreateSessionResponse:
    """Create an empty session and return its identifier."""
    session = store.create()
    log.info("session_created", session_id=session.session_id)
    return CreateSessionResponse(session_id=session.session_id)


@router.post(
    "/sessions/{session_id}/estimate",
    response_model=SessionEstimateResponse,
)
async def estimate_in_session(
    session_id: str,
    transcript: str = Form(
        ...,
        min_length=20,
        max_length=80000,
        description="Meeting transcript or project description for this turn.",
    ),
    project_type: ProjectType = Form(...),
    detail_level: DetailLevel = Form(...),
    output_format: OutputFormat = Form(...),
    attachments: list[UploadFile] = File(default=[]),
    service: SessionEstimationService = Depends(get_session_estimation_service),
    store: SessionStore = Depends(get_session_store),
) -> SessionEstimateResponse:
    """Run one conversational estimation turn with optional file attachments."""
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    log.info(
        "session_estimate_request",
        session_id=session_id,
        transcript_chars=len(transcript),
        attachment_count=len(attachments),
        project_type=project_type.value,
    )

    try:
        return await service.estimate_turn(
            session_id=session_id,
            transcript=transcript.strip(),
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
            attachments=attachments,
        )
    except InputGuardrailViolation as exc:
        log.info(
            "session_estimate_blocked",
            session_id=session_id,
            reason=exc.reason,
        )
        raise HTTPException(
            status_code=400,
            detail={"reason": exc.reason, "message": exc.message},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error(
            "session_estimate_error",
            session_id=session_id,
            error=str(exc)[:400],
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail="Upstream LLM call failed") from exc
