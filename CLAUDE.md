# CLAUDE.md — devops-ai-agentic

## Project Overview

Demo repository for an AI agentic application deployed on OpenShift using OpenShift AI.
Fully automated cluster bootstrap: Ansible installs OpenShift GitOps, then ArgoCD owns everything else via GitOps.

Owner: Red Hat employee (Etienne Dubois). Clusters are ephemeral sandbox environments from RHDP (opentlc).

---

## Architecture

```
1. Ansible bootstrap.yml
   └── Installs OpenShift GitOps operator (Subscription CR)
       └── Operator auto-creates default ArgoCD instance in openshift-gitops namespace

2. Ansible bootstrap.yml (continued)
   └── Applies the "App of Apps" ArgoCD Application CR pointing to this repo

3. ArgoCD (owns everything from here)
   ├── Sealed Secrets        — Bitnami Helm chart via ArgoCD Application
   ├── Red Hat OpenShift AI  — Subscription + DataScienceCluster CRs
   └── AI agentic app        — application workloads
```

### Install method per component

| Component | Method |
|-----------|--------|
| OpenShift GitOps (ArgoCD) | Ansible `Subscription` CR → operator auto-creates default ArgoCD instance |
| Sealed Secrets | ArgoCD `Application` → Bitnami Helm chart |
| Red Hat OpenShift AI | ArgoCD `Application` → `Subscription` + `DataScienceCluster` CRs |
| AI agentic app | ArgoCD `Application` → app manifests in `k8s/` |

---

## Repository Structure (target)

```
devops-ai-agentic/
├── CLAUDE.md
├── README.md
├── .gitignore
├── ansible/
│   ├── inventory/
│   │   ├── localhost.yml           # Localhost inventory (modules connect directly to OCP API)
│   │   └── group_vars/
│   │       └── all.yml             # Non-sensitive defaults (namespaces, operator channel, etc.)
│   ├── playbooks/
│   │   └── bootstrap.yml           # Single playbook: installs GitOps operator + applies app-of-apps
│   └── requirements.yml            # Ansible Galaxy collections (kubernetes.core)
├── k8s/
│   ├── argocd/
│   │   └── app-of-apps.yaml        # Root ArgoCD Application pointing to k8s/apps/
│   ├── apps/
│   │   ├── sealed-secrets.yaml     # ArgoCD Application — Bitnami Helm chart (no local manifests needed)
│   │   ├── rhoai.yaml              # ArgoCD Application for RHOAI operator + DSC
│   │   └── ai-agentic.yaml         # ArgoCD Application for the demo app (future)
│   ├── rhoai/                      # RHOAI Subscription + DataScienceCluster manifests
│   └── ai-agentic/                 # Application workload manifests (future)
└── docs/
    └── bootstrap.md                # Bootstrap architecture and usage guide
```

---

## Security Rules — CRITICAL

**Never commit to git:**
- OpenShift credentials (username/password, kubeadmin tokens)
- SealedSecrets private keys (only the public key may be committed)
- Any `*.kubeconfig` or `kubeconfig` files
- Ansible vault passwords or plain-text vault files
- Any file matching patterns in `.gitignore`

**Credential handling pattern:**
- Cluster API URL, username, and password are passed at runtime via env vars or Ansible extra-vars
- Example: `ansible-playbook ansible/playbooks/bootstrap.yml -e ocp_api_url=$OCP_API -e ocp_password=$OCP_PASS`
- Use `ansible-vault` for any secrets that must live in the repo (encrypted blobs only)
- SealedSecrets private key is generated once per cluster and stored in a local secure vault — never in git

---

## Cluster Provisioning Pattern

Each RHDP sandbox cluster has a unique URL and credentials. The bootstrap playbook must be cluster-agnostic:

```bash
export OCP_API=https://api.ocp.<id>.sandbox<n>.opentlc.com:6443
export OCP_USER=admin
export OCP_PASS=<password>

ansible-playbook ansible/playbooks/bootstrap.yml \
  -e ocp_api_url=$OCP_API \
  -e ocp_username=$OCP_USER \
  -e ocp_password=$OCP_PASS
```

The console URL follows the pattern: `https://console-openshift-console.apps.ocp.<id>.sandbox<n>.opentlc.com`

---

## Key Tools & Versions

- Ansible + `kubernetes.core` collection
- `kubeseal` CLI (for SealedSecrets)
- `oc` CLI (OpenShift client)
- OpenShift 4.x (RHDP sandbox)
- Red Hat OpenShift AI (RHOAI) operator
- OpenShift GitOps (ArgoCD) operator — default instance in `openshift-gitops` namespace
- Bitnami Sealed Secrets (deployed via ArgoCD Helm)

---

## Development Conventions

- Ansible bootstrap is minimal — only what ArgoCD cannot install itself
- Playbooks are idempotent — safe to re-run
- ArgoCD uses the App of Apps pattern: one root Application manages all child Applications
- Kustomize or plain YAML for K8s manifests; ArgoCD Helm plugin for Helm-based apps
- Keep `ansible/` and `k8s/` concerns separate — Ansible bootstraps, ArgoCD owns ongoing state
- Secrets in the repo use Ansible Vault (encrypted blobs only, never plaintext)
