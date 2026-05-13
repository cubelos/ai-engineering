"""Jinja template regression tests for ``estimation/v1`` (no HTTP, no LLM).

These tests render prompts with :func:`~app.prompts.loader.render_estimation_prompt`
and assert on substrings so template edits that break the contract fail fast in CI.
"""

from app.prompts.loader import DEFAULT_ESTIMATION_PROMPT_VERSION, render_estimation_prompt
from app.schemas.estimation import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
)


def _req(
    *,
    description: str = (
        "Mobile app with login, chat and push notifications for a logistics pilot."
    ),
    project_type: ProjectType = ProjectType.MOBILE_APP,
    detail_level: DetailLevel = DetailLevel.MEDIUM,
    output_format: OutputFormat = OutputFormat.NARRATIVE,
) -> EstimationRequest:
    return EstimationRequest(
        description=description,
        project_type=project_type,
        detail_level=detail_level,
        output_format=output_format,
    )


def test_description_literal_inside_project_description_block() -> None:
    """User template wraps the full description inside ``<project_description>``."""
    desc = (
        "Mobile app with login, chat and push notifications for a logistics pilot "
        "with offline sync."
    )
    _system, user, ver = render_estimation_prompt(_req(description=desc))
    assert ver == DEFAULT_ESTIMATION_PROMPT_VERSION
    assert "<project_description>" in user
    assert "</project_description>" in user
    assert desc in user


def test_phases_table_branch_injects_column_hint_not_present_for_narrative() -> None:
    """``phases_table`` adds ``confidence_pct`` guidance; narrative branch does not."""
    sys_table, _u, _ = render_estimation_prompt(
        _req(output_format=OutputFormat.PHASES_TABLE),
    )
    sys_narrative, _u2, _ = render_estimation_prompt(
        _req(output_format=OutputFormat.NARRATIVE),
    )
    assert "confidence_pct" in sys_table
    assert "confidence_pct" not in sys_narrative


def test_detailed_branch_adds_assumption_guidance_summary_branch_omits_it() -> None:
    """``detailed`` enables the long-form assumption block; ``summary`` does not."""
    sys_detailed, _, _ = render_estimation_prompt(_req(detail_level=DetailLevel.DETAILED))
    sys_summary, _, _ = render_estimation_prompt(_req(detail_level=DetailLevel.SUMMARY))
    assert "Detailed mode:" in sys_detailed
    assert "explicit assumptions" in sys_detailed
    assert "Detailed mode:" not in sys_summary
    assert "explicit assumptions" not in sys_summary
    assert "under ~400 words" in sys_summary
    assert "under ~400 words" not in sys_detailed


def test_line_items_branch_documents_task_table_columns() -> None:
    """``line_items`` output format lists Task/Hours/Cost columns in instructions."""
    sys_li, _u, _ = render_estimation_prompt(_req(output_format=OutputFormat.LINE_ITEMS))
    assert "Task | Hours | Cost (EUR)" in sys_li
    assert "confidence_pct" not in sys_li


def test_render_returns_requested_version_token() -> None:
    """Third tuple element matches the ``version`` argument passed to the loader."""
    _s, _u, ver = render_estimation_prompt(_req(), version="v1")
    assert ver == "v1"
