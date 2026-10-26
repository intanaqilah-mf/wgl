import json
import re
import time
from datetime import datetime, timedelta, timezone


def create_outage_incident(account_id: str = "", address: str = "", tenant: str = "") -> dict:
    """Reports a gas outage for an address. If an outage is already known for that ZIP code it returns
    the known incident instead of creating a new one. Works for guests and identified callers.
    Before calling this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Account id when the caller is identified; empty for guests.
        address: Service address including the ZIP code (REQUIRED).
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok and either incident_number (new report) or existing_outage, incident_id and
        eta (already known). A repeat report for the same ZIP code within two hours returns the
        open incident with duplicate true instead of creating another. The ZIP is read from the end
        of the address (never the house number); a guest report is recorded with account guest.
    """
    try:
        return _finish("create_outage_incident", _create_outage_incident_impl(account_id=account_id, address=address))
    except Exception as err:
        return _unexpected_error("create_outage_incident", err)


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


def _as_list(response) -> list:
    """Normalizes a toolset response: a list, or a dict that wraps a list under one or more layers."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("response", "result", "results", "output", "items"):
            inner = response.get(key)
            if isinstance(inner, (list, dict)):
                unwrapped = _as_list(inner)
                if unwrapped:
                    return unwrapped
        return [response] if ("document" in response or "fields" in response) else []
    return []


_DEFAULT_QUERY_LIMIT = 50


def _field_filter(field: str, value) -> dict:
    """Builds one EQUAL field filter."""
    return {"fieldFilter": {"field": {"fieldPath": field}, "op": "EQUAL", "value": _to_value(value)}}


def _run_query(collection: str, filters: dict, limit: int = _DEFAULT_QUERY_LIMIT) -> list:
    """Runs an EQUAL-only query (no orderBy, so no composite index) and returns plain documents."""
    _check_collection(collection)
    clauses = [_field_filter(field, value) for field, value in filters.items()]
    where = clauses[0] if len(clauses) == 1 else {"compositeFilter": {"op": "AND", "filters": clauses}}
    query = {"from": [{"collectionId": collection}], "where": where, "limit": limit}
    response = _payload(tools.wgl_firestore_runQuery({"structuredQuery": query}))
    return [_unwrap_doc(item["document"]) for item in _as_list(response) if isinstance(item, dict) and "document" in item]


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


# Failure-injection modes (TDD demo controls) grouped by the backend each one breaks.
_FAILURE_KINDS = {
    "cis": ("cis_down", "cis_timeout"),
    "oms": ("oms_down",),
    "gateway": ("gateway_decline",),
    "slots": ("slot_unavailable",),
}


def _controls() -> dict:
    """Reads this tenant's wgl_demo_controls document."""
    return _get_doc("wgl_demo_controls", _tenant())


def _failure_mode(controls: dict) -> str:
    """Session failure_mode (demo_ctx) wins over the global one in wgl_demo_controls."""
    session_mode = str(_object_var("demo_ctx").get("failure_mode") or "none")
    if session_mode != "none":
        return session_mode
    return str(controls.get("failure_mode") or "none")


def _injected_failure(kind: str, controls: dict) -> str:
    """Returns the active failure mode when it breaks the given backend kind, else an empty string."""
    mode = _failure_mode(controls)
    return mode if mode in _FAILURE_KINDS[kind] else ""


# An active outage whose estimated time passed more than this many days ago is a stale record, not a live outage.
_STALE_AFTER_DAYS = 14


def _local_now(tz_name: str = "America/New_York") -> datetime:
    """Current time in the outage's time zone as a naive datetime; demo_ctx.demo_clock_override replaces it when set."""
    moment = datetime.now(timezone.utc)
    override = _object_var("demo_ctx").get("demo_clock_override")
    if override:
        try:
            moment = datetime.fromisoformat(str(override).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return moment
        except ValueError:
            print("demo_clock_override is not ISO 8601; the real clock is used")
            moment = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return moment.astimezone(ZoneInfo(tz_name or "America/New_York")).replace(tzinfo=None)
    except Exception:
        offset = -4 if 3 <= moment.month <= 10 else -5
        return (moment.astimezone(timezone.utc) + timedelta(hours=offset)).replace(tzinfo=None)


def _parse_local(value, tz_name: str = "America/New_York"):
    """Parses a stored ISO time into a naive datetime in the outage's time zone; None when it is empty or not a time."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    try:
        from zoneinfo import ZoneInfo
        return parsed.astimezone(ZoneInfo(tz_name or "America/New_York")).replace(tzinfo=None)
    except Exception:
        return parsed.replace(tzinfo=None)


_ETA_PASSED_ACTION = (
    "The estimated restoration time for this outage has already passed, so do not state any restoration time or date. "
    "Say with empathy that crews are still working to restore service, thank the caller for waiting, say there is no need "
    "to report the outage again, and offer a text message when service is restored (register_restoration_sms)."
)


# One open incident per ZIP code is reused for repeat reports made within this window.
_DUPLICATE_WINDOW_SECONDS = 2 * 60 * 60


_STATE_ZIP = re.compile(
    r"(?<![A-Za-z0-9])(?:DC|MD|VA|WV|PA|DE|NJ|NY|NC|MI|OH|IN|Dc|Md|Va|dc|md|va)\.?[\s,]+(\d{5})(?:-\d{4})?(?!\d)"
)


_ZIP_AT_END = re.compile(r"(?<![\d-])(\d{5})(?:-\d{4})?\s*$")


def _zip_from_address(address: str) -> str:
    """The ZIP of an address: the five digits at the end, or after the state; never a house number at the start."""
    text = str(address or "").strip().rstrip(".")
    ending = _ZIP_AT_END.search(text)
    if ending:
        return ending.group(1)
    after_state = _STATE_ZIP.findall(text)
    return after_state[-1] if after_state else ""


def _create_outage_incident_impl(account_id: str, address: str) -> dict:
    identified = str(_object_var("identity_ctx").get("account_id", "") or "")
    if identified:
        record_account = _resolve_account_id(account_id)
        if not record_account:
            return _error("business_rule", "Only the identified account can be used. Leave account_id empty: it is filled in automatically.")
    else:
        record_account = "guest"
    zip_code = _zip_from_address(address) or str(_object_var("identity_ctx").get("service_address_zip", "") or "")
    if not zip_code or not address.strip() or zip_code == "00000":
        return _validation_error("the service address with its ZIP code")
    if _injected_failure("oms", _controls()):
        _set_var("api_failed", "true")
        return _error("transient", "The outage system did not respond. Retry once without telling the customer; if it fails again, offer a representative.")
    known = _get_doc("wgl_outages", f"{_tenant()}_{zip_code}")
    if known.get("active"):
        zone = str(known.get("eta_timezone") or "America/New_York")
        eta_time = _parse_local(known.get("eta", ""), zone)
        passed = eta_time is not None and eta_time < _local_now(zone)
        stale = passed and _local_now(zone) - eta_time > timedelta(days=_STALE_AFTER_DAYS)
        if not stale:
            result = {"ok": True, "existing_outage": True, "incident_id": known.get("incident_id", ""),
                      "eta": "" if passed else known.get("eta", "")}
            if passed:
                result["eta_passed"] = True
                result["agent_action"] = _ETA_PASSED_ACTION
            return result
    now = int(time.time())
    recent = []
    for row in _run_query("wgl_outage_incidents", {"tenant": _tenant(), "zip": zip_code, "status": "open"}, 20):
        reported = int(row.get("reported_at_epoch") or 0)
        if reported and 0 <= now - reported <= _DUPLICATE_WINDOW_SECONDS:
            recent.append((reported, row.get("_id", "")))
    if recent:
        existing = max(recent)[1]
        return {
            "ok": True,
            "existing_outage": True,
            "duplicate": True,
            "incident_id": existing,
            "incident_number": existing,
            "eta": "",
            "agent_action": "An outage report for this area is already open, so no new report was created. Give the caller this "
                            "incident number, say crews already know about it, and offer the restoration text message.",
        }
    incident = _reference_number("incidents", "I")
    _commit([_set_write("wgl_outage_incidents", incident, {
        "account_id": record_account,
        "tenant": _tenant(),
        "address": address.strip(),
        "zip": zip_code,
        "status": "open",
        "session_id": _session_id(),
        "reported_at_epoch": now,
    }, "new")])
    return {"ok": True, "existing_outage": False, "duplicate": False, "incident_number": incident}
