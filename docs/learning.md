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

## RHOAI Connection resources and model storage

### Storage options overview

RHOAI supports three ways to store model artifacts for deployment. All use the `Connection`
resource type (a Secret with specific labels/annotations that RHOAI recognises):

| Storage option | storageUri format | Best for |
|---|---|---|
| S3-compatible object storage | `s3://bucket/path/` | Shared models, frequent updates without rebuilding |
| OCI container registry | `oci://registry/image:tag` | Versioned/immutable model packages, CI/CD pipelines |
| PVC (cluster storage) | `pvc://pvc-name/path/` | Dev/proto — train and serve in the same project |
| HuggingFace Hub (external) | `hf://org/model-name` | Quick experimentation, public pre-trained models |

All four formats are handled by the KServe **storage initializer** — an init container that
runs before vLLM starts, downloads model weights to `/mnt/models`, then exits. vLLM reads
from `/mnt/models` at startup (no HuggingFace API calls at runtime).

### hf:// — version history

`storageUri: hf://...` was **not supported in RHOAI 2.25.x**. The KServe admission webhook
rejected it with:

```
admission webhook "inferenceservice.kserve-webhook-server.pod-mutator" denied the request:
storage type must be one of [s3, hdfs, webhdfs]. storage type [huggingface] is not supported
```

Even a `storage-config` Secret with `"type": "huggingface"` did not help — the webhook
blocked the request before the storage initializer ran.

**Workaround used in RHOAI 2.25.x:** skip the storage initializer entirely. Set the
HuggingFace model ID directly in the ServingRuntime args:

```yaml
# ServingRuntime — bypass pattern (RHOAI 2.25.x only)
args:
  - --model=Qwen/Qwen2.5-1.5B-Instruct   # HF model ID, not /mnt/models
env:
  - name: HUGGING_FACE_HUB_TOKEN
    valueFrom:
      secretKeyRef:
        name: hf-token
        key: token
  - name: HF_HOME
    value: /tmp/hf_home
  - name: HF_HUB_OFFLINE
    value: "0"

# InferenceService — no storageUri
spec:
  predictor:
    model:
      modelFormat:
        name: vLLM
      runtime: vllm-gpu
```

`hf://` is **supported in newer RHOAI versions** (≥ 2.16 per Red Hat documentation). With it,
the ServingRuntime becomes simpler — no HF env vars, no PVC cache, just `--model=/mnt/models`:

```yaml
# ServingRuntime — with storage initializer (modern RHOAI)
args:
  - --model=/mnt/models    # storage initializer populates this path
  - --dtype=bfloat16

# InferenceService
spec:
  predictor:
    model:
      storageUri: hf://Qwen/Qwen2.5-1.5B-Instruct
      modelFormat:
        name: vLLM
      runtime: vllm-gpu
```

For public models (Qwen2.5 is not gated), no HuggingFace token is needed by the storage
initializer. For gated models, create a `storage-config` Secret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: storage-config
  namespace: ai-agentic
type: Opaque
stringData:
  storage-config: |
    {
      "huggingface": {
        "type": "huggingface",
        "hf_token": "<token>"
      }
    }
```

### RHOAI dashboard visibility for InferenceService

An `InferenceService` only appears in the RHOAI "Models" dashboard when **all three** of
these conditions are met:

1. **Namespace label** `opendatahub.io/dashboard: "true"` — registers the namespace as a
   Data Science Project in RHOAI.

2. **Namespace label** `modelmesh-enabled: "false"` — signals that this namespace uses
   KServe single-model serving, not ModelMesh multi-model serving. Without this, the RHOAI
   dashboard shows the namespace but lists no models under "Single-model serving".

3. **`storageUri` present** in `spec.predictor.model` — the dashboard uses the URI to display
   the model source. An InferenceService with no `storageUri` is valid for KServe but appears
   blank or hidden in the RHOAI UI.

```yaml
# namespace.yaml
metadata:
  labels:
    opendatahub.io/dashboard: "true"     # makes namespace a Data Science Project
    modelmesh-enabled: "false"           # forces KServe mode in the dashboard
```

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

## Model cache persistence — PVC for HuggingFace download (bypass pattern only)

> **Note:** this section applies only to the RHOAI 2.25.x bypass pattern where vLLM downloads
> the model directly. When using `storageUri: hf://...` with the KServe storage initializer,
> the model is downloaded to `/mnt/models` (an emptyDir managed by KServe) — no PVC is needed.

Without a PVC, vLLM downloads model weights into an `emptyDir` (`HF_HOME=/tmp/hf_home`).
Every pod restart (node reboot, OOM, scaling event) re-downloads the full model (~14 GiB for Qwen2.5-7B-Instruct), adding 5-10 minutes of cold start.

**Fix:** mount a `PersistentVolumeClaim` at `HF_HOME`.

KServe `ServingRuntime` supports `spec.volumes` and container `volumeMounts` directly:

```yaml
# serving-runtime.yaml
spec:
  containers:
    - name: kserve-container
      volumeMounts:
        - name: model-cache
          mountPath: /tmp/hf_home
  volumes:
    - name: model-cache
      persistentVolumeClaim:
        claimName: qwen-model-cache
```

```yaml
# qwen-model-cache-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: qwen-model-cache
  namespace: ai-agentic
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 20Gi  # 14 GiB weights + HF cache overhead
```

**S3 for model storage** — free tier comparison:

| Provider | Free tier | S3-compatible | Enough for 14 GiB | Notes |
|----------|-----------|---------------|-------------------|-------|
| Cloudflare R2 | 10 GiB storage, 0 egress | Yes (S3 API) | No (14 GiB > 10 GiB) | No egress fees |
| Backblaze B2 | 10 GiB storage | Yes | No | Egress charges apply |
| Oracle Cloud | 20 GiB object storage | Partial (OCI API, S3 compat mode) | Yes | Requires S3 compat mode enabled |
| MinIO on-cluster | Unlimited (uses cluster PVC) | Yes | Yes | Self-hosted; backed by cluster storage |

For this project (ephemeral RHDP clusters), a PVC is the simplest approach — S3 makes sense only when reusing a model across cluster reinstalls. MinIO would require a dedicated PVC and MinIO deployment.

---

## Knative h2c named port causes 502 on KServe Serverless endpoints

### Root cause

Naming a `ServingRuntime` container port `h2c` forces Knative to use HTTP/2 cleartext
throughout its entire internal routing chain: ingress gateway → activator → queue-proxy →
container. When the Knative activator is in `mode: Proxy` (the default while
`numActivators > 0`), all inbound requests pass through the activator before reaching the
pod.

With an `h2c` named port the activator's Istio sidecar silently drops the request — it
returns a TCP-level 502 without ever forwarding to the activator app process. The activator
application logs show zero entries for those requests. The failure is invisible until you
check Envoy cluster stats directly.

### Symptom

External endpoint (`qwen-predictor-ai-agentic.apps...`) returns:
```
502 Bad Gateway
```

No error in the vLLM pod logs. No error in the activator app logs. Only visible via:
```bash
oc exec -n knative-serving <activator-pod> -c istio-proxy -- \
  curl -s http://localhost:15000/stats | grep "inbound.*rq_total"
# rq_total does NOT increase when requests are sent → activator never sees the request
```

### Fix

Remove the port `name` from the `ServingRuntime` container port definition. An unnamed
port defaults to HTTP/1.1 to the container — Knative's standard behaviour, and what
RHOAI's built-in `vllm-cuda-runtime-template` uses:

```yaml
# BEFORE (broken): h2c port name forces HTTP/2 throughout → activator drops silently
ports:
  - name: h2c
    containerPort: 8080
    protocol: TCP

# AFTER (fixed): unnamed port = HTTP/1.1 to container → works correctly
ports:
  - containerPort: 8080
    protocol: TCP
```

### Diagnostic comparison

If you have a working and a broken InferenceService side by side, compare their Knative
Revision specs:

```bash
oc get revision <working-revision> -n ai-agentic -o jsonpath='{.spec.containers[0].ports}'
# [{"containerPort":8080,"protocol":"TCP"}]  ← no name

oc get revision <broken-revision> -n ai-agentic -o jsonpath='{.spec.containers[0].ports}'
# [{"containerPort":8080,"name":"h2c","protocol":"TCP"}]  ← has h2c name
```

### Why this matters for RHOAI dashboard

RHOAI's built-in `vllm-cuda-runtime-template` deliberately uses an unnamed port 8080. Any
custom `ServingRuntime` that copies the `h2c` port name from documentation examples (which
are often written for non-Istio environments) will exhibit this 502 silently.

---

## KServe InferenceService URL — two external URLs, one with no Route

In KServe Serverless mode, the RHOAI dashboard and `InferenceService` status expose **two
distinct external URLs**:

| URL pattern | Type | OpenShift Route |
|---|---|---|
| `https://qwen-predictor-<namespace>.apps...` | Knative Service URL (per revision) | ✅ Created by Knative networking |
| `https://qwen-<namespace>.apps...` | InferenceService-level URL | ❌ No Route — handled by Istio VirtualService only |

The second URL (`qwen-<namespace>.apps...`) returns `Application not available` from the
OpenShift router because no `Route` exists for it. KServe creates only an Istio
`VirtualService` for the InferenceService-level hostname — Knative does not create a Route
for it.

**The correct URL to use for API calls is the predictor URL:**
```
https://qwen-predictor-<namespace>.apps.<cluster-domain>/v1/chat/completions
```

This is not a bug — it is the intended architecture of KServe Serverless mode. The
InferenceService URL would only work if requests entered the cluster through the Istio
ingress gateway directly (not via an OpenShift Route).

---

## RHOAI dashboard — "Unknown Serving Runtime"

Custom namespace-scoped `ServingRuntime` objects show as **"Unknown Serving Runtime"** in
the RHOAI dashboard. This is cosmetic only — it does not affect functionality.

RHOAI's dashboard only recognises runtimes defined as OpenShift `Template` objects in the
`redhat-ods-applications` namespace (the built-in templates). A custom `ServingRuntime` CR
in the application namespace is valid KServe config but the dashboard has no name mapping
for it.

**Fix (optional):** add `openshift.io/display-name` annotation to the `ServingRuntime`:
```yaml
metadata:
  annotations:
    openshift.io/display-name: "vLLM GPU (Qwen)"
```
The dashboard will then display this name instead of "Unknown Serving Runtime".

---

## Bitnami Sealed Secrets chart — OpenShift SCC incompatibility

The upstream Bitnami Sealed Secrets Helm chart hardcodes `runAsUser: 1001` and `fsGroup: 65534` in both `podSecurityContext` and `containerSecurityContext`. OpenShift's restricted SCC rejects pods with hardcoded UIDs/GIDs.

Fix: bundle the chart locally (`charts/sealed-secrets/`) and remove the `podSecurityContext` and `containerSecurityContext` blocks entirely from `templates/deployment.yaml`. OpenShift assigns a valid UID automatically.
