"""Agent Audit Dashboard — Streamlit app.

Reads intervention records from SQLite (written by agent/audit.py) and
renders each one as an expandable card, newest first.

AUDIT_DB env var controls the database path (default: /data/audit/audit.db).
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st

AUDIT_DB = Path(os.environ.get("AUDIT_DB", "/data/audit/audit.db"))

st.set_page_config(
    page_title="Agent Audit Dashboard",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=15)
def load_interventions(limit: int = 200) -> list[dict]:
    if not AUDIT_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(AUDIT_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM interventions ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        st.error(f"Could not read audit database: {exc}")
        return []


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def status_label(verified: int) -> str:
    return "FIXED" if verified else "FAILED"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Agent Audit Dashboard")

header_col, refresh_col = st.columns([5, 1])
with refresh_col:
    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

interventions = load_interventions()

if not interventions:
    st.info(
        "No interventions recorded yet. "
        "The agent writes here at the end of each remediation cycle."
    )
    if not AUDIT_DB.exists():
        st.caption(f"Database not found at `{AUDIT_DB}`")
    st.stop()

with header_col:
    st.caption(
        f"{len(interventions)} intervention(s) on record. "
        f"Last: {fmt_ts(interventions[0]['ts'])}. "
        "Cache TTL: 15 s — click Refresh to reload immediately."
    )

st.divider()

# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

for row in interventions:
    node_trace: list[str] = json.loads(row.get("node_trace") or "[]")
    solutions: list[str] = json.loads(row.get("solutions") or "[]")
    alert_type = row.get("alert_type") or "UNKNOWN"
    verified = row.get("verified", 0)
    pod = row.get("pod") or "unknown"
    namespace = row.get("namespace") or "—"
    status = status_label(verified)

    expander_label = (
        f"{fmt_ts(row['ts'])}  |  {alert_type}  |  "
        f"{namespace}/{pod}  |  {status}"
    )

    with st.expander(expander_label, expanded=False):

        # ---- Top metrics row ------------------------------------------------
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Duration", f"{row.get('duration') or 0:.1f} s")
        m2.metric("Namespace", namespace)
        m3.metric("Retries", row.get("retry_count") or 0)
        m4.metric("Status", status)

        st.divider()

        # ---- Alert ----------------------------------------------------------
        st.subheader("Alert")
        st.write(f"**Type:** `{alert_type}`")
        st.write(f"**Reason:** {row.get('reason') or '—'}")
        st.write(f"**Message:** {row.get('message') or '—'}")

        # ---- Node trace -----------------------------------------------------
        st.subheader("Node execution trace")
        if node_trace:
            st.code(" → ".join(node_trace), language=None)
        else:
            st.caption("No trace recorded.")

        # ---- Fix plan -------------------------------------------------------
        fix_plan = row.get("fix_plan") or ""
        if fix_plan:
            st.subheader("Fix plan")
            try:
                st.json(json.loads(fix_plan))
            except Exception:
                st.code(fix_plan)

        # ---- Fix result -----------------------------------------------------
        fix_result = row.get("fix_result") or ""
        if fix_result:
            st.subheader("Fix result")
            st.code(fix_result, language=None)

        # ---- RAG runbook chunks ---------------------------------------------
        if solutions:
            st.subheader("RAG runbook chunks consulted")
            for chunk in solutions:
                st.write(f"- {chunk}")

        # ---- Agent report ---------------------------------------------------
        report_text = row.get("report") or ""
        if report_text:
            st.subheader("Agent report")
            st.info(report_text)
