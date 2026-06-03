"""execute_fix node — execute the structured fix plan against the Kubernetes API.

Parses AgentState.fix_plan (JSON string produced by plan_fix) and dispatches
to the appropriate Kubernetes API call.

Supported actions:
  patch_deployment_image  — PATCH apps/v1 Deployment, update container image
  create_secret           — POST core/v1 Secret (Opaque)
  patch_secret_ref        — PATCH apps/v1 Deployment, fix secretKeyRef name

Guardrail (Layer 2 — see docs/agent-architecture.md):
  Before executing any action, the target namespace is re-checked against
  BLOCKED_PREFIXES. This is a safety net independent of the monitor_cluster
  guardrail, protecting against any future code path that bypasses it.

fix_result is set to a human-readable status string.
Kubernetes API errors are caught and stored — never propagated as exceptions.
"""

import json
import logging

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from agent.nodes.monitor_cluster import BLOCKED_PREFIXES, _load_k8s_config
from agent.state import AgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guardrail helper
# ---------------------------------------------------------------------------

def _namespace_allowed(namespace: str) -> bool:
    if any(namespace.startswith(p) for p in BLOCKED_PREFIXES):
        logger.error("execute_fix BLOCKED: namespace %r matches blocked prefix", namespace)
        return False
    return True


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _patch_deployment_image(namespace: str, deployment: str, image: str) -> str:
    """Update the first container image in a Deployment."""
    apps_v1 = client.AppsV1Api()
    body = {"spec": {"template": {"spec": {"containers": [{"name": deployment, "image": image}]}}}}
    try:
        apps_v1.patch_namespaced_deployment(
            name=deployment,
            namespace=namespace,
            body=body,
        )
        return f"patched deployment/{deployment} image to {image}"
    except ApiException as exc:
        return f"ERROR patching deployment/{deployment}: {exc.status} {exc.reason}"


def _create_secret(namespace: str, name: str, data: dict) -> str:
    """Create an Opaque Secret with the given string data."""
    core_v1 = client.CoreV1Api()
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        type="Opaque",
        string_data={k: str(v) for k, v in data.items()},
    )
    try:
        core_v1.create_namespaced_secret(namespace=namespace, body=secret)
        return f"created secret/{name}"
    except ApiException as exc:
        if exc.status == 409:
            return f"secret/{name} already exists — no action taken"
        return f"ERROR creating secret/{name}: {exc.status} {exc.reason}"


def _patch_secret_ref(namespace: str, deployment: str, secret_name: str) -> str:
    """Update all secretKeyRef references in a Deployment to use a new secret name."""
    apps_v1 = client.AppsV1Api()
    try:
        dep = apps_v1.read_namespaced_deployment(name=deployment, namespace=namespace)
    except ApiException as exc:
        return f"ERROR reading deployment/{deployment}: {exc.status} {exc.reason}"

    patched = False
    for container in dep.spec.template.spec.containers or []:
        for env_var in container.env or []:
            if env_var.value_from and env_var.value_from.secret_key_ref:
                env_var.value_from.secret_key_ref.name = secret_name
                patched = True

    if not patched:
        return f"no secretKeyRef found in deployment/{deployment} — nothing to patch"

    try:
        apps_v1.replace_namespaced_deployment(name=deployment, namespace=namespace, body=dep)
        return f"patched deployment/{deployment} secretKeyRef → {secret_name}"
    except ApiException as exc:
        return f"ERROR patching deployment/{deployment}: {exc.status} {exc.reason}"


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def execute_fix(state: AgentState) -> dict:
    """Execute the fix plan from AgentState.fix_plan against the Kubernetes API.

    Guardrail: target namespace is re-checked against BLOCKED_PREFIXES before
    any destructive action — independent of the monitor_cluster guardrail.

    fix_result is always set (success message or error string).
    No exceptions are propagated — verify node decides whether to retry.
    """
    fix_plan_str = state.get("fix_plan", "")
    if not fix_plan_str:
        return {"fix_result": "ERROR: empty fix_plan — plan_fix did not produce a valid plan"}

    try:
        plan = json.loads(fix_plan_str)
    except json.JSONDecodeError as exc:
        return {"fix_result": f"ERROR: invalid fix_plan JSON: {exc}"}

    action = plan.get("action", "")
    target = plan.get("target", "")
    params = plan.get("params", {})
    namespace = state.get("current_alert", {}).get("namespace", "ai-agentic")

    print(f"[execute_fix] action={action} target={target} namespace={namespace} params={params}")

    # Layer 2 guardrail
    if not _namespace_allowed(namespace):
        return {"fix_result": f"ERROR: namespace {namespace!r} is blocked by guardrail"}

    _load_k8s_config()

    if action == "patch_deployment_image":
        image = params.get("image", "")
        if not image or image == "PLACEHOLDER":
            result = "ERROR: image is missing or PLACEHOLDER — cannot patch"
        else:
            result = _patch_deployment_image(namespace, target, image)

    elif action == "create_secret":
        data = params.get("data", {})
        if not data:
            result = "ERROR: secret data is empty"
        elif any(str(v) == "PLACEHOLDER" for v in data.values()):
            result = "ERROR: secret data contains PLACEHOLDER — cannot create secret with unknown values"
        else:
            result = _create_secret(namespace, target, data)

    elif action == "patch_secret_ref":
        secret_name = params.get("secret_name", "")
        if not secret_name or secret_name == "PLACEHOLDER":
            result = "ERROR: secret_name is missing or PLACEHOLDER"
        else:
            result = _patch_secret_ref(namespace, target, secret_name)

    else:
        result = f"ERROR: unknown action {action!r}"

    print(f"[execute_fix] result: {result}")
    return {"fix_result": result}
