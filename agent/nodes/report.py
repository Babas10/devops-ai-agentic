from agent.state import AgentState


def report(state: AgentState) -> dict:
    """Use Qwen via KServe to produce a human-readable remediation summary.

    Covers: what was detected, what fix was applied, and whether it succeeded.
    Output is printed to stdout (pod logs) and stored in AgentState.report.

    TODO (Story 2.10): implement LLM report generation.
    """
    return {"report": ""}
