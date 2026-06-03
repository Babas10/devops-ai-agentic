from agent.knowledge import search_knowledge
from agent.state import AgentState


def search_rag(state: AgentState) -> dict:
    """Query the runbook knowledge base for the current alert.

    Filters by alert_type when available (exact match on KB metadata) then
    ranks by cosine similarity using nomic-embed-text-v1 embeddings.
    Returns the top-3 matching runbook chunks as the solutions list.
    """
    alert = state.get("current_alert", {})
    alert_type = state.get("alert_type")
    query = alert.get("message") or alert_type or ""

    solutions = search_knowledge(query, alert_type=alert_type, n_results=3)
    return {"solutions": solutions}
