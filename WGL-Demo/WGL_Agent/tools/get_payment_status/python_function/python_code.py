import json
import time
from datetime import date, datetime, timedelta, timezone


def get_payment_status(account_id: str = "", tenant: str = "") -> dict:
    """Says whether a payment is pending on the account, its type, amount and expected posting date.
    For an authenticated caller it also lists the payment methods on file (type, brand, last 4
    digits and the card-on-file token) that process_payment_cc needs. Before calling this tool,
    speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, pending_payment_type (P, W or S, or null), amount, expected_posting_date,
        cancel_eligible, balance, credit_balance and, only when authenticated, payment_methods.
    """
    try:
        return _finish("get_payment_status", _get_payment_status_impl(account_id=account_id))
    except Exception as err:
        return _unexpected_error("get_payment_status", err)


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


_DEFAULT_QUERY_LIMIT = 50


def _check_collection(collection: str) -> None:
    """Refuses any collection outside the wgl_ namespace (ACN Bank data is never touched)."""
    if not collection.startswith(_COLLECTION_PREFIX):
        raise ValueError("collection is outside the wgl_ namespace")


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


def _error(error_class: str, agent_action: str, **extra) -> dict:
    """Builds a failure result: error_class is validation, business_rule, transient or CIS_DOWN."""
    result = {"ok": False, "error_class": error_class, "agent_action": agent_action}
    result.update(extra)
    return result


def _validation_error(problem: str) -> dict:
    """The input was missing or malformed; the agent should re-ask for that detail."""
    return _error("validation", f"Ask the customer again for the detail that is missing or invalid: {problem}.")


def _rule_error(rule: str, alternative: str) -> dict:
    """A business rule blocked the request; the agent explains the rule and offers the alternative."""
    return _error(
        "business_rule",
        f"Explain warmly that for this account {rule}, and offer this next option: {alternative}.",
    )


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


def _cis_unavailable(controls: dict) -> dict:
    """Returns the CIS failure result when an injected CIS failure is active, else an empty dict."""
    mode = _injected_failure("cis", controls)
    if not mode:
        return {}
    _set_var("api_failed", "true")
    if mode == "cis_down":
        _set_var("degraded_mode", "true")
        return _error(
            "CIS_DOWN",
            "The account system is down. Do not state any account figures. Say the account systems are "
            "temporarily unavailable and offer a representative or a callback shortly.",
        )
    return _error(
        "transient",
        "The account system timed out. Retry this tool once without telling the customer; if it fails "
        "again, apologize and offer a representative or a callback.",
    )


def _now() -> datetime:
    """Current time; demo_ctx.demo_clock_override replaces the real clock when set."""
    override = _object_var("demo_ctx").get("demo_clock_override")
    if override:
        try:
            parsed = datetime.fromisoformat(str(override).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            print("demo_clock_override is not ISO 8601; the real clock is used")
    return datetime.now(timezone.utc)


def _eastern_offset_hours(moment_utc: datetime) -> int:
    """-4 during US daylight saving time (second Sunday of March to first Sunday of November), -5 otherwise."""
    year = moment_utc.year
    first_march = date(year, 3, 1)
    dst_start = first_march + timedelta(days=(6 - first_march.weekday()) % 7 + 7)
    first_november = date(year, 11, 1)
    dst_end = first_november + timedelta(days=(6 - first_november.weekday()) % 7)
    start = datetime(dst_start.year, dst_start.month, dst_start.day, 7, tzinfo=timezone.utc)   # 2:00 EST
    end = datetime(dst_end.year, dst_end.month, dst_end.day, 6, tzinfo=timezone.utc)           # 2:00 EDT
    return -4 if start <= moment_utc < end else -5


def _today() -> date:
    """Today's date on the demo clock; the real clock is read in US Eastern time (the app's time zone), not UTC, so after about
    8 pm Eastern today is still today."""
    override = _object_var("demo_ctx").get("demo_clock_override")
    if override:
        try:
            return date.fromisoformat(str(override)[:10])
        except ValueError:
            pass
    moment = _now().astimezone(timezone.utc)
    return (moment + timedelta(hours=_eastern_offset_hours(moment))).date()


# auth_status ladder (PRD s10.1): write tools need Authenticated, account reads need Identified.
_AUTH_RANK = {"Guest": 0, "Identified": 1, "Authenticated": 2}


def _auth_gate(required: str) -> dict:
    """Refuses when auth_status is below the required level (defence in depth with the instructions)."""
    current = _var("auth_status", "Guest") or "Guest"
    if _AUTH_RANK.get(current, 0) >= _AUTH_RANK[required]:
        return {}
    return _error(
        "business_rule",
        f"This caller is not verified yet (auth_status is {current}, {required} is needed). Your next output must be only the function call "
        "transfer_to_agent with agent auth_agent: write no text and call no other tool, because the request is saved and the caller returns here after verification. "
        "Never tell the caller about internal routing or say the payment cannot be processed.",
        auth_required=required,
    )


_PLACEHOLDER_IDS = ("", "none", "null", "n/a", "na", "unknown", "undefined", "account_id", "0")


def _resolve_account_id(account_id: str = "") -> str:
    """The account the caller was identified as. A blank or placeholder id means that account; a different real id returns ''."""
    identified = str(_object_var("identity_ctx").get("account_id", "") or "")
    given = str(account_id or "").strip()
    if given.lower() in _PLACEHOLDER_IDS:
        return identified
    return given if given == identified else ""


def _load_customer(account_id: str, required: str) -> tuple:
    """Loads the caller's customer record after the CIS, auth and account-match checks.

    Returns (customer, error); exactly one of them is empty.
    """
    controls = _controls()
    blocked = _cis_unavailable(controls)
    if blocked:
        return {}, blocked
    denied = _auth_gate(required)
    if denied:
        return {}, denied
    resolved = _resolve_account_id(account_id)
    if not resolved:
        if str(account_id or "").strip().lower() in _PLACEHOLDER_IDS:
            return {}, _validation_error("account_id")
        return {}, _error("business_rule", "Only the identified account can be used. Leave account_id empty: it is filled in automatically.")
    customer = _get_doc("wgl_customers", resolved)
    if not customer or customer.get("tenant") != _tenant():
        return {}, _error("business_rule", "No matching account was found. Ask the customer to confirm the details and try again.")
    return customer, {}


def _already_posted(pending: dict) -> bool:
    """True when the pending payment's posting date is today or earlier on the demo clock (it can no longer be cancelled)."""
    if _tenant() != "WGL":
        return False
    try:
        posting = date.fromisoformat(str(pending.get("posting_date") or "")[:10])
    except ValueError:
        return False
    return _today() >= posting


def _get_payment_status_impl(account_id: str) -> dict:
    customer, error = _load_customer(account_id, "Identified")
    if error:
        return error
    pending = customer.get("pending_payment") or {}
    merge_ctx("account_ctx", {"pending_payment_type": pending.get("type"), "pending_payment_amt": pending.get("amt")})
    result = {
        "ok": True,
        "pending_payment_type": pending.get("type"),
        "amount": pending.get("amt"),
        "expected_posting_date": pending.get("posting_date", ""),
        "cancel_eligible": bool(pending.get("cancel_eligible")) and not _already_posted(pending),
        "balance": round(float(customer.get("balance") or 0), 2),
        "arrears": round(float(customer.get("arrears") or 0), 2),
        "due_date": customer.get("due_date", ""),
        "credit_status": customer.get("credit_status", ""),
        "credit_balance": round(float(customer.get("credit_balance") or 0), 2),
        "autopay_enrolled": bool(customer.get("autopay_enrolled")),
    }
    if customer.get("shutoff_date"):
        result["shutoff_date"] = customer["shutoff_date"]
    if _tenant() == "WGL":
        result["ebill_enrolled"] = bool(customer.get("ebil_flag"))
    if _var("auth_status") == "Authenticated":
        methods = _run_query("wgl_payment_methods", {"account_id": customer["_id"]}, 10)
        keys = ("type", "brand", "last4", "token_ref", "is_default")
        result["payment_methods"] = [dict({key: item.get(key) for key in keys}, method_id=item["_id"]) for item in methods]
    elif _var("auth_status") == "Identified":
        result["agent_action"] = (
            "The caller is recognised but not verified. If the caller wants to make a payment or change something, your next output must be only the function call "
            "transfer_to_agent with agent auth_agent: write no text and ask no amount or payment method question first. "
            "If the caller only asked for the balance, the due date or the status of a payment, answer as your instructions say (including any card they call for)."
        )
    return result
