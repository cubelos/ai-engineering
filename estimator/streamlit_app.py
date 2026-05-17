"""Streamlit client for multi-turn conversational estimation."""

from __future__ import annotations

import os

import httpx
import streamlit as st
from dotenv import load_dotenv

from app.schemas.estimation import DetailLevel, OutputFormat, ProjectType

load_dotenv()

API_BASE_URL = os.getenv("ESTIMATOR_API_BASE_URL", "http://localhost:8000").rstrip("/")
SESSIONS_ENDPOINT = f"{API_BASE_URL}/api/v1/sessions"
LONG_TRANSCRIPT_WARN_CHARS = 12_000


def _ensure_session() -> str:
    if st.session_state.get("session_id"):
        return st.session_state["session_id"]
    response = httpx.post(SESSIONS_ENDPOINT, timeout=httpx.Timeout(30.0, connect=10.0))
    response.raise_for_status()
    session_id = response.json()["session_id"]
    st.session_state["session_id"] = session_id
    st.session_state.setdefault("project_metadata", {})
    return session_id


def _new_conversation() -> None:
    response = httpx.post(SESSIONS_ENDPOINT, timeout=httpx.Timeout(30.0, connect=10.0))
    response.raise_for_status()
    st.session_state["session_id"] = response.json()["session_id"]
    st.session_state["project_metadata"] = {}
    st.session_state.pop("last_result", None)


def _render_result(body: dict) -> None:
    result = body.get("result", {})
    st.markdown(f"**Prompt version:** `{body.get('prompt_version', '?')}`")
    if body.get("cached"):
        st.info("Served from cache")
    st.markdown(f"**Summary:** {result.get('summary', '')}")
    st.markdown(
        f"**Total:** {result.get('total_cost_eur', 0):,} EUR · "
        f"{result.get('total_duration_weeks', 0)} weeks · "
        f"confidence {result.get('confidence_pct', 0)}%"
    )
    phases = result.get("phases") or []
    if phases:
        st.markdown("#### Phases")
        for phase in phases:
            st.markdown(
                f"- **{phase.get('name')}** — {phase.get('duration_weeks')} wk, "
                f"{phase.get('cost_eur', 0):,} EUR: {phase.get('summary', '')}"
            )


st.set_page_config(page_title="Software Estimator", page_icon="📊")
st.title("Software Estimator")
st.caption(
    "Multi-turn estimation with session memory. Each turn refines the project "
    "scope; project facts appear in the sidebar."
)

try:
    session_id = _ensure_session()
except httpx.HTTPError as exc:
    st.error(f"Could not create session at `{SESSIONS_ENDPOINT}`: {exc}")
    st.stop()

with st.sidebar:
    st.header("Session")
    st.code(session_id, language="text")
    if st.button("Nueva conversación", use_container_width=True):
        try:
            _new_conversation()
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Could not create session: {exc}")

    st.header("Project metadata")
    metadata = st.session_state.get("project_metadata") or {}
    if metadata:
        st.json(metadata)
    else:
        st.caption("No project facts extracted yet.")

    st.header("Service")
    st.code(f"{API_BASE_URL}/api/v1/sessions/{{id}}/estimate", language="text")
    st.markdown(f"**Primary model:** `{os.getenv('PRIMARY_MODEL', 'gpt-4o-mini')}`")

with st.form("estimation_form", clear_on_submit=False):
    transcript = st.text_area(
        "Transcript / project description",
        height=200,
        placeholder="Describe the project or paste a meeting transcript for this turn…",
        help="Between 20 and 80,000 characters.",
    )
    uploaded_files = st.file_uploader(
        "Attachments (PDF)",
        type=["pdf"],
        accept_multiple_files=True,
        help="Technical specs or proposals (Camino A — sent to the LLM via Files API).",
    )
    project_type = st.selectbox(
        "Project type",
        options=[t.value for t in ProjectType],
        index=1,
    )
    detail_level = st.radio(
        "Detail level",
        options=[d.value for d in DetailLevel],
        index=1,
        horizontal=True,
    )
    output_format = st.selectbox(
        "Output format",
        options=[f.value for f in OutputFormat],
        index=0,
    )
    submitted = st.form_submit_button("Generate estimation", type="primary")

if submitted:
    transcript_text = transcript.strip()
    if len(transcript_text) < 20:
        st.error("The transcript must be at least 20 characters long.")
    else:
        if len(transcript_text) > LONG_TRANSCRIPT_WARN_CHARS:
            st.warning(
                f"This transcript is {len(transcript_text):,} characters. "
                "Very long inputs slow the model and can fail validation. "
                "For Turn 1, a focused summary (roughly 2–8k characters) is usually enough."
            )
        estimate_url = f"{API_BASE_URL}/api/v1/sessions/{session_id}/estimate"
        form_data = {
            "transcript": transcript_text,
            "project_type": project_type,
            "detail_level": detail_level,
            "output_format": output_format,
        }
        files = []
        if uploaded_files:
            for f in uploaded_files:
                files.append(
                    ("attachments", (f.name, f.getvalue(), f.type or "application/pdf"))
                )

        with st.spinner("Calling the estimator service…"):
            try:
                response = httpx.post(
                    estimate_url,
                    data=form_data,
                    files=files or None,
                    timeout=httpx.Timeout(180.0, connect=10.0),
                )
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = exc.response.text
                try:
                    detail = exc.response.json().get("detail", detail)
                except ValueError:
                    pass
                st.error(f"Service returned {status}: {detail}")
                if status == 502:
                    st.info(
                        "The model could not produce a valid estimate after several retries. "
                        "Shorten the transcript, use **Detail level: summary**, or start "
                        "**Nueva conversación** and try again."
                    )
            except httpx.HTTPError as exc:
                st.error(f"Could not reach the estimator at `{estimate_url}`: {exc}")
            else:
                st.session_state["project_metadata"] = body.get("project_metadata", {})
                st.session_state["last_result"] = body
                st.rerun()

if st.session_state.get("last_result") and not submitted:
    st.divider()
    st.subheader("Last estimation")
    _render_result(st.session_state["last_result"])
