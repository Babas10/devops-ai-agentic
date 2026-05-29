# Learnings

Operational notes and lessons learned from real cluster deployments.

---

## KServe requires OpenShift Service Mesh and OpenShift Serverless

### Why

KServe has two serving modes:

- **Serverless mode** (default): inference endpoints are served via Knative Serving. Knative requires OpenShift Serverless, which in turn requires OpenShift Service Mesh (Istio) for its networking layer.
- **Raw Deployment mode**: inference endpoints are standard Kubernetes Deployments. No Knative or Service Mesh required.

Even if `rawDeploymentServiceConfig: Headless` is set in the DataScienceCluster (which makes raw deployment the default for new InferenceServices), setting `serving.managementState: Managed` under `kserve` still instructs the RHOAI operator to install and manage Knative Serving — which pulls in the Service Mesh and Serverless dependencies. These are independent settings.

The RHOAI operator reports:
```
ServiceMesh operator must be installed for this component's configuration
Serverless operator must be installed for this component's configuration
```

### Install pattern

**OpenShift Service Mesh v2** — required by RHOAI 2.25.x
- Operator name: `servicemeshoperator`
- Channel: `stable`
- Namespace: `openshift-operators`
- OperatorGroup: none needed — `openshift-operators` already has the global `global-operators` OperatorGroup
- RHOAI manages the `ServiceMeshControlPlane` CR (`data-science-smcp` in `istio-system`) automatically

> **Note:** RHOAI 2.25.x does NOT support Service Mesh v3 (`servicemeshoperator3`). It is hardcoded to look for the subscription name `servicemeshoperator` and uses the SM v2 `ServiceMeshControlPlane` API. SM v2 and SM v3 conflict on Istio CRDs and cannot coexist on the same cluster. SM v3 support in RHOAI is expected in a future release.

**OpenShift Serverless**
- Operator name: `serverless-operator`
- Channel: `stable`
- Namespace: `openshift-serverless` (must be created)
- OperatorGroup: required — global scope (no `targetNamespaces`), same pattern as RHOAI

The RHOAI operator creates the `KnativeServing` CR in the `knative-serving` namespace automatically once `serving.managementState: Managed` is set and the Serverless operator is installed. No manual KnativeServing CR is needed.

---

## OLM OperatorGroup rules

- `openshift-operators` already has a global `OperatorGroup` (`global-operators`) — never create another one there or OLM will refuse to install any operator in that namespace.
- Dedicated namespaces (e.g. `openshift-gitops-operator`, `redhat-ods-operator`, `openshift-serverless`) need their own `OperatorGroup` with global scope (`spec: upgradeStrategy: Default`, no `targetNamespaces`).

---

## ArgoCD Application CRs silently disappear with kubernetes.core.k8s

`kubernetes.core.k8s` reports `changed` but the ArgoCD `Application` CR does not persist in the cluster. Use `ansible.builtin.command` with `oc apply --server --token --insecure-skip-tls-verify` instead for applying ArgoCD Application CRs from Ansible.

---

## Sealed Secrets — stable signing key across reinstalls

The controller generates a new signing key on first start if none exists. To reuse the same key across cluster reinstalls:

1. Pre-create a TLS Secret named `sealed-secrets-key` in the `sealed-secrets` namespace with label `sealedsecrets.bitnami.com/sealed-secrets-key: active` before the controller starts.
2. The controller discovers keys by this label (not by name) and uses all active keys found.
3. Set `keyrenewperiod: "0"` in the Helm values to prevent automatic rotation.
4. Store the key encrypted with Ansible Vault — see `ansible/inventory/group_vars/vault.yml.example`.

---

## KServe storage initializer — HuggingFace Hub not supported in RHOAI 2.25.x

### What doesn't work

`storageUri: hf://...` is rejected by the KServe admission webhook in RHOAI 2.25.x:

```
admission webhook "inferenceservice.kserve-webhook-server.pod-mutator" denied the request:
storage type must be one of [s3, hdfs, webhdfs]. storage type [huggingface] is not supported
```

Even if a `storage-config` Secret is present with `"type": "huggingface"`, the webhook rejects it because `huggingface` is not in the supported type list for this KServe version.

### Correct approach — let vLLM download the model directly

Skip the KServe storage initializer entirely. Set the HuggingFace model ID directly in the ServingRuntime args and pass the token as an env var. vLLM handles the download on pod startup:

```yaml
# ServingRuntime
args:
  - --model=Qwen/Qwen2.5-1.5B-Instruct   # HF model ID, not /mnt/models
  - --device=cpu
  - --dtype=bfloat16
env:
  - name: HUGGING_FACE_HUB_TOKEN
    valueFrom:
      secretKeyRef:
        name: hf-token
        key: token
  - name: HF_HOME
    value: /tmp/hf_home

# InferenceService — no storageUri, no storage.key
spec:
  predictor:
    model:
      modelFormat:
        name: vLLM
      runtime: vllm-cpu
```

The `storage-config` Secret is **not needed** with this approach. Only the `hf-token` SealedSecret is required (for the env var).

First pod startup takes ~2-3 minutes while vLLM downloads the model into `HF_HOME`. The cache is in `/tmp/hf_home` (emptyDir) so the download repeats on pod restart.

---

## Bitnami Sealed Secrets chart — OpenShift SCC incompatibility

The upstream Bitnami Sealed Secrets Helm chart hardcodes `runAsUser: 1001` and `fsGroup: 65534` in both `podSecurityContext` and `containerSecurityContext`. OpenShift's restricted SCC rejects pods with hardcoded UIDs/GIDs.

Fix: bundle the chart locally (`charts/sealed-secrets/`) and remove the `podSecurityContext` and `containerSecurityContext` blocks entirely from `templates/deployment.yaml`. OpenShift assigns a valid UID automatically.
