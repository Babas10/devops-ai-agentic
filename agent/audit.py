"""Audit log — persists every agent intervention to SQLite for the dashboard.

Called by the report node at the end of every remediation cycle.
The database file lives on the audit-data PVC (ReadWriteMany), which is also
mounted by the dashboard pod.

AUDIT_DB env var controls the path (default: /data/audit/audit.db).
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIT_DB = Path(os.environ.get("AUDIT_DB", "/data/audit/audit.db"))

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS interventions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    duration    REAL,
    namespace   TEXT,
    pod         TEXT,
    alert_type  TEXT,
    reason      TEXT,
    message     TEXT,
    node_trace  TEXT,
    fix_plan    TEXT,
    fix_result  TEXT,
    solutions   TEXT,
    retry_count INTEGER,
    verified    INTEGER,
    report      TEXT
)
"""


def _get_conn() -> sqlite3.Connection:
    AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUDIT_DB))
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def write_record(state: dict) -> None:
    """Persist one intervention record from the final agent state.

    Errors are logged but never raised — audit failure must not crash the agent.
    """
    alert = state.get("current_alert", {})
    cycle_start = state.get("cycle_start") or time.time()
    duration = round(time.time() - cycle_start, 1)

    # Store just the first line of each solution chunk (KB article header)
    solutions = state.get("solutions", [])
    solution_ids = [s.split("\n")[0][:120] for s in solutions[:3]]

    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO interventions
              (ts, duration, namespace, pod, alert_type, reason, message,
               node_trace, fix_plan, fix_result, solutions,
               retry_count, verified, report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                duration,
                alert.get("namespace", ""),
                alert.get("pod", ""),
                state.get("alert_type", "UNKNOWN"),
                alert.get("reason", ""),
                alert.get("message", ""),
                json.dumps(state.get("node_trace", [])),
                state.get("fix_plan", ""),
                state.get("fix_result", ""),
                json.dumps(solution_ids),
                state.get("retry_count", 0),
                1 if state.get("verified") else 0,
                state.get("report", ""),
            ),
        )
        conn.commit()
        conn.close()
        logger.info(
            "audit: record written — pod=%s alert_type=%s duration=%.1fs",
            alert.get("pod"),
            state.get("alert_type"),
            duration,
        )
    except Exception as exc:
        logger.error("audit: failed to write record: %s", exc)
