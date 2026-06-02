from agent.state import AgentState


def search_rag(state: AgentState) -> dict:
    """Query ChromaDB with the alert type + message to retrieve runbook chunks.

    Uses nomic-embed-text via sentence-transformers for embeddings.
    Returns top-3 chunks by cosine similarity.

    TODO (Story 2.6): implement ChromaDB retrieval.
    """
    return {"solutions": []}
