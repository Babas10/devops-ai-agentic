"""monitor_cluster node — query Kubernetes API for unhealthy pods.

Guardrails (see docs/agent-architecture.md):
  - Layer 1 (primary):    only namespaces in WATCHED_NAMESPACES are queried.
  - Layer 2 (safety net): execute_fix re-checks before any destructive action.

WATCHED_NAMESPACES env var controls the scope (comma-separated, default: ai-agentic).
BLOCKED_PREFIXES is hardcoded and cannot be overridden by configuration.
"""

import logging
import os

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrail configuration
# ---------------------------------------------------------------------------

WATCHED_NAMESPACES: list[str] = [
    ns.strip()
    for ns in os.environ.get("WATCHED_NAMESPACES", "ai-agentic").split(",")
    if ns.strip()
]

# Hardcoded — cannot be overridden. Agent never touches system namespaces.
BLOCKED_PREFIXES = (
    "openshift-",
    "kube-",
    "kube-system",
    "redhat-",
    "rhoas-",
    "default",
)

# Pod container states that indicate a problem worth acting on
ACTIONABLE_REASONS = {
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CrashLoopBackOff",
    "OOMKilled",
}

SECRET_NOT_FOUND_MARKER = "secret"  # matched in event messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_allowed_namespace(namespace: str) -> bool:
    """Return True only if the namespace is watched and not system-owned."""
    if any(namespace.startswith(prefix) for prefix in BLOCKED_PREFIXES):
        return False
    return namespace in WATCHED_NAMESPACES


def _load_k8s_config() -> None:
    """Load in-cluster config, fall back to kubeconfig for local dev."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _alerts_from_pod(pod) -> list[dict]:
    """Extract structured alerts from a single pod object."""
    alerts = []
    namespace = pod.metadata.namespace
    pod_name = pod.metadata.name

    if not pod.status or not pod.status.container_statuses:
        return alerts

    for cs in pod.status.container_statuses:
        waiting = cs.state.waiting if cs.state else None
        if not waiting:
            continue

        reason = waiting.reason or ""
        message = waiting.message or ""

        if reason in ACTIONABLE_REASONS:
            alerts.append({
                "pod": pod_name,
                "namespace": namespace,
                "container": cs.name,
                "reason": reason,
                "message": message,
            })
        elif SECRET_NOT_FOUND_MARKER in message.lower() and "not found" in message.lower():
            alerts.append({
                "pod": pod_name,
                "namespace": namespace,
                "container": cs.name,
                "reason": "MissingSecret",
                "message": message,
            })

    return alerts


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def monitor_cluster(state: AgentState) -> dict:
    """Query Kubernetes for unhealthy pods across all watched namespaces.

    Guardrail: only namespaces in WATCHED_NAMESPACES that do not match
    BLOCKED_PREFIXES are queried. System namespaces are never observed.

    Routing:
      - alerts found  → classify_alert (picks first alert as current_alert)
      - no alerts     → END
    """
    _load_k8s_config()
    v1 = client.CoreV1Api()

    all_alerts: list[dict] = []

    for namespace in WATCHED_NAMESPACES:
        if not _is_allowed_namespace(namespace):
            logger.warning(
                "Namespace %r is blocked by guardrail — skipping.", namespace
            )
            continue

        try:
            pods = v1.list_namespaced_pod(
                namespace=namespace,
                field_selector="status.phase!=Running",
            )
        except ApiException as exc:
            logger.error(
                "Failed to list pods in namespace %r: %s", namespace, exc
            )
            continue

        for pod in pods.items:
            all_alerts.extend(_alerts_from_pod(pod))

    if not all_alerts:
        logger.info("No alerts found across watched namespaces: %s", WATCHED_NAMESPACES)

    current_alert = all_alerts[0] if all_alerts else {}

    return {
        "alerts": all_alerts,
        "current_alert": current_alert,
        "retry_count": 0,
        "node_trace": ["monitor_cluster"],
    }
