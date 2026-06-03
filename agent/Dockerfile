# Build context: project root
#   docker build -f agent/Dockerfile -t <image> .
#
# Push to OpenShift internal registry:
#   oc registry login
#   docker build -f agent/Dockerfile \
#     -t image-registry.openshift-image-registry.svc:5000/ai-agentic/agent:latest .
#   docker push image-registry.openshift-image-registry.svc:5000/ai-agentic/agent:latest
#
# Or use an OpenShift binary build (no local Docker needed):
#   oc new-build --binary --name=agent -n ai-agentic
#   oc start-build agent --from-dir=. --follow -n ai-agentic

FROM registry.access.redhat.com/ubi9/python-311

WORKDIR /opt/app-root/src

# Install CPU-only PyTorch first — avoids pulling the much larger CUDA variant
# that pip would otherwise resolve transitively via sentence-transformers
RUN pip install --no-cache-dir \
    torch \
    --index-url https://download.pytorch.org/whl/cpu

# Install remaining requirements
COPY agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent source and the RAG knowledge file
COPY agent/ ./agent/
COPY docs/ ./docs/

ENV PYTHONPATH=/opt/app-root/src

# HF model cache and ChromaDB persist under /data (mounted as a PVC in-cluster)
ENV HF_HOME=/data/huggingface
ENV CHROMA_PERSIST_DIR=/data/chroma

# Create cache dirs and ensure they are group-writable for the arbitrary UIDs
# that OpenShift assigns at runtime
USER root
RUN mkdir -p /data/huggingface /data/chroma && \
    chown -R 1001:0 /data && \
    chmod -R g+rwX /data
USER 1001

CMD ["python", "-m", "agent.main"]
