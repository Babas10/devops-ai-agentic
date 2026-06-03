"""classify_alert node — use Qwen to classify a raw alert into a canonical type.

Sends the alert reason + message to Qwen via the KServe OpenAI-compatible
endpoint and extracts one of three labels:

  IMAGE_PULL_BACKOFF  — wrong image tag or missing image pull secret
  MISSING_SECRET      — pod references a secret that does not exist
  UNKNOWN             — anything else; no automated fix available

QWEN_INFERENCE_URL env var must point to the KServe predictor service,
e.g. http://qwen-predictor.ai-agentic.svc.cluster.local
"""

import logging
import os
import re

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from agent.state import AgentState

logger = logging.getLogger(__name__)

VALID_TYPES = {"IMAGE_PULL_BACKOFF", "MISSING_SECRET", "UNKNOWN"}

SYSTEM_PROMPT = """\
You are a Kubernetes alert classifier. Your only job is to read a pod alert and
return exactly one of these labels — nothing else, no explanation:

  IMAGE_PULL_BACKOFF  — the pod cannot pull its container image (wrong tag, missing pull secret, registry unreachable)
  MISSING_SECRET      — the pod references a Kubernetes Secret that does not exist
  UNKNOWN             — anything else

Reply with the label only. Do not add punctuation, quotes, or any other text.\
"""

# Fast-path keyword rules applied before calling the LLM.
# Avoids a network round-trip for unambiguous cases.
_KEYWORD_RULES: list[tuple[set[str], str]] = [
    ({"imagepullbackoff", "errimagepull", "pull access denied", "manifest unknown"}, "IMAGE_PULL_BACKOFF"),
    ({"secret", "not found", "createcontainerconfigerror"}, "MISSING_SECRET"),
]


def _classify_by_keyword(reason: str, message: str) -> str | None:
    """Return a type if keywords unambiguously match, else None."""
    text = f"{reason} {message}".lower()
    for keywords, label in _KEYWORD_RULES:
        if all(kw in text for kw in keywords):
            return label
    return None


def _build_llm() -> ChatOpenAI:
    base_url = os.environ.get("QWEN_INFERENCE_URL", "http://qwen-predictor.ai-agentic.svc.cluster.local")
    return ChatOpenAI(
        model="qwen",
        base_url=base_url.rstrip("/") + "/v1",
        api_key="unused",
        temperature=0,
        max_tokens=20,
    )


def classify_alert(state: AgentState) -> dict:
    """Classify the current alert into a canonical type using Qwen.

    Strategy:
      1. Fast-path: check unambiguous keyword patterns (no LLM call needed).
      2. LLM-path: send reason + message to Qwen and parse the label.
      3. Fallback: default to UNKNOWN on any error or unexpected response.

    Routing:
      - UNKNOWN          → report (no automated fix available)
      - Any other type   → search_rag
    """
    alert = state.get("current_alert", {})
    reason = alert.get("reason", "")
    message = alert.get("message", "")

    # 1. Fast-path keyword check
    fast = _classify_by_keyword(reason, message)
    if fast:
        logger.info("classify_alert fast-path: %s (reason=%r)", fast, reason)
        return {"alert_type": fast}

    # 2. LLM classification
    try:
        llm = _build_llm()
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"reason: {reason}\nmessage: {message}"),
        ])
        raw = response.content.strip().upper()

        # Extract the first matching label in case the model adds stray text
        match = re.search(r"IMAGE_PULL_BACKOFF|MISSING_SECRET|UNKNOWN", raw)
        alert_type = match.group(0) if match else "UNKNOWN"

    except Exception as exc:
        logger.error("classify_alert LLM call failed: %s", exc)
        alert_type = "UNKNOWN"

    logger.info("classify_alert result: %s (reason=%r)", alert_type, reason)
    return {"alert_type": alert_type}
