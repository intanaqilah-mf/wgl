import json


def set_session_state(
    _action_trigger: str = "",
    _escalation_reason: str = "",
    _escalation_topic: str = "",
    route: str = "",
    sentiment_score: str = "",
    emergency_detected: str = "",
    _return_to_agent: str = "",
    pending_intent: str = "",
    slots_json: str = "",
) -> dict:
    """Sets session state that the system acts on: the action trigger (emergency, live_agent_handoff,
    replay_consent, return_to_origin), escalation reason and topic, route label, sentiment, the return agent and the
    saved task for an authentication round trip. No spoken pacing is needed; this tool is silent and
    instant.

    Args:
        _action_trigger: emergency, live_agent_handoff, replay_consent or return_to_origin; the
            system performs the action on the next step (replay_consent speaks the recording notice).
        _escalation_reason: Reason code for a handoff, for example user_requested_agent or
            auth_failure_handoff or auth_failure_exit.
        _escalation_topic: Queue for a handoff: billing, service, collections, medical,
            bereavement, regulatory or emergency.
        route: Module label M1 to M9 for the current topic; internal, never spoken.
        sentiment_score: Decimal from 0.0 (calm) to 1.0 (distressed).
        emergency_detected: true or false.
        _return_to_agent: Name of the agent to return to after authentication.
        pending_intent: What the customer was trying to do, saved before an authentication round
            trip.
        slots_json: JSON object text of the details collected so far, saved with pending_intent.

    Returns:
        dict with ok and the names of the variables that were set.
    """
    updates = {}
    if _action_trigger:
        if _action_trigger not in _ALLOWED_TRIGGERS:
            return _validation_error("an action trigger of emergency, live_agent_handoff, replay_consent or return_to_origin")
        updates["_action_trigger"] = _action_trigger
        if _action_trigger == "emergency":
            updates["emergency_detected"] = "true"
    for name, value in (("_escalation_reason", _escalation_reason), ("_escalation_topic", _escalation_topic),
                        ("route", route), ("_return_to_agent", _return_to_agent)):
        if value:
            updates[name] = value
    if sentiment_score:
        try:
            updates["sentiment_score"] = str(min(1.0, max(0.0, float(sentiment_score))))
        except ValueError:
            return _validation_error("a sentiment score between 0.0 and 1.0")
    if emergency_detected:
        updates["emergency_detected"] = "true" if emergency_detected.strip().lower() == "true" else "false"
    for name, value in updates.items():
        context.state[name] = value
    saved = []
    if pending_intent or slots_json:
        try:
            slots = json.loads(slots_json) if slots_json.strip() else {}
        except ValueError:
            return _validation_error("slots_json as a JSON object")
        merge_ctx("saved_task_state", {
            "saved": True,
            "pending_intent": pending_intent,
            "prior_route": str(context.state.get("route", "")),
            "slots": slots if isinstance(slots, dict) else {},
            "resume_offered": False,
        })
        saved = ["saved_task_state"]
    if not updates and not saved:
        return _validation_error("at least one value to set")
    return {"ok": True, "updated": sorted(updates) + saved}


def _object_var(name: str) -> dict:
    """Reads an object variable as a dict, accepting a dict or a JSON string."""
    value = context.state.get(name)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def merge_ctx(name: str, patch: dict) -> dict:
    """Reads the object variable, changes only the named fields, writes the whole object back.

    A None value removes the field: the platform silently drops a whole object write that carries null fields.
    """
    merged = _object_var(name)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    context.state[name] = merged
    return merged


def _error(error_class: str, agent_action: str, **extra) -> dict:
    """Builds a failure result: error_class is validation, business_rule, transient or CIS_DOWN."""
    result = {"ok": False, "error_class": error_class, "agent_action": agent_action}
    result.update(extra)
    return result


def _validation_error(problem: str) -> dict:
    """The input was missing or malformed; the agent should re-ask for that detail."""
    return _error("validation", f"Ask the customer again for the detail that is missing or invalid: {problem}.")


# Triggers the before_model callback knows how to execute deterministically.
_ALLOWED_TRIGGERS = ("emergency", "live_agent_handoff", "replay_consent", "return_to_origin")
