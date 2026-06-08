"""verify node — poll pod status to confirm the fix worked.

After execute_fix applies a change, this node waits up to VERIFY_TIMEOUT_S
seconds (polling every POLL_INTERVAL_S) for the affected pod to reach
Running/Ready state.

Routing:
  - Pod recovered          → verified=True  → report
  - Not recovered, retries remaining → verified=False, retry_count++ → plan_fix
  - Not recovered, max retries hit   → verified=False → report (failure)

MAX_RETRIES controls how many times the full plan_fix → execute_fix → verify
cycle is attempted before giving up.
"""

import logging
import time

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from agent.nodes.monitor_cluster import _load_k8s_config
from agent.state import AgentState

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_S = 30
POLL_INTERVAL_S = 5
MAX_RETRIES = 3


def _pod_is_healthy(pod) -> bool:
    """Return True if a pod is Running and all containers are Ready."""
    if not pod.status:
        return False
    if pod.status.phase != "Running":
        return False
    if not pod.status.container_statuses:
        return False
    return all(cs.ready for cs in pod.status.container_statuses)


def _find_pods_for_alert(v1: client.CoreV1Api, namespace: str, pod_name: str) -> list:
    """Return current pods that match the original alert pod's base name.

    After a deployment rollout, the original pod is replaced by a new one
    with a different suffix. We match on the deployment name prefix.
    """
    base = "-".join(pod_name.split("-")[:-2]) if pod_name.count("-") >= 2 else pod_name
    try:
        all_pods = v1.list_namespaced_pod(namespace=namespace)
        return [p for p in all_pods.items if p.metadata.name.startswith(base)]
    except ApiException as exc:
        logger.error("Failed to list pods in %s: %s", namespace, exc)
        return []


def verify(state: AgentState) -> dict:
    """Poll pod status for up to VERIFY_TIMEOUT_S seconds after a fix.

    Returns:
      verified=True                         — pod recovered, route to report
      verified=False, retry_count incremented — not recovered, route to plan_fix
      verified=False at MAX_RETRIES         — give up, route to report
    """
    alert = state.get("current_alert", {})
    pod_name = alert.get("pod", "")
    namespace = alert.get("namespace", "ai-agentic")
    retry_count = state.get("retry_count", 0)

    print(f"[verify] checking pod={pod_name} namespace={namespace} "
          f"(attempt {retry_count + 1}/{MAX_RETRIES})")

    _load_k8s_config()
    v1 = client.CoreV1Api()

    deadline = time.time() + VERIFY_TIMEOUT_S
    while time.time() < deadline:
        pods = _find_pods_for_alert(v1, namespace, pod_name)

        if pods:
            healthy = [p for p in pods if _pod_is_healthy(p)]
            phases = [f"{p.metadata.name}={p.status.phase}" for p in pods]
            print(f"[verify] pods found: {phases} — healthy: {len(healthy)}/{len(pods)}")

            if healthy:
                print("[verify] pod recovered ✓")
                return {
                    "verified": True,
                    "retry_count": retry_count,
                    "node_trace": state.get("node_trace", []) + ["verify"],
                }
        else:
            print(f"[verify] no pods matching {pod_name!r} yet — waiting")

        time.sleep(POLL_INTERVAL_S)

    # Timeout reached
    new_retry_count = retry_count + 1
    _trace = state.get("node_trace", []) + ["verify"]
    if new_retry_count >= MAX_RETRIES:
        print(f"[verify] max retries ({MAX_RETRIES}) reached — giving up")
        return {"verified": False, "retry_count": new_retry_count, "node_trace": _trace}

    print(f"[verify] not recovered after {VERIFY_TIMEOUT_S}s — retrying "
          f"({new_retry_count}/{MAX_RETRIES})")
    return {"verified": False, "retry_count": new_retry_count, "node_trace": _trace}
