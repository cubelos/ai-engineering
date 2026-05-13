"""Render versioned Jinja2 prompts for the estimation API.

Templates live under ``app/prompts/estimation/<version>/`` (for example ``v1``,
``v2``) so teams can ship and compare prompt bundles without changing call sites.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas.estimation import EstimationRequest

DEFAULT_ESTIMATION_PROMPT_VERSION = "v1"

PROMPTS_DIR = Path(__file__).resolve().parent

_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
    undefined=StrictUndefined,
)


def render_estimation_prompt(
    request: EstimationRequest,
    version: str | None = None,
) -> tuple[str, str, str]:
    """Build system and user prompt strings from ``request``.

    Parameters
    ----------
    request:
        Typed fields (description, enums) passed into the template context.
    version:
        Subdirectory name under ``estimation/``. Defaults to
        :data:`DEFAULT_ESTIMATION_PROMPT_VERSION`.

    Returns
    -------
    tuple[str, str, str]
        ``(system_prompt, user_prompt, version_used)``. The third value should
        be echoed back as ``EstimationResponse.prompt_version`` so clients know
        which template bundle produced the call.
    """
    v = version or DEFAULT_ESTIMATION_PROMPT_VERSION
    ctx = {
        "description": request.description,
        "project_type": request.project_type.value,
        "project_type_human": request.project_type.value.replace("_", " "),
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
        "version": v,
    }
    system_t = _env.get_template(f"estimation/{v}/system.j2")
    user_t = _env.get_template(f"estimation/{v}/user.j2")
    return system_t.render(**ctx), user_t.render(**ctx), v
