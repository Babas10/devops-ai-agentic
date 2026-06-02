from langgraph.graph import END, StateGraph

from agent.nodes.classify_alert import classify_alert
from agent.nodes.execute_fix import execute_fix
from agent.nodes.monitor_cluster import monitor_cluster
from agent.nodes.plan_fix import plan_fix
from agent.nodes.report import report
from agent.nodes.search_rag import search_rag
from agent.nodes.verify import verify
from agent.state import AgentState


def _route_after_monitor(state: AgentState) -> str:
    """No alerts → done. Alerts found → classify the first one."""
    return "classify_alert" if state.get("alerts") else END


def _route_after_classify(state: AgentState) -> str:
    """Unknown alert type → report immediately. Known type → search RAG."""
    return "report" if state.get("alert_type") == "UNKNOWN" else "search_rag"


def _route_after_verify(state: AgentState) -> str:
    """Fixed → report. Not fixed and retries left → try a new plan. Exhausted → report."""
    if state.get("verified"):
        return "report"
    if state.get("retry_count", 0) < 3:
        return "plan_fix"
    return "report"


def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("monitor_cluster", monitor_cluster)
    workflow.add_node("classify_alert", classify_alert)
    workflow.add_node("search_rag", search_rag)
    workflow.add_node("plan_fix", plan_fix)
    workflow.add_node("execute_fix", execute_fix)
    workflow.add_node("verify", verify)
    workflow.add_node("report", report)

    workflow.set_entry_point("monitor_cluster")

    workflow.add_conditional_edges(
        "monitor_cluster",
        _route_after_monitor,
        {"classify_alert": "classify_alert", END: END},
    )
    workflow.add_conditional_edges(
        "classify_alert",
        _route_after_classify,
        {"search_rag": "search_rag", "report": "report"},
    )
    workflow.add_edge("search_rag", "plan_fix")
    workflow.add_edge("plan_fix", "execute_fix")
    workflow.add_edge("execute_fix", "verify")
    workflow.add_conditional_edges(
        "verify",
        _route_after_verify,
        {"plan_fix": "plan_fix", "report": "report"},
    )
    workflow.add_edge("report", END)

    return workflow.compile()


graph = build_graph()
