"""Request and response models for the estimation endpoint.

Session 4 contract: typed form-style request maps to a typed, validated
``EstimationResult`` (structured output via Instructor + Pydantic). Two model
validators enforce business rules that the LLM cannot break:

1. ``total_cost_eur`` and ``total_duration_weeks`` are aligned to phase sums
   when the model's arithmetic is off (avoids endless Instructor retries).
2. Low-confidence answers (< 30%) must declare it explicitly by starting the
   summary with ``"Out of scope:"`` — Instructor re-prompts on that violation.
"""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ProjectType(str, Enum):
    """Coarse project category sent by the cliente form."""

    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):
    """How much breakdown the prompt asks the LLM to produce."""

    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    """Rendered shape of the estimation in the model summary."""

    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class EstimationRequest(BaseModel):
    """Typed payload sent by the business backend or Streamlit form."""

    description: str = Field(
        min_length=20,
        max_length=80000,
        description="Free-text description or transcription of the project to estimate.",
    )
    project_type: ProjectType = Field(description="Coarse-grained project category.")
    detail_level: DetailLevel = Field(description="How deep the estimation should go.")
    output_format: OutputFormat = Field(description="Shape of the rendered estimation.")


# --- Structured response ----------------------------------------------------


OUT_OF_SCOPE_PREFIX = "Out of scope:"
LOW_CONFIDENCE_THRESHOLD = 30


class Phase(BaseModel):
    """One phase in the breakdown of an estimation."""

    name: str = Field(min_length=1, max_length=64)
    duration_weeks: int = Field(ge=1, le=52)
    cost_eur: int = Field(ge=0, le=1_000_000)
    summary: str = Field(min_length=10, max_length=600)


class EstimationResult(BaseModel):
    """Structured estimation. Validators enforce business rules on every parse
    (including Instructor tool output). Low-confidence scope rules still re-prompt.

    Field order is deliberate: ``phases`` comes BEFORE the totals so the LLM
    commits to the per-phase numbers first (autoregressive generation) and
    then only needs to sum them when filling the totals. Putting totals first
    leads the model to pick a round number and then back-fit phases to it,
    which it does very badly arithmetically — particularly with smaller
    models like ``gpt-4o-mini``.
    """

    summary: str = Field(min_length=10, max_length=1200)
    confidence_pct: int = Field(ge=0, le=100)
    phases: list[Phase] = Field(min_length=1, max_length=8)
    total_duration_weeks: int = Field(ge=1, le=104)
    total_cost_eur: int = Field(ge=0, le=2_000_000)

    @model_validator(mode="after")
    def phases_sum_matches_total(self) -> "EstimationResult":
        """Align totals to phase sums when the model gets arithmetic wrong.

        Instructor would otherwise re-prompt until ``max_retries`` and return 502
        on long transcripts; phase costs are treated as the source of truth.
        """
        phase_cost = sum(p.cost_eur for p in self.phases)
        phase_weeks = sum(p.duration_weeks for p in self.phases)
        if phase_cost != self.total_cost_eur:
            self.total_cost_eur = phase_cost
        if phase_weeks != self.total_duration_weeks:
            self.total_duration_weeks = phase_weeks
        return self

    @model_validator(mode="after")
    def low_confidence_requires_out_of_scope_prefix(self) -> "EstimationResult":
        if self.confidence_pct < LOW_CONFIDENCE_THRESHOLD and not self.summary.startswith(
            OUT_OF_SCOPE_PREFIX
        ):
            raise ValueError(
                f"confidence_pct < {LOW_CONFIDENCE_THRESHOLD} requires summary to "
                f"start with {OUT_OF_SCOPE_PREFIX!r}; refuse the estimation if the "
                f"description is too vague to size"
            )
        return self


class EstimationResponse(BaseModel):
    """Wraps the validated result, the prompt version that produced it, and
    whether it came from a cache (exact or semantic)."""

    result: EstimationResult
    prompt_version: str
    cached: bool = False
