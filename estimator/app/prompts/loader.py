"""Jinja2 loader for versioned prompt templates.

Layout: ``app/prompts/<use_case>/<version>/<role>.j2``. Uses ``StrictUndefined`` so
missing template variables fail at render time.
"""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas.estimation import EstimationRequest
from app.services.sessions import ProjectMetadata

_BASE_DIR = Path(__file__).resolve().parent

_env = Environment(
    loader=FileSystemLoader(_BASE_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
    keep_trailing_newline=True,
)


def _form_context(request: EstimationRequest) -> dict:
    """Shared template variables from a transactional ``EstimationRequest``."""
    return {
        "description": request.description,
        "project_type": request.project_type.value,
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
    }


def render_estimation_prompt(
    request: EstimationRequest,
    version: str = "v1",
    *,
    project_metadata: ProjectMetadata | None = None,
) -> tuple[str, str]:
    """Render system and user prompts for the stateless estimation endpoint."""
    context = {
        **_form_context(request),
        "project_metadata": project_metadata or ProjectMetadata(),
    }
    system = _env.get_template(f"estimation/{version}/system.j2").render(**context)
    user = _env.get_template(f"estimation/{version}/user.j2").render(**context)
    return system, user


def render_session_estimation_system(
    *,
    project_type: str,
    detail_level: str,
    output_format: str,
    project_metadata: ProjectMetadata,
    version: str = "v1",
) -> str:
    """Render the system prompt for a multi-turn session turn."""
    context = {
        "project_type": project_type,
        "detail_level": detail_level,
        "output_format": output_format,
        "project_metadata": project_metadata,
    }
    return _env.get_template(f"estimation/{version}/system.j2").render(**context)


def render_session_user_text(
    *,
    transcript: str,
    project_type: str,
    version: str = "v1",
) -> str:
    """Render the text portion of a session user turn (attachments are separate blocks)."""
    return _env.get_template(f"estimation/{version}/session_user.j2").render(
        transcript=transcript,
        project_type=project_type,
    )


def render_metadata_extractor_prompt(
    *,
    current_metadata: ProjectMetadata,
    user_turn: str,
    assistant_turn: str,
    version: str = "v1",
) -> str:
    """Render the LLM extractor prompt for updating project metadata."""
    return _env.get_template(f"metadata/{version}/extractor.j2").render(
        current_metadata_json=current_metadata.model_dump_json(indent=2),
        user_turn=user_turn,
        assistant_turn=assistant_turn,
    )
