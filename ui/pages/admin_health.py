"""Admin · System health.

The same report ``scripts/healthcheck.py`` prints, rendered instead of printed.
One screen answers the questions that otherwise cost half an hour of debugging:
which AI provider is actually live, which model weights are already on disk,
which optional package is missing, and whether a credential is still at its
shipped default.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from core import db, model_assets
from core.config import settings
from services import health_service
from ui import session, theme

_TONE = {"ok": "success", "warn": "warning", "fail": "danger"}
_STATE = "view_health_report"


def render() -> None:
    theme.inject()
    session.require("admin")
    theme.page_header(
        "System health",
        "Every backend the portal depends on, in one view.",
        icon="🩺",
    )

    report = _report()
    counts = health_service.summarize(report)
    theme.metric_row(
        [
            theme.metric_tile("Healthy", counts["ok"], tone="success"),
            theme.metric_tile(
                "Warnings", counts["warn"], "optional or not downloaded", tone="warning"
            ),
            theme.metric_tile(
                "Failures", counts["fail"], "needs attention", tone="danger"
            ),
        ]
    )
    st.write("")

    left, right = st.columns(2, gap="large")
    with left:
        _group(report, "environment", "Environment")
        _group(report, "storage", "Storage")
        _group(report, "ai", "AI backends")
        _configuration()
    with right:
        _group(report, "models", "Model weights")
        _model_downloads(report)
        _group(report, "packages", "Python packages")

    with st.expander("Raw report (paste this into a bug report)"):
        st.json(report)


def _report() -> list[dict[str, Any]]:
    """Probe once per visit; re-probing is an explicit click, not a rerun."""
    controls = st.columns([1.1, 1.2, 2.2])
    with controls[0]:
        again = st.button("Run checks", type="primary", use_container_width=True)
    with controls[1]:
        include_ai = st.toggle(
            "Probe AI backends",
            value=True,
            help="Off skips LLM/STT/TTS, which may reach the network or load a model.",
        )

    cached = st.session_state.get(_STATE)
    if again or not cached or cached["include_ai"] != include_ai:
        with st.spinner("Probing backends…"):
            cached = {
                "include_ai": include_ai,
                "report": health_service.collect(include_ai=include_ai),
                "at": db.utc_now_iso(),
            }
        st.session_state[_STATE] = cached

    with controls[2]:
        st.caption(f"Last run {cached['at'].replace('T', ' ')}")
    return list(cached["report"])


def _group(report: list[dict[str, Any]], key: str, label: str) -> None:
    entries = [entry for entry in report if entry.get("group") == key]
    if not entries:
        return
    st.markdown(f"##### {label}")
    rows = "".join(
        f'<div class="p-kv">'
        f'<span class="k">{theme.pill(entry["name"], _TONE.get(entry["status"], "neutral"))}</span>'
        f'<span class="v" style="color:{theme.MUTED};font-weight:500">'
        f'{theme.esc(entry["detail"])}</span></div>'
        for entry in entries
    )
    theme.html_block(f'<div class="p-card">{rows}</div>')


def _configuration() -> None:
    st.markdown("##### Active configuration")
    llm = settings.llm
    model = {
        "gemini": llm.gemini_model,
        "ollama": llm.ollama_model,
        "openai_compat": llm.openai_compat_model,
    }.get(llm.provider, "—")
    theme.html_block(
        theme.kv(
            [
                ("LLM provider", llm.provider),
                ("LLM model", model),
                ("Gemini keys loaded", len(llm.gemini_api_keys)),
                ("Speech to text", settings.speech.stt_provider),
                ("Text to speech", settings.speech.tts_provider),
                ("Proctoring", "on" if settings.proctoring.enabled else "off"),
                (
                    "Object detection",
                    "on" if settings.proctoring.object_detection else "off",
                ),
                ("Session TTL", f"{settings.auth.session_ttl_hours} h"),
                ("Database", settings.database_path.name),
            ]
        )
    )
    st.caption("Everything above is an `.env` edit away — see `.env.example`.")


def _model_downloads(report: list[dict[str, Any]]) -> None:
    """Pre-fetch weights so a cold start never lands mid-interview."""
    missing = [
        entry["extra"]
        for entry in report
        if entry.get("group") == "models" and not entry["extra"].get("cached")
    ]
    if not missing:
        return
    st.caption(
        "Weights download on first use. Fetching them now avoids a stall in the "
        "middle of someone's interview."
    )
    if not st.button(
        f"Download {len(missing)} missing file(s)", use_container_width=True
    ):
        return

    progress = st.progress(0.0, text="Starting…")
    failed: list[str] = []
    for index, entry in enumerate(missing, start=1):
        progress.progress((index - 1) / len(missing), text=f"Fetching {entry['key']}…")
        if model_assets.ensure(entry["key"]) is None:
            failed.append(str(entry["key"]))
    progress.progress(1.0, text="Done")

    st.session_state.pop(_STATE, None)  # force a re-probe
    if failed:
        st.error("Could not fetch: " + ", ".join(failed) + ". Check the network.")
        return
    st.success("All model weights are on disk.")
    st.rerun()
