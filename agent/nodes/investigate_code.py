"""investigate_code node — gather context for CRASH_LOOP alerts.

Steps:
  1. Fetch the last 20 lines of the crashed pod's logs via Kubernetes API.
  2. Look up the source repo in the repo mapping.
  3. Call GitHub API for recent commits that touched source code files
     (non-Helm, non-docs files) and return a compact diff summary.

Output stored in AgentState.investigation:
  pod_logs     — last 20 lines of container output (empty if unavailable)
  source_repo  — GitHub repo slug for the app source
  code_diff    — compact diff of recent source file changes

This node routes to search_rag → report (no execute_fix).
The report node uses pod_logs + code_diff to produce a root cause analysis
and suggested fix. No automated remediation is applied for code errors.
"""

import logging

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from agent.nodes.github import get_code_diff, get_repos
from agent.nodes.monitor_cluster import _load_k8s_config
from agent.state import AgentState

logger = logging.getLogger(__name__)

LOG_TAIL_LINES = 20


def _get_pod_logs(namespace: str, pod_name: str) -> str:
    """Fetch the last LOG_TAIL_LINES lines from a (possibly crashed) pod."""
    v1 = client.CoreV1Api()
    # Try the previous (crashed) container first, then the current one
    for previous in (True, False):
        try:
            return v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                tail_lines=LOG_TAIL_LINES,
                previous=previous,
            )
        except ApiException:
            continue
    logger.warning("Could not fetch logs for pod %s/%s", namespace, pod_name)
    return ""


def investigate_code(state: AgentState) -> dict:
    """Collect pod logs + recent code changes context for a CRASH_LOOP alert.

    Populates AgentState.investigation so that report can produce a rich
    root cause analysis instead of a generic 'pod is crashing' message.
    """
    alert = state.get("current_alert", {})
    namespace = alert.get("namespace", "ai-agentic")
    pod_name = alert.get("pod", "")

    parts = pod_name.split("-")
    deployment_name = "-".join(parts[:-2]) if len(parts) > 2 else pod_name

    print(f"[investigate_code] namespace={namespace} deployment={deployment_name}")

    _load_k8s_config()

    pod_logs = _get_pod_logs(namespace, pod_name)
    repos = get_repos(namespace, deployment_name)
    code_diff = get_code_diff(repos["source_repo"])

    investigation = {
        "pod_logs": pod_logs,
        "source_repo": repos["source_repo"],
        "code_diff": code_diff,
    }

    print(
        f"[investigate_code] log_lines={len(pod_logs.splitlines())} "
        f"source_repo={repos['source_repo']!r} diff_chars={len(code_diff)}"
    )

    return {"investigation": investigation}
