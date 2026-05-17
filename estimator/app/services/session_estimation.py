"""Multi-turn estimation pipeline with session memory and multimodal attachments.

One HTTP turn: guardrails → optional PDF upload → LLM estimation with sliding-window
history → metadata extractor → update ``SessionStore``. Does not use Redis cache.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import UploadFile

from app.config import get_settings
from app.guardrails.input import check_input
from app.guardrails.output import enforce_scope_response
from app.prompts.loader import (
    render_metadata_extractor_prompt,
    render_session_estimation_system,
    render_session_user_text,
)
from app.schemas.estimation import (
    DetailLevel,
    EstimationResult,
    OutputFormat,
    ProjectType,
)
from app.schemas.session import SessionEstimateResponse
from app.services.attachments import (
    AttachmentError,
    AttachmentService,
    read_upload_bytes,
    validate_attachment,
)
from app.services.llm_wrapper import LLMWrapper, _provider_from_model
from app.services.sessions import ProjectMetadata, Session, SessionStore

log = structlog.get_logger()

METADATA_EXTRACTOR_MODEL = "gpt-4o-mini"  # cheap second call per turn for ProjectMetadata


class SessionEstimationService:
    """Orchestrates one turn in a conversational estimation session."""

    def __init__(
        self,
        *,
        llm_wrapper: LLMWrapper,
        session_store: SessionStore,
        openai_client: Any | None = None,
        prompt_version: str = "v1",
    ) -> None:
        self.llm_wrapper = llm_wrapper
        self.session_store = session_store
        self.openai_client = openai_client
        self.prompt_version = prompt_version
        settings = get_settings()
        self.attachment_service = AttachmentService(
            openai_api_key=settings.OPENAI_API_KEY,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            primary_model=settings.PRIMARY_MODEL,
        )

    async def estimate_turn(
        self,
        *,
        session_id: str,
        transcript: str,
        project_type: ProjectType,
        detail_level: DetailLevel,
        output_format: OutputFormat,
        attachments: list[UploadFile],
    ) -> SessionEstimateResponse:
        """Run one conversational turn: estimate, extract metadata, append history."""
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError("Session not found")

        check_input(transcript, openai_client=self.openai_client)

        provider = self.attachment_service.provider
        if session.attachment_provider and session.attachment_provider != provider:
            log.warning(
                "session_attachment_provider_mismatch",
                session_id=session_id,
                stored=session.attachment_provider,
                current=provider,
            )

        for upload in attachments:
            validate_attachment(upload)
            data = await read_upload_bytes(upload)
            file_id = await self.attachment_service.upload_pdf(
                filename=upload.filename or "attachment.pdf",
                data=data,
                provider=provider,
            )
            if file_id not in session.attachment_file_ids:
                session.attachment_file_ids.append(file_id)
            session.attachment_provider = provider

        system_prompt = render_session_estimation_system(
            project_type=project_type.value,
            detail_level=detail_level.value,
            output_format=output_format.value,
            project_metadata=session.project_metadata,
            version=self.prompt_version,
        )

        user_text = render_session_user_text(
            transcript=transcript,
            project_type=project_type.value,
            version=self.prompt_version,
        )
        user_content = self.attachment_service.build_user_content(
            text=user_text,
            file_ids=session.attachment_file_ids,
            provider=provider,
        )

        messages = session.history.to_messages_list(system_prompt)
        messages.append({"role": "user", "content": user_content})

        extra_kwargs: dict[str, Any] = {}
        if provider == "anthropic" and session.attachment_file_ids:
            extra_kwargs["extra_headers"] = {"anthropic-beta": "files-api-2025-04-14"}

        result, meta = self.llm_wrapper.complete_structured_messages(
            messages=messages,
            response_model=EstimationResult,
            extra_kwargs=extra_kwargs or None,
            max_retries=10,
        )
        log.info(
            "session_estimation_generated",
            session_id=session_id,
            confidence_pct=result.confidence_pct,
            **meta,
        )

        result = enforce_scope_response(result)

        session.project_metadata = self._extract_metadata(
            current=session.project_metadata,
            user_turn=transcript,
            assistant_turn=result.summary,
        )

        session.history.append_turn(
            user=f"[Turn] {transcript[:500]}",
            assistant=result.summary,
        )
        session.touch()

        return SessionEstimateResponse(
            result=result,
            prompt_version=self.prompt_version,
            cached=False,
            session_id=session_id,
            project_metadata=session.project_metadata,
        )

    def _extract_metadata(
        self,
        *,
        current: ProjectMetadata,
        user_turn: str,
        assistant_turn: str,
    ) -> ProjectMetadata:
        """Second LLM call: merge new facts from this turn into ``ProjectMetadata``."""
        prompt = render_metadata_extractor_prompt(
            current_metadata=current,
            user_turn=user_turn,
            assistant_turn=assistant_turn,
            version="v1",
        )
        updated, _meta = self.llm_wrapper.complete_structured(
            system_prompt=(
                "You extract structured project facts for a software estimation session. "
                "Return only valid ProjectMetadata fields."
            ),
            user_message=prompt,
            response_model=ProjectMetadata,
            model_override=METADATA_EXTRACTOR_MODEL,
            max_tokens=1024,
            max_retries=3,
        )
        return updated
