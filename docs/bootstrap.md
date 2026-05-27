# Bootstrap Architecture

## Overview

The bootstrap process brings a fresh OpenShift cluster from zero to a fully GitOps-managed state in two phases. Ansible does the minimum required to get ArgoCD running; ArgoCD then owns everything else.

---

## Phases

### Phase 1 — Ansible

Ansible connects directly to the OpenShift API (no kubeconfig file written to disk) and performs three steps:

1. **Authenticate** — hits the OpenShift OAuth server directly (`/oauth/authorize`) with basic auth to get a short-lived Bearer token; no kubeconfig is written to disk
2. **Install OpenShift GitOps operator** — applies an OLM `Subscription` CR; the operator automatically creates a default ArgoCD instance in the `openshift-gitops` namespace
3. **Apply the App of Apps** — applies the root ArgoCD `Application` CR and revokes the token via `DELETE /oauth/token/<token>`

```
[Ansible]
    │
    ├─ uri: GET /oauth/authorize (basic auth → Bearer token)
    │
    ├─ Subscription: openshift-gitops-operator
    │     └─ OLM creates ArgoCD instance in openshift-gitops ns
    │           └─ wait until phase == Available
    │
    ├─ Application: app-of-apps (k8s/argocd/ → k8s/apps/)
    │
    └─ k8s_auth revoke
```

### Phase 2 — ArgoCD (GitOps)

Once the App of Apps Application is applied, ArgoCD reconciles the repo and manages all subsequent state. No further Ansible involvement is needed.

```
[ArgoCD — app-of-apps]
    │  source: k8s/apps/
    │
    ├─ Application: sealed-secrets
    │     └─ Helm chart: bitnami-labs/sealed-secrets
    │           namespace: sealed-secrets
    │
    ├─ Application: rhoai
    │     └─ source: k8s/rhoai/
    │           ├─ Namespace
    │           ├─ OperatorGroup
    │           ├─ Subscription: rhods-operator
    │           └─ DataScienceCluster: default-dsc
    │                 (ArgoCD retries until RHOAI CRD is available)
    │
    └─ Application: ai-agentic  (future)
          └─ source: k8s/ai-agentic/
```

---

## Prerequisites

Install these tools locally before running the bootstrap:

| Tool | Purpose |
|------|---------|
| `ansible` | Run the bootstrap playbook |
| `kubernetes.core` collection | Ansible modules for K8s/OCP |
| `oc` or `kubectl` | Optional — useful for debugging |
| `kubeseal` | Encrypt secrets with Sealed Secrets (post-bootstrap) |

Install Ansible collections:
```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

---

## Running the Bootstrap

Credentials are always passed at runtime — never stored in the repo.

```bash
export OCP_API=https://api.ocp.<id>.sandbox<n>.opentlc.com:6443
export OCP_USER=admin
export OCP_PASS=<password>

ansible-playbook ansible/playbooks/bootstrap.yml \
  -e ocp_api_url=$OCP_API \
  -e ocp_username=$OCP_USER \
  -e ocp_password=$OCP_PASS
```

You can also run only specific phases using tags:

```bash
# Only install the GitOps operator (skip App of Apps)
ansible-playbook ansible/playbooks/bootstrap.yml ... --tags gitops

# Only apply the App of Apps (ArgoCD must already be running)
ansible-playbook ansible/playbooks/bootstrap.yml ... --tags argocd-apps
```

---

## Configuration

Non-sensitive defaults live in `ansible/group_vars/all.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `gitops_operator_channel` | `latest` | OLM channel for the GitOps operator |
| `gitops_argocd_namespace` | `openshift-gitops` | Namespace where ArgoCD runs |
| `gitops_repo_url` | _(set your repo)_ | Git repo ArgoCD will watch |
| `gitops_repo_revision` | `main` | Branch/tag to track |
| `gitops_app_of_apps_path` | `k8s/argocd` | Path to the root Application manifest |

---

## Security Notes

- Credentials are passed as runtime variables and never written to disk
- The Ansible API token is revoked at the end of the playbook
- No kubeconfig file is created during bootstrap
- Sealed Secrets private keys are stored locally or in a secure vault — never in git
- Any secrets that must live in the repo are encrypted with Ansible Vault
