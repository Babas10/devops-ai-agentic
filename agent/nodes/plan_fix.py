"""plan_fix node — use Qwen to produce a structured JSON fix plan.

Given the current alert, its classification, and the RAG runbook chunks,
asks Qwen to produce a single JSON object describing what action to take.

Supported actions (consumed by execute_fix):
  patch_deployment_image  — update a deployment's container image
  create_secret           — create a missing Kubernetes secret
  patch_secret_ref        — update a deployment's secret reference name

Output schema stored in AgentState.fix_plan (JSON string):
  {"action": "patch_deployment_image", "target": "<deployment>", "params": {"image": "<image:tag>"}}
  {"action": "create_secret",          "target": "<secret>",     "params": {"data": {"<key>": "<value>"}}}
  {"action": "patch_secret_ref",       "target": "<deployment>", "params": {"secret_name": "<name>"}}

If the LLM returns invalid JSON it retries once with an explicit correction
prompt before giving up and storing an empty fix_plan.
"""

import json
import logging
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agent.state import AgentState

logger = logging.getLogger(__name__)

SUPPORTED_ACTIONS = {"patch_deployment_image", "create_secret", "patch_secret_ref"}

SYSTEM_PROMPT = """\
You are a Kubernetes remediation planner. Given an alert and runbook context, \
output a single JSON fix plan — nothing else, no explanation, no markdown fences.

Supported actions:
  patch_deployment_image  — fix a wrong or missing image
  create_secret           — create a missing Kubernetes secret
  patch_secret_ref        — fix a deployment that references the wrong secret name

Output schema (pick the most appropriate action):
  {"action": "patch_deployment_image", "target": "<deployment-name>", "params": {"image": "<registry/image:tag>"}}
  {"action": "create_secret",          "target": "<secret-name>",     "params": {"data": {"<key>": "<placeholder>"}}}
  {"action": "patch_secret_ref",       "target": "<deployment-name>", "params": {"secret_name": "<correct-secret-name>"}}

Rules:
- Use the pod name from the alert as a hint for the deployment name (strip the random suffix).
- If the secret name is unknown use "PLACEHOLDER" as the value.
- Output valid JSON only. No prose.\
"""

RETRY_PROMPT = """\
Your previous response was not valid JSON. Output only the JSON object, \
no explanation, no markdown fences, no extra text.\
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
        max_tokens=200,
    )


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from text, stripping markdown fences."""
    # Strip ```json ... ``` fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _validate(plan: dict) -> bool:
    return (
        isinstance(plan, dict)
        and plan.get("action") in SUPPORTED_ACTIONS
        and isinstance(plan.get("target"), str)
        and isinstance(plan.get("params"), dict)
    )


def _build_user_message(state: AgentState) -> str:
    alert = state.get("current_alert", {})
    alert_type = state.get("alert_type", "UNKNOWN")
    solutions = state.get("solutions", [])

    runbook = "\n\n".join(solutions[:2]) if solutions else "No runbook available."

    return (
        f"Alert type: {alert_type}\n"
        f"Pod: {alert.get('pod', 'unknown')} in namespace {alert.get('namespace', 'unknown')}\n"
        f"Reason: {alert.get('reason', '')}\n"
        f"Message: {alert.get('message', '')}\n\n"
        f"Runbook context:\n{runbook}"
    )


def plan_fix(state: AgentState) -> dict:
    """Ask Qwen for a structured JSON fix plan based on alert + RAG context.

    Retries once if the response is not valid JSON.
    Stores the JSON string in AgentState.fix_plan, or empty string on failure.
    """
    llm = _build_llm()
    user_msg = _build_user_message(state)
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_msg)]

    for attempt in range(2):
        try:
            response = llm.invoke(messages)
            raw = response.content.strip()
            print(f"[plan_fix] attempt {attempt + 1} raw response: {raw[:300]}")

            plan = _extract_json(raw)
            if plan and _validate(plan):
                fix_plan = json.dumps(plan)
                print(f"[plan_fix] valid plan: {fix_plan}")
                return {"fix_plan": fix_plan}

            print(f"[plan_fix] attempt {attempt + 1} invalid — retrying")
            messages.append(HumanMessage(content=RETRY_PROMPT))

        except Exception as exc:
            print(f"[plan_fix] LLM error ({type(exc).__name__}): {exc}")
            break

    logger.error("plan_fix failed to produce a valid plan after retries")
    return {"fix_plan": ""}
