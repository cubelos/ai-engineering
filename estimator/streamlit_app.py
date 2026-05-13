"""Browser UI: build an :class:`~app.schemas.estimation.EstimationRequest` and POST it.

Runs as a separate process from the API. Reads ``ESTIMATOR_API_BASE_URL`` for the
backend base URL (defaults to ``http://localhost:8000``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import streamlit as st
from dotenv import load_dotenv

# Running ``streamlit run streamlit_app.py`` does not always add the project root
# to ``sys.path``; keep imports consistent with ``uv run pytest``.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.schemas.estimation import (  # noqa: E402 — after sys.path bootstrap
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
)

load_dotenv()

API_BASE_URL = os.getenv("ESTIMATOR_API_BASE_URL", "http://localhost:8000")
ESTIMATE_ENDPOINT = f"{API_BASE_URL.rstrip('/')}/api/v1/estimate"
STREAM_ENDPOINT = f"{API_BASE_URL.rstrip('/')}/api/v1/estimate/stream"
STREAM_FORM_ENDPOINT = f"{API_BASE_URL.rstrip('/')}/api/v1/estimate/stream-form"

st.set_page_config(page_title="Software Estimator", page_icon="📊")
st.title("Software Estimator")
st.caption(
    "Fill the form below. The service composes a versioned backend prompt from "
    "your selections — you are not writing the system prompt yourself."
)

_PROJECT_LABELS: dict[ProjectType, str] = {
    ProjectType.MOBILE_APP: "Mobile app",
    ProjectType.WEB_SAAS: "Web / SaaS",
    ProjectType.INTERNAL_TOOL: "Internal tool",
    ProjectType.DATA_PIPELINE: "Data pipeline",
}

_DETAIL_LABELS: dict[DetailLevel, str] = {
    DetailLevel.SUMMARY: "Summary",
    DetailLevel.MEDIUM: "Medium",
    DetailLevel.DETAILED: "Detailed",
}

_FORMAT_LABELS: dict[OutputFormat, str] = {
    OutputFormat.PHASES_TABLE: "Phases table",
    OutputFormat.LINE_ITEMS: "Line items",
    OutputFormat.NARRATIVE: "Narrative",
}


def _enum_picker(label: str, mapping: dict, *, default_key):
    options = list(mapping.keys())
    labels = [mapping[k] for k in options]
    ix = labels.index(mapping[default_key])
    choice_label = st.selectbox(label, labels, index=ix)
    return options[labels.index(choice_label)]


with st.form("estimation_form"):
    description = st.text_area(
        "Project description",
        height=220,
        placeholder="At least 20 characters (up to ~50k): goals, constraints, or a full transcript…",
        help="Sent as <project_description> in the Jinja user template. API max length 50,000 characters.",
    )
    col_a, col_b = st.columns(2)
    with col_a:
        project_type = _enum_picker("Project type", _PROJECT_LABELS, default_key=ProjectType.WEB_SAAS)
    with col_b:
        output_format = _enum_picker(
            "Output format", _FORMAT_LABELS, default_key=OutputFormat.NARRATIVE
        )
    detail_level = st.radio(
        "Detail level",
        options=list(DetailLevel),
        format_func=lambda d: _DETAIL_LABELS[d],
        horizontal=True,
    )
    run_validation = st.checkbox(
        "Run structural validation (score + issues)",
        value=True,
        help="Runs structural checks (sections, table sums, finish reason) on the returned markdown.",
    )
    use_stream = st.checkbox(
        "Stream tokens (SSE, same form as JSON endpoint)",
        value=False,
        help="POST /api/v1/estimate/stream-form. The UI refreshes on each token. "
        "If Redis returns a cached estimate, the API replays it in ~400-char slices. "
        "Use curl -N when testing in a terminal.",
    )
    submitted = st.form_submit_button("Generate estimation")

if submitted:
    try:
        req = EstimationRequest(
            description=description.strip(),
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
            evaluate=run_validation,
        )
    except Exception as exc:  # noqa: BLE001 — Pydantic user-facing validation
        st.error(f"Invalid input: {exc}")
    else:
        payload = req.model_dump(mode="json")
        try:
            if use_stream:
                # Update the placeholder on every token; a single ``st.markdown`` at
                # the end looks like "everything at once" because Streamlit only
                # paints after the blocking loop finishes.
                text_acc: list[str] = []
                validation_obj: dict | None = None
                event_name = "message"
                data_lines: list[str] = []
                stream_md = st.empty()
                st.caption("Streaming tokens from the API…")
                with httpx.stream(
                    "POST",
                    STREAM_FORM_ENDPOINT,
                    json=payload,
                    timeout=httpx.Timeout(120.0, connect=10.0),
                ) as r:
                    r.raise_for_status()
                    for raw in r.iter_lines():
                        line = raw.decode() if isinstance(raw, bytes) else (raw or "")
                        if line == "":
                            if data_lines:
                                joined = "\n".join(data_lines)
                                if event_name == "token":
                                    text_acc.append(joined)
                                    stream_md.markdown("".join(text_acc))
                                elif event_name == "validation":
                                    validation_obj = json.loads(joined)
                                elif event_name == "error":
                                    raise RuntimeError(joined)
                                data_lines = []
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:") :].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:") :].lstrip())
                    if data_lines:
                        joined = "\n".join(data_lines)
                        if event_name == "token":
                            text_acc.append(joined)
                            stream_md.markdown("".join(text_acc))
                        elif event_name == "validation":
                            validation_obj = json.loads(joined)
                        elif event_name == "error":
                            raise RuntimeError(joined)
                st.success("Stream finished (`stream-form`).")
                if validation_obj is not None:
                    st.metric("Validation score", f"{float(validation_obj.get('score', 0)):.2f}")
            else:
                with st.spinner("Calling the estimator service…"):
                    r = httpx.post(
                        ESTIMATE_ENDPOINT,
                        json=payload,
                        timeout=httpx.Timeout(120.0, connect=10.0),
                    )
                    r.raise_for_status()
                    body = r.json()
                    st.success(f"Prompt version: `{body.get('prompt_version', '?')}`")
                    if body.get("validation"):
                        v = body["validation"]
                        st.metric("Validation score", f"{v.get('score', 0):.2f}")
                    st.markdown(body.get("text") or "")
        except httpx.HTTPError as exc:
            ep = STREAM_FORM_ENDPOINT if use_stream else ESTIMATE_ENDPOINT
            st.error(f"Request failed (`{ep}`): {exc}")
        except (RuntimeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            st.error(f"Stream parse error: {exc}")

with st.sidebar:
    st.header("Endpoints")
    st.markdown("**Form (this UI)**")
    st.code(ESTIMATE_ENDPOINT, language="text")
    st.markdown("**Streaming (transcription demo)**")
    st.code(STREAM_ENDPOINT, language="text")
    st.markdown("**Streaming (typed form, Jinja)**")
    st.code(STREAM_FORM_ENDPOINT, language="text")
    primary = os.getenv("PRIMARY_MODEL", "gpt-4o-mini")
    fallback = os.getenv("FALLBACK_MODEL", "claude-haiku-4-5-20251001")
    st.markdown(f"**Primary model:** `{primary}`")
    st.markdown(f"**Fallback model:** `{fallback}`")
    st.markdown(f"**Cache TTL:** `{os.getenv('CACHE_TTL', '86400')}s`")
