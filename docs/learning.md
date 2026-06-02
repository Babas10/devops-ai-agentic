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

## odh-vllm-cuda-rhel9 ships with HF_HUB_OFFLINE=1

The `odh-vllm-cuda-rhel9` image is designed for air-gapped environments where models are
pre-cached. It sets `HF_HUB_OFFLINE=1` in its default environment, which tells
`huggingface_hub` to never make outbound requests.

### The misleading error

vLLM crashes at startup with a confusing message:

```
OSError: We couldn't connect to 'https://huggingface.co' to load the files,
and couldn't find them in the cached files.
```

This looks like a network error. The real cause is buried in the chained exception:

```
LocalEntryNotFoundError: Cannot find the requested files in the disk cache
and outgoing traffic has been disabled.
To enable hf.co look-ups and downloads online, set 'local_files_only' to False.
```

The network is perfectly reachable — `HF_HUB_OFFLINE=1` silently blocks all
`huggingface_hub` downloads before any network call is even attempted.

### How to diagnose

Create a debug pod using the exact image and run:

```bash
oc exec -n ai-agentic vllm-debug -- env | grep -iE "offline|HF_HUB"
# HF_HUB_OFFLINE=1
```

### Fix

Override the image default in the `ServingRuntime` env:

```yaml
env:
  - name: HF_HUB_OFFLINE
    value: "0"
```

### First-start download time

With `HF_HUB_OFFLINE=0` and `HF_HOME` pointing to an `emptyDir`, the model is downloaded
from HuggingFace on every pod start. For Qwen2.5-7B-Instruct (~14 GiB) this takes several
minutes. To avoid repeated downloads, mount a PVC at `HF_HOME` so the cache persists across
pod restarts.

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

## RHOAI vLLM CPU image has no amd64 build

`odh-vllm-cpu-rhel9` does not include an amd64 (x86_64) entry in its manifest list. On standard AWS `m6a`/`m5a` nodes the pod fails with:

```
no image found in manifest list for architecture "amd64"
```

**Workaround:** use `odh-vllm-cuda-rhel9` (which does have an amd64 build) with CPU-only mode forced via env var:

```yaml
image: registry.redhat.io/rhoai/odh-vllm-cuda-rhel9@sha256:...
env:
  - name: CUDA_VISIBLE_DEVICES
    value: ""        # hides all GPUs — vLLM falls back to CPU
args:
  - --device=cpu
  - --dtype=bfloat16
```

`CUDA_VISIBLE_DEVICES: ""` tells PyTorch/vLLM there are no CUDA devices, preventing any CUDA initialisation errors while the CUDA libraries remain unused.

However, newer vLLM versions (as shipped in `odh-vllm-cuda-rhel9`) fail to infer the device type when CUDA is hidden and no explicit device type env var is set:

```
RuntimeError: Failed to infer device type, please set the environment variable
`VLLM_LOGGING_LEVEL=DEBUG` to turn on verbose logging to help debug the issue.
```

This crash happens during argument parsing — before `--device=cpu` is even read. **Fix:** also set `VLLM_DEVICE_TYPE=cpu`:

```yaml
env:
  - name: CUDA_VISIBLE_DEVICES
    value: ""
  - name: VLLM_DEVICE_TYPE
    value: cpu
```

---

## RHOAI vLLM CPU runtime — amd64 not supported (use Ollama instead)

The `vLLM CPU ServingRuntime for KServe` template in RHOAI 2.25.x is **ppc64le/s390x only**. There is no amd64 (x86_64) CPU build.

The `odh-vllm-cuda-rhel9` image has an amd64 build but is a CUDA-compiled vLLM. Its `cpu_platform_plugin()` only activates when:
- The vLLM version string contains `"cpu"` (CPU builds), or
- The host OS is macOS

On the CUDA build with `CUDA_VISIBLE_DEVICES=""`, no platform is detected and vLLM crashes during dataclass initialisation — **before** `--device=cpu` args are even parsed. `VLLM_DEVICE_TYPE` and `VLLM_TARGET_DEVICE` env vars do not help; they are not read by the platform detection logic.

Summary of RHOAI 2.25.x LLM serving options on amd64 CPU:

| Option | amd64 | LLM / OpenAI API |
|--------|-------|-----------------|
| `odh-vllm-cuda-rhel9` | pulls | crashes — CUDA build, no CPU fallback |
| `odh-vllm-cpu-rhel9` | no amd64 build | n/a |
| `odh-openvino-model-server-rhel9` | runs | predictive only (KServe v2 protocol) |

**Solution for CPU LLM serving on amd64: use Ollama**

`docker.io/ollama/ollama` supports amd64 CPU natively, provides an OpenAI-compatible REST API (`/v1/chat/completions` with streaming), and is deployed as a plain Kubernetes `Deployment` (no KServe required).

OpenShift note: Ollama runs as root — grant `anyuid` SCC to the pod's `ServiceAccount` via `ClusterRoleBinding` to `system:openshift:scc:anyuid`.

---

## GPU scheduling — taints, tolerations and resource sizing

### Taints and tolerations

A **taint** on a node repels all pods unless they explicitly tolerate it. GPU nodes are tainted
to prevent non-GPU workloads from landing on expensive GPU capacity.

To schedule a pod on a GPU node you need **both**:

| Requirement | What it does |
|-------------|-------------|
| Resource request `nvidia.com/gpu: "1"` | Reserves one GPU for the pod; enforces exclusive access |
| Toleration for `nvidia.com/gpu: True` | Allows the pod past the taint gate |

Missing either one leaves the pod `Pending`. The typical scheduler messages are:
- `had untolerated taint {nvidia.com/gpu: True}` → toleration missing
- `Insufficient nvidia.com/gpu` → GPU already claimed by another pod

For KServe `InferenceService`, tolerations go under `spec.predictor`:

```yaml
spec:
  predictor:
    tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
```

### GPU sharing

By default, Kubernetes treats GPUs as **non-shareable** — a request for `nvidia.com/gpu: "1"`
grants a pod exclusive access to the entire physical GPU. Other pods cannot use it even if
utilisation is low.

Options to share a GPU across multiple pods:

| Method | Isolation | Works on L4 | Notes |
|--------|-----------|-------------|-------|
| **MIG** (Multi-Instance GPU) | Strong (hardware) | ❌ L4 not supported | Requires A100/H100 |
| **Time-slicing** | None (memory shared) | ✅ | Configured via GPU operator ConfigMap; performance degrades under contention |
| **MPS** (Multi-Process Service) | Partial | ✅ | Better utilisation than time-slicing, still no memory isolation |

For this project (Qwen2.5-7B-Instruct using ~14–18 GiB of the L4's 23 GiB VRAM), there is
no meaningful headroom for sharing — the GPU is essentially full with a single model loaded.

### CPU/RAM sizing on small GPU nodes

RHDP GPU sandbox nodes are typically small (e.g. 3.5 allocatable CPU, ~14 Gi allocatable RAM).
vLLM inference is **GPU-bound** — actual CPU and RAM usage is low. Default sizing templates
often request 4 CPU / 16 Gi which exceeds the node's allocatable resources.

Correct sizing for a small L4 node:

```yaml
resources:
  requests:
    cpu: "2"
    memory: 10Gi
    nvidia.com/gpu: "1"
  limits:
    cpu: "4"
    memory: 14Gi
    nvidia.com/gpu: "1"
```

The VRAM usage (model weights + KV cache) is independent of these CPU/RAM values — it lives
entirely on the GPU device.

---

## Bitnami Sealed Secrets chart — OpenShift SCC incompatibility

The upstream Bitnami Sealed Secrets Helm chart hardcodes `runAsUser: 1001` and `fsGroup: 65534` in both `podSecurityContext` and `containerSecurityContext`. OpenShift's restricted SCC rejects pods with hardcoded UIDs/GIDs.

Fix: bundle the chart locally (`charts/sealed-secrets/`) and remove the `podSecurityContext` and `containerSecurityContext` blocks entirely from `templates/deployment.yaml`. OpenShift assigns a valid UID automatically.
