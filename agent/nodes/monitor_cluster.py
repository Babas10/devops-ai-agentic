from agent.state import AgentState


def monitor_cluster(state: AgentState) -> dict:
    """Query Kubernetes API for pods in a non-Running/Ready state.

    Detects: ImagePullBackOff, ErrImagePull, missing secret events.
    Routes to classify_alert if alerts found, otherwise to END.

    TODO (Story 2.4): implement real Kubernetes polling.
    """
    return {"alerts": [], "current_alert": {}, "retry_count": 0}
