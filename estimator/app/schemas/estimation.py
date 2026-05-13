"""Pydantic models for estimation HTTP APIs and internal pipeline types.

``EstimationRequest`` / ``EstimationResponse`` are the public contract for
``POST /api/v1/estimate``. Additional literals and models support the streaming
route, canonical example formatting, and post-generation validation.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

PreprocessingMode = Literal["none", "inline_cleaning", "two_phase"]
ExampleFormat = Literal["markdown", "json", "narrative"]


class ProjectType(str, Enum):
    """Coarse project category injected into prompt templates."""

    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):
    """Requested depth of the written estimate."""

    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    """Requested shape of the narrative output (markdown conventions)."""

    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class TokenUsage(BaseModel):
    """Token counts returned by the provider (including optional preprocessing)."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    preprocessing_input_tokens: int = 0
    preprocessing_output_tokens: int = 0


class StructureCheck(BaseModel):
    """Heuristic structural score for generated estimation markdown."""

    has_title: bool
    has_breakdown_table: bool
    has_totals_section: bool
    has_team_section: bool
    has_duration_section: bool
    declared_total_hours: int | None
    sum_row_hours: int | None
    hours_match: bool | None
    declared_total_cost: float | None
    sum_row_cost: float | None
    cost_match: bool | None
    finish_reason_ok: bool
    score: float
    issues: list[str]


class EstimationRequest(BaseModel):
    """Input for ``POST /api/v1/estimate`` — product parameters, not a raw system prompt."""

    description: str = Field(
        ...,
        min_length=20,
        max_length=50_000,
        description="Project brief or long transcript (upper bound 50k characters).",
    )
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
    evaluate: bool = Field(
        default=True,
        description="When true, run regex/parse validation on the model output.",
    )


class EstimationResponse(BaseModel):
    """Successful estimation payload returned to HTTP clients."""

    text: str = Field(..., description="Generated estimation (markdown/plain text)")
    prompt_version: str = Field(
        ...,
        description="Template bundle identifier (matches rendered ``estimation/<id>/``).",
    )
    model: str = Field(..., description="Resolved model name after routing")
    provider: str = Field(..., description="Provider id (e.g. openai, anthropic)")
    usage: TokenUsage
    finish_reason: str = Field(..., description="Provider stop reason")
    preprocessing: PreprocessingMode = Field(
        default="none",
        description="Preprocessing mode recorded for the call (template path uses ``none``).",
    )
    extracted_requirements: str | None = Field(
        default=None,
        description="Populated only when a two-phase transcript pipeline is used.",
    )
    latency_ms: int = Field(..., description="Wall-clock server latency in milliseconds")
    validation: StructureCheck | None = Field(
        default=None,
        description="Present when ``evaluate`` was true on the request",
    )
    cache_hit: bool = Field(default=False, description="Whether the response came from Redis")
    cost_usd: float = Field(default=0.0, description="Estimated USD spend for the call")


class StreamEstimationRequest(BaseModel):
    """Input for ``POST /api/v1/estimate/stream`` — long transcription plus optional knobs."""

    transcription: str = Field(..., min_length=50, description="Meeting or brief text to estimate")
    model: str | None = Field(default=None, description="Override primary/fallback routing target")
    max_tokens: int = Field(default=4000, gt=0, le=16000)
