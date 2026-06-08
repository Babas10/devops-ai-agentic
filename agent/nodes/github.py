"""GitHub API helpers and repo mapping loader shared by investigation nodes.

Repo mapping is loaded from a YAML file mounted via ConfigMap
(REPO_MAPPING_FILE env var, default /etc/agent/repos.yaml).

Each entry:
  namespace:    Kubernetes namespace of the deployment
  deployment:   Deployment name
  source_repo:  owner/repo for application code  (CrashLoopBackOff)
  helm_repo:    owner/repo for Helm chart         (ImagePullBackOff)
  helm_path:    path prefix to chart root in helm_repo

If the deployment is not in the mapping, the source-repo annotation on
the Deployment object is used as a fallback for both repos.
"""

import logging
import os

import requests
import yaml
from kubernetes import client
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
REPO_MAPPING_FILE = os.environ.get("REPO_MAPPING_FILE", "/etc/agent/repos.yaml")

# Helm file patterns for investigate_image
HELM_PATTERNS = ("values", "chart.yaml", "templates/")

# Source file exclusions for investigate_code
SOURCE_EXCLUDE = ("helm/", "docs/", "k8s/", ".md", ".txt", ".yaml", ".yml")

# Max chars of a file patch to include — keeps LLM context manageable
MAX_PATCH_CHARS = 300


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ---------------------------------------------------------------------------
# Repo mapping
# ---------------------------------------------------------------------------

def _load_mapping() -> list[dict]:
    """Load the repo mapping YAML file. Returns empty list on any error."""
    try:
        with open(REPO_MAPPING_FILE) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not load repo mapping from %s: %s", REPO_MAPPING_FILE, exc)
        return []


def _annotation_fallback(namespace: str, deployment_name: str) -> str:
    """Return the source-repo annotation from the Deployment, or empty string."""
    try:
        apps_v1 = client.AppsV1Api()
        dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        return (dep.metadata.annotations or {}).get("source-repo", "")
    except ApiException:
        return ""


def get_repos(namespace: str, deployment_name: str) -> dict:
    """Return {'source_repo': ..., 'helm_repo': ..., 'helm_path': ...} for a deployment.

    Lookup order:
      1. agent-repo-mapping ConfigMap (repos.yaml)
      2. source-repo annotation on the Deployment (used for both repos)
      3. Empty strings (investigation proceeds without GitHub context)
    """
    for entry in _load_mapping():
        if entry.get("namespace") == namespace and entry.get("deployment") == deployment_name:
            return {
                "source_repo": entry.get("source_repo", ""),
                "helm_repo": entry.get("helm_repo", ""),
                "helm_path": entry.get("helm_path", ""),
            }

    fallback = _annotation_fallback(namespace, deployment_name)
    return {"source_repo": fallback, "helm_repo": fallback, "helm_path": ""}


# ---------------------------------------------------------------------------
# GitHub diff helpers
# ---------------------------------------------------------------------------

def _get_commits(repo: str, n: int = 3) -> list[dict]:
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/commits",
            headers=_headers(),
            params={"per_page": n},
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception as exc:
        logger.warning("GitHub commits error for %s: %s", repo, exc)
        return []


def _get_commit_files(repo: str, sha: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/commits/{sha}",
            headers=_headers(),
            timeout=10,
        )
        return resp.json().get("files", []) if resp.status_code == 200 else []
    except Exception as exc:
        logger.warning("GitHub commit detail error for %s@%s: %s", repo, sha, exc)
        return []


def _format_file(f: dict) -> str:
    patch = f.get("patch", "")[:MAX_PATCH_CHARS]
    suffix = "..." if len(f.get("patch", "")) > MAX_PATCH_CHARS else ""
    return f"  {f['filename']} (+{f['additions']}/-{f['deletions']}):\n{patch}{suffix}"


def get_helm_diff(helm_repo: str, helm_path: str) -> str:
    """Return a compact summary of recent commits that touched Helm files."""
    if not helm_repo:
        return ""

    summaries = []
    for commit in _get_commits(helm_repo):
        sha = commit["sha"][:7]
        message = commit["commit"]["message"].split("\n")[0]
        files = _get_commit_files(helm_repo, commit["sha"])

        helm_files = [
            f for f in files
            if any(p in f["filename"].lower() for p in HELM_PATTERNS)
            or (helm_path and f["filename"].startswith(helm_path))
        ]
        if not helm_files:
            continue

        summaries.append(
            f"commit {sha}: {message}\n" + "\n".join(_format_file(f) for f in helm_files)
        )

    return "\n\n".join(summaries)


def get_code_diff(source_repo: str) -> str:
    """Return a compact summary of recent commits that touched source code files."""
    if not source_repo:
        return ""

    summaries = []
    for commit in _get_commits(source_repo):
        sha = commit["sha"][:7]
        message = commit["commit"]["message"].split("\n")[0]
        files = _get_commit_files(source_repo, commit["sha"])

        source_files = [
            f for f in files
            if not any(
                f["filename"].lower().startswith(p) or f["filename"].lower().endswith(p)
                for p in SOURCE_EXCLUDE
            )
        ]
        if not source_files:
            continue

        summaries.append(
            f"commit {sha}: {message}\n" + "\n".join(_format_file(f) for f in source_files)
        )

    return "\n\n".join(summaries)
