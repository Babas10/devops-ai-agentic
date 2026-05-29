# LLM Serving Sizing Guide

How to size CPU and memory for LLM inference on KServe + vLLM.

---

## Memory sizing

Memory is the hard constraint. A pod that runs out of memory is killed — there is no graceful degradation.

### Formula

```
total_memory = model_weights + kv_cache + vllm_overhead
```

**Model weights**

Depends on the number of parameters and the precision (dtype):

| Dtype | Bytes per parameter | When to use |
|---|---|---|
| float32 | 4 | Fallback — not recommended, doubles memory |
| bfloat16 | 2 | Default for CPU (AVX2 native) |
| float16 | 2 | GPU only — no AVX-512 on most CPUs |
| int8 | 1 | Quantized — halves memory, small quality loss |
| int4 | 0.5 | Heavily quantized — significant quality loss |

```
weight_memory_GB = parameters_B × bytes_per_param
```

Examples (bfloat16):

| Model | Parameters | Weight memory |
|---|---|---|
| Qwen2.5-0.5B | 0.5B | ~1 GB |
| Qwen2.5-1.5B | 1.5B | ~3 GB |
| Qwen2.5-3B | 3B | ~6 GB |
| Qwen2.5-7B | 7B | ~14 GB |
| Qwen2.5-14B | 14B | ~28 GB |

**KV cache**

The KV cache holds attention keys and values for in-flight requests. Size depends on context length and concurrent sequences:

```
kv_cache_GB ≈ (max_model_len × max_num_seqs × num_layers × hidden_size × 2 × dtype_bytes) / 1e9
```

For practical purposes, use these rule-of-thumb estimates:

| Context (max_model_len) | Concurrent seqs (max_num_seqs) | KV cache |
|---|---|---|
| 2048 | 4 | ~0.5–1 GB |
| 4096 | 4 | ~1–2 GB |
| 8192 | 4 | ~2–4 GB |
| 4096 | 16 | ~4–8 GB |

**vLLM overhead**

~1–2 GB for the Python runtime, CUDA/CPU ops, tokenizer, and buffers. Budget 2 GB.

**Total examples**

| Model | Dtype | Context | Concurrency | Total (approx) | Recommended limit |
|---|---|---|---|---|---|
| Qwen2.5-1.5B | bf16 | 4096 | 4 | ~6 GB | 12 Gi |
| Qwen2.5-3B | bf16 | 4096 | 4 | ~10 GB | 16 Gi |
| Qwen2.5-7B | bf16 | 4096 | 4 | ~18 GB | 24 Gi |
| Qwen2.5-7B | int8 | 4096 | 4 | ~11 GB | 16 Gi |

Always set the **limit** ~30% above the calculated total to absorb spikes. Set the **request** to ~50% of the limit so the scheduler can bin-pack efficiently.

---

## CPU sizing

More CPUs directly increase token throughput on CPU inference. vLLM uses OpenMP threads.

| vCPUs | Throughput (Qwen2.5-1.5B, bf16) | Use case |
|---|---|---|
| 2 | ~3–5 tok/s | Testing only |
| 4 | ~6–10 tok/s | Light demo |
| 8 | ~10–15 tok/s | Demo / dev — recommended |
| 16 | ~12–18 tok/s | Marginal gain over 8 |

Returns diminish past 8 cores because inference is memory-bandwidth bound, not compute bound.

**Rule of thumb:** set CPU limit to 8, request to 2. This lets the pod burst to 8 when available without blocking scheduling.

---

## GPU sizing

With a GPU, memory is VRAM and throughput is dramatically higher.

| GPU | VRAM | Max model (bf16) | Throughput |
|---|---|---|---|
| T4 | 16 GB | 7B (tight) | ~40–80 tok/s |
| A10G | 24 GB | 7B (comfortable) | ~80–150 tok/s |
| A100 40G | 40 GB | 14B | ~150–300 tok/s |
| A100 80G | 80 GB | 30B | ~200–400 tok/s |

With GPU, use `float16` or `bfloat16` and remove `--device=cpu` from the vLLM args. Replace the CPU image with the CUDA image:

```
registry.redhat.io/rhoai/odh-vllm-cuda-rhel9@sha256:...
```

And add GPU resource limits:

```yaml
resources:
  limits:
    nvidia.com/gpu: "1"
```

---

## Context length vs throughput trade-off

Longer context = larger KV cache = less memory for concurrent requests.

For a conversational agent, 4096 tokens is sufficient (roughly 3000 words of context). Only increase if you need long-document RAG or very long conversations.

Reduce `--max-model-len` to free up KV cache memory and allow more concurrent sequences.

---

## Quick sizing checklist

1. **Pick the model** — balance quality vs available memory
2. **Calculate weight memory** — `params_B × 2` (bfloat16)
3. **Add KV cache** — use 2 GB for 4k context / 4 concurrent seqs
4. **Add 2 GB overhead**
5. **Set memory limit** = total × 1.3, **request** = limit × 0.5
6. **Set CPU limit** = 8 (CPU) or add `nvidia.com/gpu: 1` (GPU)
7. **Set `--max-num-seqs`** = 4 for CPU demo, higher for GPU
