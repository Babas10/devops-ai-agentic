from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    alerts: list[dict]
    current_alert: dict
    alert_type: str        # IMAGE_PULL_BACKOFF | MISSING_SECRET | UNKNOWN
    solutions: list[str]
    fix_plan: str
    fix_result: str
    retry_count: int
    verified: bool
    report: str
    messages: Annotated[list, add_messages]
