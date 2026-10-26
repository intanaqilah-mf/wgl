import json
import re
import time


def create_callback_ticket(
    reason: str = "",
    callback_number: str = "",
    details: str = "",
    account_id: str = "",
    tenant: str = "",
) -> dict:
    """Creates a callback ticket when the customer cannot be served live (after hours, start service
    while the order system is unavailable, or degraded mode) or, for Washington Gas, to follow up a
    service abandonment or added load request. Never ends the conversation by itself.
    Before calling this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        reason: start_service, after_hours or degraded (REQUIRED); Washington Gas also accepts
            service_abandonment and added_load for service requests that need a form.
        callback_number: Phone number to call back (REQUIRED).
        details: Everything collected so far, in plain text, for the callback agent.
        account_id: Account id when the caller is identified; empty for guests (a guest ticket is
            recorded with account guest).
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, ticket_number, callback_number_last4 and, for a service_abandonment or added_load
        ticket, form_instructions saying where the request form goes.
    """
    try:
        return _finish("create_callback_ticket", _create_callback_ticket_impl(reason=reason, callback_number=callback_number, details=details, account_id=account_id))
    except Exception as err:
        return _unexpected_error("create_callback_ticket", err)


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


def _digits(text: str) -> str:
    """Keeps only the digits of a spoken, keyed or typed value."""
    return re.sub(r"\D", "", str(text))


def _e164(raw: str) -> str:
    """Normalizes a phone number to +1XXXXXXXXXX; returns an empty string when it is not a US number."""
    digits = _digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    # North American numbering plan: area code and exchange never start with 0 or 1, and never end in 11.
    area, exchange = digits[:3], digits[3:6]
    if area[0] in "01" or exchange[0] in "01" or area[1:] == "11" or exchange[1:] == "11":
        return ""
    return f"+1{digits}"


_PLACEHOLDER_IDS = ("", "none", "null", "n/a", "na", "unknown", "undefined", "account_id", "0")


def _resolve_account_id(account_id: str = "") -> str:
    """The account the caller was identified as. A blank or placeholder id means that account; a different real id returns ''."""
    identified = str(_object_var("identity_ctx").get("account_id", "") or "")
    given = str(account_id or "").strip()
    if given.lower() in _PLACEHOLDER_IDS:
        return identified
    return given if given == identified else ""


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
    """Commits all writes atomically; raises when Firestore reports an error so a failed write is never reported as success."""
    response = _first_dict(_payload(tools.wgl_firestore_commit({"writes": writes})))
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(f"Firestore commit failed: {str(response.get('error'))[:200]}")
    return response


def _next_sequence(counter_id: str) -> int:
    """Increments a wgl_counters document and returns the new value."""
    write = {
        "update": {"name": f"{_DB_DOCUMENTS}/wgl_counters/{counter_id}"},
        "updateMask": {"fieldPaths": []},
        "updateTransforms": [{"fieldPath": "value", "increment": {"integerValue": "1"}}],
    }
    results = _commit([write]).get("writeResults", [])
    transform = results[0].get("transformResults", []) if results else []
    return int(_from_value(transform[0])) if transform else 0


def _reference_number(counter_id: str, letter: str = "") -> str:
    """Builds a deterministic reference such as WG-84921 from the tenant prefix and a counter."""
    return f"{_tenant()[:2]}-{letter}{_next_sequence(counter_id)}"


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


_COMMON_REASONS = ("start_service", "after_hours", "degraded")


_WGL_ONLY_REASONS = ("service_abandonment", "added_load")


# Where the customer sends the request form (Washington Gas site FAQ).
_FORM_ROUTING = {
    "service_abandonment": "The completed Gas Service Abandonment/Demolition Request Form goes by email to svcabandonments@washgas.com or by fax to 703-750-7570. Abandonment usually takes about 8 to 12 weeks.",
    "added_load": "The completed Added Load/Split Meter Request Form goes by email to 941sales@washgas.com or by fax to 703-750-7691.",
}


def _create_callback_ticket_impl(reason: str, callback_number: str, details: str, account_id: str) -> dict:
    allowed = _COMMON_REASONS + (_WGL_ONLY_REASONS if _tenant() == "WGL" else ())
    if reason not in allowed:
        return _validation_error("a reason of " + ", ".join(allowed))
    phone = _e164(callback_number)
    if not phone:
        return _validation_error("a ten-digit callback number")
    identified = str(_object_var("identity_ctx").get("account_id", "") or "")
    if identified:
        record_account = _resolve_account_id(account_id)
        if not record_account:
            return _error("business_rule", "Only the identified account can be used. Leave account_id empty: it is filled in automatically.")
    else:
        # A guest (or an invented id) is recorded with an explicit marker, never an empty account id.
        record_account = "guest"
    ticket = _reference_number("cases", "CB")
    _commit([_set_write("wgl_callback_tickets", ticket, {
        "tenant": _tenant(),
        "reason": reason,
        "callback_number": phone,
        "details": str(details or "")[:2000],
        "account_id": record_account,
        "status": "open",
        "session_id": _session_id(),
    }, "new")])
    result = {"ok": True, "ticket_number": ticket, "callback_number_last4": phone[-4:]}
    if reason in _FORM_ROUTING:
        result["form_instructions"] = _FORM_ROUTING[reason]
    return result
