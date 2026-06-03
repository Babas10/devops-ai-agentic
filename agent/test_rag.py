"""Quick smoke-test for the RAG knowledge base.

Run from the repo root:
    python -m agent.test_rag

Or paste each cell into a workbench notebook.
"""

# ── Cell 1: build the index ──────────────────────────────────────────────────
from agent.knowledge import build_index, search_knowledge

print("Building index (first run downloads nomic-embed-text-v1 ~270 MB)...")
build_index()
print("Index ready.\n")

# ── Cell 2: unfiltered similarity search ─────────────────────────────────────
results = search_knowledge("container cannot pull image from registry", n_results=2)
print("=== Unfiltered: 'container cannot pull image from registry' ===")
for i, r in enumerate(results, 1):
    print(f"\n--- Result {i} ---")
    print(r[:400])

# ── Cell 3: filtered by alert_type ───────────────────────────────────────────
results = search_knowledge(
    "secret db-password not found",
    alert_type="MISSING_SECRET",
    n_results=1,
)
print("\n\n=== Filtered MISSING_SECRET: 'secret db-password not found' ===")
for r in results:
    print(r[:400])

# ── Cell 4: OOMKilled ────────────────────────────────────────────────────────
results = search_knowledge(
    "pod killed exit code 137 memory limit exceeded",
    alert_type="OOMKILLED",
    n_results=1,
)
print("\n\n=== Filtered OOMKILLED: 'exit code 137 memory limit exceeded' ===")
for r in results:
    print(r[:400])
