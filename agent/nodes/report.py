"""report node — use Qwen to produce a human-readable remediation summary.

This is the terminal node for all graph paths. It receives the full
accumulated state and asks Qwen to write a concise (3-5 sentence) report
covering:
  - What was detected (alert type, pod, namespace)
  - What fix was attempted (fix_plan action)
  - Whether the fix worked (verified status)
  - Any error encountered along the way

The report is printed to stdout (visible in pod logs) and stored in
AgentState.report for callers / notebook display.

If the LLM call fails, a plain-text fallback summary is generated from
state fields so the node never returns an empty report.
"""

import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agent.state import AgentState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Kubernetes operations assistant writing an incident summary.
Given the context below, write a concise report of 3-5 sentences covering:
  1. What was wrong (alert type, affected pod/namespace)
  2. What remediation was attempted
  3. Whether the remediation succeeded or failed

Write in plain prose, no bullet points, no markdown. Be direct and factual.\
"""


def _build_llm() -> ChatOpenAI:
    base_url = os.environ.get(
        "QWEN_INFERENCE_URL",
        "http://qwen-predictor.ai-agentic.svc.cluster.local:8080",
    )
    return ChatOpenAI(
        model="qwen",
        base_url=base_url.rstrip("/") + "/v1",
        api_key="unused",
        temperature=0,
        max_tokens=300,
    )


def _build_context(state: AgentState) -> str:
    alert = state.get("current_alert", {})
    alert_type = state.get("alert_type", "UNKNOWN")
    fix_plan = state.get("fix_plan", "")
    fix_result = state.get("fix_result", "")
    verified = state.get("verified", False)
    retry_count = state.get("retry_count", 0)

    lines = [
        f"Alert type: {alert_type}",
        f"Pod: {alert.get('pod', 'unknown')} in namespace {alert.get('namespace', 'unknown')}",
        f"Reason: {alert.get('reason', 'unknown')}",
        f"Message: {alert.get('message', '')}",
        f"Fix plan: {fix_plan or 'none'}",
        f"Fix result: {fix_result or 'none'}",
        f"Verified: {verified}",
        f"Retry attempts: {retry_count}",
    ]
    return "\n".join(lines)


def _fallback_report(state: AgentState) -> str:
    """Plain-text summary built from state fields — used when LLM is unavailable."""
    alert = state.get("current_alert", {})
    alert_type = state.get("alert_type", "UNKNOWN")
    verified = state.get("verified", False)
    fix_result = state.get("fix_result", "")
    retry_count = state.get("retry_count", 0)

    pod = alert.get("pod", "unknown")
    namespace = alert.get("namespace", "unknown")
    outcome = "succeeded" if verified else "failed"

    parts = [
        f"Alert type {alert_type} detected on pod {pod} in namespace {namespace}.",
    ]
    if fix_result:
        parts.append(f"Remediation attempted: {fix_result}.")
    if retry_count:
        parts.append(f"Fix was retried {retry_count} time(s).")
    parts.append(f"Overall outcome: {outcome}.")
    return " ".join(parts)


def report(state: AgentState) -> dict:
    """Generate a human-readable remediation report using Qwen.

    Falls back to a plain-text summary if the LLM call fails.
    Always returns a non-empty AgentState.report.
    """
    context = _build_context(state)
    print(f"[report] generating report for alert_type={state.get('alert_type')} "
          f"verified={state.get('verified')}")

    try:
        llm = _build_llm()
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=context),
        ])
        report_text = response.content.strip()
        print(f"[report] LLM report:\n{report_text}")
    except Exception as exc:
        print(f"[report] LLM error ({type(exc).__name__}): {exc} — using fallback")
        report_text = _fallback_report(state)
        print(f"[report] fallback report:\n{report_text}")

    return {"report": report_text}
