"""Upload attachments via provider Files APIs (Camino A — multimodal direct)."""

from __future__ import annotations

import structlog
from fastapi import UploadFile

from app.services.llm_wrapper import _provider_from_model

log = structlog.get_logger()

ALLOWED_EXTENSIONS = {".pdf"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
}


class AttachmentError(ValueError):
    """Raised when an attachment cannot be accepted or uploaded."""


def validate_attachment(upload: UploadFile) -> None:
    """Reject non-PDF uploads before calling the provider Files API."""
    filename = (upload.filename or "").lower()
    if not any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise AttachmentError(
            f"Unsupported file type: {upload.filename!r}. "
            "Camino A (multimodal) supports PDF only in this phase."
        )
    content_type = (upload.content_type or "").lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise AttachmentError(
            f"Unsupported content type {content_type!r} for {upload.filename!r}. "
            "Only application/pdf is accepted."
        )


async def read_upload_bytes(upload: UploadFile) -> bytes:
    """Read upload body; raise ``AttachmentError`` if the file is empty."""
    data = await upload.read()
    if not data:
        raise AttachmentError(f"Empty file: {upload.filename!r}")
    return data


class AttachmentService:
    """Upload PDFs to OpenAI or Anthropic and build multimodal user content blocks."""

    def __init__(
        self,
        *,
        openai_api_key: str | None,
        anthropic_api_key: str | None,
        primary_model: str,
    ) -> None:
        self.openai_api_key = openai_api_key
        self.anthropic_api_key = anthropic_api_key
        self.primary_model = primary_model

    @property
    def provider(self) -> str:
        """``openai`` or ``anthropic``, derived from ``PRIMARY_MODEL``."""
        return _provider_from_model(self.primary_model)

    async def upload_pdf(
        self,
        *,
        filename: str,
        data: bytes,
        provider: str | None = None,
    ) -> str:
        """Upload a PDF and return the provider file id."""
        target = provider or self.provider
        if target == "openai":
            return self._upload_openai(filename, data)
        if target == "anthropic":
            return self._upload_anthropic(filename, data)
        raise AttachmentError(f"No API key configured for provider {target!r}")

    def _upload_openai(self, filename: str, data: bytes) -> str:
        """Upload via OpenAI Files API (``purpose=user_data``)."""
        if not self.openai_api_key:
            raise AttachmentError("OPENAI_API_KEY is required to upload PDFs")
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key)
        uploaded = client.files.create(
            file=(filename, data, "application/pdf"),
            purpose="user_data",
        )
        log.info("attachment_uploaded", provider="openai", file_id=uploaded.id)
        return uploaded.id

    def _upload_anthropic(self, filename: str, data: bytes) -> str:
        """Upload via Anthropic beta Files API."""
        if not self.anthropic_api_key:
            raise AttachmentError("ANTHROPIC_API_KEY is required to upload PDFs")
        import anthropic

        client = anthropic.Anthropic(api_key=self.anthropic_api_key)
        uploaded = client.beta.files.upload(
            file=(filename, data, "application/pdf"),
        )
        log.info("attachment_uploaded", provider="anthropic", file_id=uploaded.id)
        return uploaded.id

    def build_user_content(
        self,
        *,
        text: str,
        file_ids: list[str],
        provider: str | None = None,
    ) -> str | list[dict]:
        """Return plain text or a multimodal content block list for the LLM user message."""
        target = provider or self.provider
        if not file_ids:
            return text

        if target == "openai":
            blocks: list[dict] = [{"type": "text", "text": text}]
            for file_id in file_ids:
                blocks.append(
                    {
                        "type": "file",
                        "file": {"file_id": file_id},
                    }
                )
            return blocks

        if target == "anthropic":
            blocks = []
            for file_id in file_ids:
                blocks.append(
                    {
                        "type": "document",
                        "source": {"type": "file", "file_id": file_id},
                    }
                )
            blocks.append({"type": "text", "text": text})
            return blocks

        raise AttachmentError(f"Unsupported provider for multimodal content: {target!r}")
