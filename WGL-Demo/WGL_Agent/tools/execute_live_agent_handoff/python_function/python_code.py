import json
import re
import time


def execute_live_agent_handoff(reason: str = "", context: str = "", queue: str = "", tenant: str = "") -> dict:
    """Records a simulated live-agent handoff (reason, queue, context packet) in Firestore. The spoken
    handoff text comes from the verbatim strings and the session then ends. Before calling this
    tool, speak a brief conversational pacing phrase while it runs.

    Args:
        reason: Reason code such as user_requested_agent, no_input_escalation,
            auth_failure_handoff, auth_failure_exit, identification_failure_handoff, system_unavailable,
            pa_downpayment_failed, disambig_max_attempts or emergency (REQUIRED).
        context: Short plain-text summary of what the customer needs, without any digits used for
            verification.
        queue: Queue name: billing, service, collections, medical, bereavement, regulatory or
            emergency; empty uses the escalation topic.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, handoff_id, queue, reason, esr_available and, when the emergency queue is
        unavailable, emergency_number.
    """
    try:
        return _finish("execute_live_agent_handoff", _execute_live_agent_handoff_impl(reason=reason, context_packet=context, queue=queue))
    except Exception as err:
        return _unexpected_error("execute_live_agent_handoff", err)


def _var(name: str, default: str = "") -> str:
    """Reads a plain string session variable."""
    value = context.state.get(name, default)
    return default if value is None else str(value)


def _set_var(name: str, value: str) -> None:
    """Writes a plain string session variable."""
    context.state[name] = value


def _tenant() -> str:
    """Returns the tenant of this app; always taken from tenant_id, never from the model."""
    return _var("tenant_id", "WGL") or "WGL"


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


def _to_value(value) -> dict:
    """Converts a plain Python value into a Firestore typed value."""
    if value is None:
        return {"nullValue": "NULL_VALUE"}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_to_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {key: _to_value(item) for key, item in value.items()}}}
    return {"stringValue": str(value)}


def _from_value(typed: dict):
    """Converts a Firestore typed value back into a plain Python value."""
    if "stringValue" in typed:
        return typed["stringValue"]
    if "integerValue" in typed:
        return int(typed["integerValue"])
    if "doubleValue" in typed:
        return float(typed["doubleValue"])
    if "booleanValue" in typed:
        return bool(typed["booleanValue"])
    if "timestampValue" in typed:
        return typed["timestampValue"]
    if "arrayValue" in typed:
        return [_from_value(item) for item in typed["arrayValue"].get("values", [])]
    if "mapValue" in typed:
        return {key: _from_value(item) for key, item in typed["mapValue"].get("fields", {}).items()}
    return None


def _unwrap_doc(document: dict) -> dict:
    """Turns a Firestore document into plain fields plus its document id under _id."""
    plain = {key: _from_value(item) for key, item in document.get("fields", {}).items()}
    plain["_id"] = document.get("name", "").rsplit("/", 1)[-1]
    return plain


def _first_dict(response) -> dict:
    """Normalizes a toolset response that is a dict, possibly wrapped under one or more layers."""
    if isinstance(response, list):
        return _first_dict(response[0]) if response and isinstance(response[0], dict) else {}
    if isinstance(response, dict):
        if "fields" in response or "name" in response or "writeResults" in response or "error" in response:
            return response
        for key in ("response", "result", "output"):
            if isinstance(response.get(key), (dict, list)):
                return _first_dict(response[key])
        return response
    return {}


def _payload(response):
    """Unwraps the platform ExternalResponse into plain JSON; raises for an HTTP error (the text carries the status)."""
    if response is None or isinstance(response, (list, dict)):
        return response
    if not getattr(response, "ok", True):
        raise RuntimeError(f"Firestore HTTP {getattr(response, 'status_code', '?')}: {str(getattr(response, 'text', ''))[:200]}")
    return response.json()


# The Firestore toolset is reached only from Python tools; the model never sees it.
_DB_DOCUMENTS = "projects/emvnzir-canada-song/databases/acn-bank-fs-db/documents"


_COLLECTION_PREFIX = "wgl_"


def _check_collection(collection: str) -> None:
    """Refuses any collection outside the wgl_ namespace (ACN Bank data is never touched)."""
    if not collection.startswith(_COLLECTION_PREFIX):
        raise ValueError("collection is outside the wgl_ namespace")


def _get_doc(collection: str, doc_id: str) -> dict:
    """Reads one document; returns an empty dict when it does not exist."""
    _check_collection(collection)
    try:
        response = _payload(tools.wgl_firestore_getDocument({"collectionId": collection, "documentId": doc_id}))
    except Exception as err:
        if "404" in str(err) or "NOT_FOUND" in str(err):
            return {}
        raise
    document = _first_dict(response)
    if "error" in document and "fields" not in document:
        return {}
    return _unwrap_doc(document) if "name" in document or "fields" in document else {}


def _set_write(collection: str, doc_id: str, fields: dict, exists: str = "any") -> dict:
    """Builds a commit write that sets the given fields; exists is any, must or new."""
    _check_collection(collection)
    write = {
        "update": {
            "name": f"{_DB_DOCUMENTS}/{collection}/{doc_id}",
            "fields": {key: _to_value(item) for key, item in fields.items()},
        },
        "updateMask": {"fieldPaths": list(fields.keys())},
        "updateTransforms": [{"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}],
    }
    if exists == "must":
        write["currentDocument"] = {"exists": True}
    elif exists == "new":
        write["currentDocument"] = {"exists": False}
        write["updateTransforms"].append({"fieldPath": "created_at", "setToServerValue": "REQUEST_TIME"})
    return write


def _commit(writes: list) -> dict:
    """Commits all writes atomically."""
    return _first_dict(_payload(tools.wgl_firestore_commit({"writes": writes})))


def _error(error_class: str, agent_action: str, **extra) -> dict:
    """Builds a failure result: error_class is validation, business_rule, transient or CIS_DOWN."""
    result = {"ok": False, "error_class": error_class, "agent_action": agent_action}
    result.update(extra)
    return result


def _validation_error(problem: str) -> dict:
    """The input was missing or malformed; the agent should re-ask for that detail."""
    return _error("validation", f"Ask the customer again for the detail that is missing or invalid: {problem}.")


def _unexpected_error(tool_name: str, err: Exception) -> dict:
    """Classifies an unexpected exception as transient and flags the failure for the callbacks."""
    print(f"{tool_name} failed: {err}")
    _set_var("api_failed", "true")
    return _error(
        "transient",
        "Retry this tool once without telling the customer. If it fails again, apologize, say the "
        "systems are having trouble, and offer a representative or a callback.",
    )


def _session_id() -> str:
    """Returns the platform session id used to tie log rows together."""
    return str(getattr(context, "session_id", "") or "local_session")


def _new_id() -> str:
    """Builds a unique document id for rows keyed by session and time."""
    return f"{_session_id()}_{time.time_ns()}"


def _log_event(event_type: str, detail: dict) -> None:
    """Writes one flat row to wgl_session_events; never raises and never carries raw digits."""
    try:
        fields = {
            "session_id": _session_id(),
            "tenant": _tenant(),
            "event_type": event_type,
            "detail": json.dumps(detail, default=str),
        }
        _commit([_set_write("wgl_session_events", _new_id(), fields, "new")])
    except Exception as err:
        print(f"session event log failed: {err}")


def _finish(tool_name: str, result: dict) -> dict:
    """Logs the tool outcome (name, ok, error class only) and returns the result."""
    _log_event("tool_call", {"tool": tool_name, "ok": bool(result.get("ok", True)), "error_class": result.get("error_class", "")})
    return result


def _controls() -> dict:
    """Reads this tenant's wgl_demo_controls document."""
    return _get_doc("wgl_demo_controls", _tenant())


def _tenant_config() -> dict:
    """Reads this tenant's wgl_tenant_config document."""
    return _get_doc("wgl_tenant_config", _tenant())


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


def _execute_live_agent_handoff_impl(reason: str, context_packet: str, queue: str) -> dict:
    if not reason.strip():
        return _validation_error("the handoff reason")
    if reason.strip() == "user_requested_agent" and not _person_requested():
        return _error("business_rule", _no_handoff_action())
    queue_name = queue.strip() or _var("_escalation_topic", "billing") or "billing"
    controls = _controls()
    handoff_id = _new_id()
    _commit([_set_write("wgl_handoffs", handoff_id, {
        "tenant": _tenant(),
        "session_id": _session_id(),
        "reason": reason.strip(),
        "queue": queue_name,
        "language": _var("language", "EN"),
        "context": context_packet,
    }, "new")])
    _set_var("_escalation_reason", reason.strip())
    _set_var("_escalation_topic", queue_name)
    if queue_name == "medical":
        merge_ctx("hardship_ctx", {"medical_flag": True})
    esr_available = bool(controls.get("esr_available", True))
    result = {"ok": True, "handoff_id": handoff_id, "queue": queue_name, "reason": reason.strip(), "esr_available": esr_available}
    if queue_name == "emergency" and not esr_available:
        result["emergency_number"] = (_tenant_config().get("demo_phones") or {}).get("emergency", "")
    return result
