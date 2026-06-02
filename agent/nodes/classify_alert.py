from agent.state import AgentState


def classify_alert(state: AgentState) -> dict:
    """Use Qwen via KServe to classify the alert into a canonical category.

    Output: IMAGE_PULL_BACKOFF | MISSING_SECRET | UNKNOWN
    Routes to search_rag on known types, or directly to report on UNKNOWN.

    TODO (Story 2.5): implement LLM classification.
    """
    return {"alert_type": "UNKNOWN"}
