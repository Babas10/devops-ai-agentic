from agent.state import AgentState


def plan_fix(state: AgentState) -> dict:
    """Use Qwen via KServe to produce a structured JSON fix plan.

    Output schema: {"action": str, "target": str, "params": dict}
    Supported actions: patch_deployment_image, create_secret, patch_secret_ref

    TODO (Story 2.7): implement LLM fix planning.
    """
    return {"fix_plan": ""}
