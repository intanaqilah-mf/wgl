import json
import re


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
        if _action_trigger == "live_agent_handoff" and (_escalation_reason or "user_requested_agent") == "user_requested_agent" and not _person_requested():
            return _error("business_rule", _no_handoff_action())
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
    result = {"ok": True, "updated": sorted(updates) + saved}
    # Without a next step in the result the model sometimes returns an empty reply after this call (the caller then sees
    # "I'm having trouble with that"). An identification handoff (the request is saved, so the caller needs identifying
    # or verifying) goes to auth_agent; a plain hop goes to the agent named in _return_to_agent.
    if _return_to_agent in _TRANSFER_TARGETS and not _action_trigger:
        destination = "auth_agent" if pending_intent else _return_to_agent
        result["agent_action"] = f"Saved. Now call transfer_to_agent with agent {destination} and write nothing else."
    elif route in _ROUTE_AGENTS and not _action_trigger and not _return_to_agent:
        result["agent_action"] = (f"Saved. Now call transfer_to_agent with agent {_ROUTE_AGENTS[route]} unless you are already that agent, "
                                  "and write nothing else.")
    return result


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


_PERSON_REQUEST = re.compile(
    r"\b(representative|rep|agent|human|person|people|someone|somebody|operator|supervisor|manager|advisor|associate|"
    r"specialist|customer service|talk to|speak (?:to|with)|connect me|transfer me|call me|"
    r"representante|agente|persona|alguien|operador|gerente|supervisor|hablar con|comun[ií]que)\b",
    re.IGNORECASE,
)
_SPECIALIST_TOPIC = re.compile(
    r"medical|life support|oxygen|ventilator|dialysis|death|died|passed away|estate|funeral|bereave|bankrupt|"
    r"name change|change (?:the |my )?name|regulat|commission|attorney general|"
    r"m[eé]dic|fallec|muri[oó]|quiebra|cambio de nombre|regulador",
    re.IGNORECASE,
)
# Where a request belongs, checked in this order: "usage history" goes to service and "bill history" to billing.
_INTENT_ROUTES = (
    ("hardship_agent", re.compile(r"can'?t pay|cannot pay|unable to pay|shut ?off|disconnect|hardship|assistance|arrear|past due|payment plan|payment arrangement", re.IGNORECASE)),
    ("service_agent", re.compile(r"usage|therm|how much gas|gas (?:use|used)|(?:did|do) i use|appointment|meter|start service|stop service|move|reconnect", re.IGNORECASE)),
    ("billing_payments_agent", re.compile(r"balance|bill|pay|autopay|budget|refund|due|charge|statement", re.IGNORECASE)),
    ("account_support_agent", re.compile(r"outage|rebate|account number|log ?in|password|\bpin\b|profile|complaint", re.IGNORECASE)),
)
# The route labels the instructions set (M3 to M9) and the agent each one belongs to.
_ROUTE_AGENTS = {"M3": "billing_payments_agent", "M4": "billing_payments_agent", "M5": "hardship_agent", "M6": "service_agent",
                 "M7": "service_agent", "M8": "account_support_agent", "M9": "account_support_agent"}


def _agent_for_intent(intent: str) -> str:
    """The specialist for this request; root_agent when none fits."""
    for agent, pattern in _INTENT_ROUTES:
        if pattern.search(intent):
            return agent
    return "root_agent"


def _no_handoff_action() -> str:
    """The refusal text: name the agent that fits the caller's latest message."""
    target = _agent_for_intent(str(context.state.get("utterance", "") or ""))
    if target == "root_agent":
        return ("The caller did not ask for a person, so do not hand off and do not offer one. Keep helping: answer the request "
                "from the tools. Offer a representative only if they ask.")
    return ("The caller did not ask for a person, so do not hand off and do not offer one. Your next action must be to call "
            f"transfer_to_agent with agent {target} now and write nothing else (if you are already {target}, answer the request "
            "from the tools instead).")


def _person_requested() -> bool:
    """True when the caller's latest message asks for a person or raises a specialist-only topic, or is too short to judge
    (a bare yes after an offer). Fails open when there is no message."""
    text = str(context.state.get("utterance", "") or "").strip()
    if not text or len(text.split()) <= 3:
        return True
    return bool(_PERSON_REQUEST.search(text) or _SPECIALIST_TOPIC.search(text))


_TRANSFER_TARGETS = ("billing_payments_agent", "hardship_agent", "service_agent", "account_support_agent", "auth_agent", "root_agent")

# Triggers the before_model callback knows how to execute deterministically.
_ALLOWED_TRIGGERS = ("emergency", "live_agent_handoff", "replay_consent", "return_to_origin")
