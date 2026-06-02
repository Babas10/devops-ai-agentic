from agent.state import AgentState


def verify(state: AgentState) -> dict:
    """Poll pod status for up to 30 s to check whether the fix worked.

    Sets verified=True if pod is Running/Ready.
    Increments retry_count if not fixed; the graph retries plan_fix up to 3 times.

    TODO (Story 2.9): implement pod status polling.
    """
    return {"verified": False, "retry_count": state.get("retry_count", 0) + 1}
