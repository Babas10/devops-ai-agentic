"""investigate_image node — gather context for IMAGE_PULL_BACKOFF alerts.

Steps:
  1. Read the Deployment to get the current (broken) image tag.
  2. Query ReplicaSet history to find the last known-working image tag.
  3. Look up the Helm repo in the repo mapping.
  4. Call GitHub API for recent commits that touched Helm files, focusing on
     image tag changes in values.yaml / Chart.yaml / templates/.

Output stored in AgentState.investigation:
  current_image   — broken image tag currently in the Deployment
  previous_image  — last working image from rollout history (None if unavailable)
  source_repo     — GitHub repo slug for the app source
  helm_repo       — GitHub repo slug for the Helm chart
  helm_diff       — compact diff of recent Helm file changes

plan_fix receives this context and uses previous_image as the target image,
so the fix is based on rollout history rather than an LLM guess.
"""

import logging

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from agent.nodes.github import get_helm_diff, get_repos
from agent.nodes.monitor_cluster import _load_k8s_config
from agent.state import AgentState

logger = logging.getLogger(__name__)


def _get_current_image(namespace: str, deployment_name: str) -> str:
    try:
        dep = client.AppsV1Api().read_namespaced_deployment(
            name=deployment_name, namespace=namespace
        )
        containers = dep.spec.template.spec.containers or []
        return containers[0].image if containers else ""
    except ApiException as exc:
        logger.warning("Could not read deployment %s: %s", deployment_name, exc)
        return ""


def _get_previous_image(namespace: str, deployment_name: str) -> str | None:
    """Return the container image from the second-most-recent ReplicaSet.

    After a bad rollout, the newest ReplicaSet has the broken image. The
    previous one (index 1 when sorted by creation time descending) has the
    last working image.
    """
    try:
        rs_list = client.AppsV1Api().list_namespaced_replica_set(namespace=namespace)
        owned = [
            rs for rs in rs_list.items
            if any(
                ref.kind == "Deployment" and ref.name == deployment_name
                for ref in (rs.metadata.owner_references or [])
            )
        ]
        owned.sort(key=lambda rs: rs.metadata.creation_timestamp, reverse=True)
        if len(owned) >= 2:
            containers = owned[1].spec.template.spec.containers or []
            return containers[0].image if containers else None
    except ApiException as exc:
        logger.warning("Could not list ReplicaSets in %s: %s", namespace, exc)
    return None


def investigate_image(state: AgentState) -> dict:
    """Collect rollout history + Helm git context for an IMAGE_PULL_BACKOFF alert.

    Populates AgentState.investigation with image and diff data so that
    plan_fix can use the previous_image as the fix target.
    """
    alert = state.get("current_alert", {})
    namespace = alert.get("namespace", "ai-agentic")
    pod_name = alert.get("pod", "")

    # Strip the two random suffixes to get the Deployment name
    parts = pod_name.split("-")
    deployment_name = "-".join(parts[:-2]) if len(parts) > 2 else pod_name

    print(f"[investigate_image] namespace={namespace} deployment={deployment_name}")

    _load_k8s_config()

    current_image = _get_current_image(namespace, deployment_name)
    previous_image = _get_previous_image(namespace, deployment_name)
    repos = get_repos(namespace, deployment_name)
    helm_diff = get_helm_diff(repos["helm_repo"], repos["helm_path"])

    investigation = {
        "current_image": current_image,
        "previous_image": previous_image,
        "source_repo": repos["source_repo"],
        "helm_repo": repos["helm_repo"],
        "helm_diff": helm_diff,
    }

    print(
        f"[investigate_image] current={current_image!r} previous={previous_image!r} "
        f"helm_repo={repos['helm_repo']!r} diff_chars={len(helm_diff)}"
    )

    return {"investigation": investigation}
