from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    alerts: list[dict]
    current_alert: dict
    alert_type: str        # IMAGE_PULL_BACKOFF | MISSING_SECRET | CRASH_LOOP | UNKNOWN
    investigation: dict    # populated by investigate_image / investigate_code
    solutions: list[str]
    fix_plan: str
    fix_result: str
    retry_count: int
    verified: bool
    report: str
    node_trace: list[str]  # ordered list of node names executed this cycle
    cycle_start: float     # unix timestamp set by classify_alert
    messages: Annotated[list, add_messages]
