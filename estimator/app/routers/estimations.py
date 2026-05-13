"""HTTP routes for estimation (blocking JSON and SSE streaming)."""

import asyncio
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.dependencies import get_llm_wrapper
from app.prompts.loader import render_estimation_prompt
from app.schemas.estimation import (
    EstimationRequest,
    EstimationResponse,
    StreamEstimationRequest,
    TokenUsage,
)
from app.services.evaluation import evaluate_estimation_structure
from app.services.llm_service import (
    GenerationOptions,
    LLMServiceError,
    build_system_prompt,
    generate_estimation_from_prompt_pair,
)
from app.services.llm_wrapper import LLMWrapper

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(request: EstimationRequest) -> EstimationResponse:
    """Return a structured software estimate from typed form input.

    Renders Jinja templates into separate system and user prompts, runs one
    blocking LLM call, optionally validates markdown structure, and maps the
    provider payload into :class:`~app.schemas.estimation.EstimationResponse`.
    """
    opts = GenerationOptions()
    system_prompt, user_prompt, prompt_version = render_estimation_prompt(request)

    try:
        result = generate_estimation_from_prompt_pair(
            system_prompt=system_prompt,
            user_message=user_prompt,
            opts=opts,
        )
    except LLMServiceError as exc:
        log.error("estimation_endpoint_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    validation = (
        evaluate_estimation_structure(result["estimation"], result["finish_reason"])
        if request.evaluate
        else None
    )

    u = result["usage"]
    usage = TokenUsage(
        input_tokens=u["input_tokens"],
        output_tokens=u["output_tokens"],
        total_tokens=u["total_tokens"],
        preprocessing_input_tokens=u.get("preprocessing_input_tokens", 0),
        preprocessing_output_tokens=u.get("preprocessing_output_tokens", 0),
    )

    return EstimationResponse(
        text=result["estimation"],
        prompt_version=prompt_version,
        model=result["model"],
        provider=result["provider"],
        usage=usage,
        finish_reason=result["finish_reason"],
        preprocessing=result["preprocessing"],
        extracted_requirements=result.get("extracted_requirements"),
        latency_ms=result["latency_ms"],
        validation=validation,
        cache_hit=result.get("cache_hit", False),
        cost_usd=float(result.get("cost_usd", 0.0)),
    )


@router.post("/estimate/stream")
async def create_estimation_stream(
    request: StreamEstimationRequest,
    wrapper: LLMWrapper = Depends(get_llm_wrapper),
) -> EventSourceResponse:
    """Stream an estimate as Server-Sent Events (token events + done).

    Uses :func:`~app.services.llm_service.build_system_prompt` and the raw
    ``transcription`` as the user message. Skips structural validation because
    the client consumes partial text; two-phase preprocessing is not applied
    here to avoid leaking intermediate model output.
    """
    system_prompt = build_system_prompt()

    async def event_generator() -> AsyncIterator[dict]:
        loop = asyncio.get_running_loop()
        chunks = wrapper.complete_stream(
            system_prompt=system_prompt,
            user_message=request.transcription,
            model_override=request.model,
            max_tokens=request.max_tokens,
        )

        def _next_chunk() -> str | None:
            try:
                return next(chunks)
            except StopIteration:
                return None
            except Exception as exc:  # noqa: BLE001 — surface as SSE error event
                log.error("estimate_stream_failed", error=str(exc), error_type=type(exc).__name__)
                raise

        try:
            while True:
                chunk = await loop.run_in_executor(None, _next_chunk)
                if chunk is None:
                    break
                if chunk:
                    yield {"event": "token", "data": chunk}
            yield {"event": "done", "data": "[DONE]"}
        except Exception as exc:  # noqa: BLE001
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_generator())
