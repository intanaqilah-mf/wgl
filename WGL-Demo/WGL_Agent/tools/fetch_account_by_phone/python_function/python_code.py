import json
import re
import time


def fetch_account_by_phone(phone: str = "", tenant: str = "", service_address: str = "") -> dict:
    """Identifies the account from a mobile number the customer gives (Tier 1 fallback). When several
    accounts share the number, pass the street the customer chose in service_address to pick one.
    Before calling this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        phone: The customer's mobile number, spoken, keyed or typed (REQUIRED).
        tenant: Injected by the system from tenant_id; leave empty.
        service_address: Street of the account the customer chose when several accounts matched;
            leave empty on the first call.

    Returns:
        dict with ok, found, count, accounts (account_id and service_address only) and, on one
        match, the Identified account summary; attempts_remaining on a miss.
    """
    try:
        return _finish("fetch_account_by_phone", _fetch_account_by_phone_impl(phone=phone, service_address=service_address))
    except Exception as err:
        return _unexpected_error("fetch_account_by_phone", err)


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


def _digits(text: str) -> str:
    """Keeps only the digits of a spoken, keyed or typed value."""
    return re.sub(r"\D", "", str(text))


def _e164(raw: str) -> str:
    """Normalizes a phone number to +1XXXXXXXXXX; returns an empty string when it is not a US number."""
    digits = _digits(raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return ""


def _money(value) -> float:
    """Rounds an amount to cents."""
    return round(float(value or 0), 2)


def _tokens(text: str) -> set:
    """Lowercase alphanumeric words of a name or address."""
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


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


# PRD s19.1: a CIS timeout during identification or authentication degrades the session like an outage does.
_DEGRADING_MODES = ("cis_down", "cis_timeout")


def _cis_unavailable(controls: dict) -> dict:
    """Returns the CIS failure result when an injected CIS failure is active, else an empty dict."""
    mode = _injected_failure("cis", controls)
    if not mode:
        return {}
    _set_var("api_failed", "true")
    if mode in _DEGRADING_MODES:
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


def _identity_patch(customer: dict) -> dict:
    """Maps a customer document to identity_ctx fields (account_number_last4 is the only number ever spoken)."""
    return {
        "account_id": customer["_id"],
        "account_number_last4": customer.get("account_number_last4", ""),
        "customer_name": f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(),
        "service_address": customer.get("service_address", ""),
        "service_address_zip": customer.get("zip", ""),
        "customer_type": customer.get("customer_type", ""),
        "jurisdiction": customer.get("jurisdiction", ""),
    }


def _wna_applies(customer: dict) -> bool:
    """WNA applies to Washington Gas customers in Virginia only (BR-GU-018, BR-GU-029)."""
    return _tenant() == "WGL" and customer.get("jurisdiction") == "VA"


def _account_patch(customer: dict) -> dict:
    """Maps a customer document to account_ctx fields; tenant-specific fields are set only where they apply."""
    pending = customer.get("pending_payment") or {}
    patch = {
        "balance": _money(customer.get("balance")),
        "due_date": customer.get("due_date", ""),
        "arrears": _money(customer.get("arrears")),
        "shutoff_date": customer.get("shutoff_date"),
        "credit_status": customer.get("credit_status", ""),
        "last_payment_amt": _money(customer.get("last_payment_amt")),
        "pending_payment_type": pending.get("type"),
        "pending_payment_amt": pending.get("amt"),
        "budget_plan_enrolled": bool(customer.get("budget_plan_enrolled")),
        "autopay_enrolled": bool(customer.get("autopay_enrolled")),
    }
    if _tenant() == "WGL":
        patch["ebil_flag"] = bool(customer.get("ebil_flag"))
        patch["wna_amount"] = customer.get("wna_amount") if _wna_applies(customer) else None
        patch["wna_direction"] = customer.get("wna_direction") if _wna_applies(customer) else None
    if customer.get("hhc_amount") is not None:
        patch["hhc_amount"] = customer.get("hhc_amount")
    return patch


# PRD s10.5: three identification failures with one method end in a live-agent handoff.
_TIER1_LIMIT = 3


def _apply_identity(customer: dict, method: str, level: str = "Identified") -> dict:
    """Loads the matched customer into the session and returns the minimal model-facing summary."""
    merge_ctx("identity_ctx", _identity_patch(customer))
    merge_ctx("account_ctx", _account_patch(customer))
    _set_var("auth_status", level)
    _set_var("auth_method_tier1", method)
    _set_var("identification_status", "Pass")
    _set_var("auth_attempts_tier1", "0")
    _commit([_set_write("wgl_sessions", _session_id(), {
        "tenant": _tenant(),
        "channel": _var("channel"),
        "language": _var("language", "EN"),
        "auth_status": level,
        "auth_method_tier1": method,
    })])
    identity = _identity_patch(customer)
    return {
        "ok": True,
        "found": True,
        "auth_status": level,
        "account_id": identity["account_id"],
        "customer_name": identity["customer_name"],
        "customer_type": identity["customer_type"],
        "jurisdiction": identity["jurisdiction"],
        "service_address_zip": identity["service_address_zip"],
        "balance": _money(customer.get("balance")),
        "due_date": customer.get("due_date", ""),
    }


def _candidates_result(customers: list, method: str) -> dict:
    """Several accounts matched: keep the candidates by address only; the account is not chosen yet."""
    candidates = [{"account_id": c["_id"], "service_address": c.get("service_address", "")} for c in customers]
    merge_ctx("identity_ctx", {"candidate_accounts": candidates})
    return {
        "ok": True,
        "found": False,
        "count": len(candidates),
        "accounts": candidates,
        "agent_action": "Several accounts match. Ask which service address the customer means by its street "
                        "name, never by account number, then repeat the lookup with that address.",
    }


def _tier1_count(method: str) -> int:
    """Failed identifications so far with this method. Kept in a flat integer field: nested maps written
    through the Firestore toolset arrive empty, which used to leave this count stuck at one."""
    return int(_get_doc("wgl_sessions", _session_id()).get(f"tier1_fail_{method}") or 0)


def _lockout_error() -> dict:
    """The result once a method has failed three times: hand off, never look up again."""
    return _error(
        "business_rule",
        "Identification failed three times with this method. Say a warm hand-off line, then call "
        "set_session_state with _action_trigger live_agent_handoff, _escalation_reason "
        "identification_failure_handoff and _escalation_topic billing.",
        found=False,
        attempts_remaining=0,
    )


def _tier1_lockout(method: str) -> dict:
    """An error result when this method already failed three times (checked before every lookup), else empty."""
    if _tier1_count(method) >= _TIER1_LIMIT:
        _set_var("auth_attempts_tier1", str(_TIER1_LIMIT))
        return _lockout_error()
    return {}


def _identification_failure(method: str) -> dict:
    """Counts a failed identification per method and escalates at the third failure."""
    count = _tier1_count(method) + 1
    _set_var("auth_attempts_tier1", str(count))
    _set_var("identification_status", "Fail")
    _commit([_set_write("wgl_sessions", _session_id(), {"tenant": _tenant(), f"tier1_fail_{method}": count})])
    if count >= _TIER1_LIMIT:
        return _lockout_error()
    return {
        "ok": True,
        "found": False,
        "attempts_remaining": _TIER1_LIMIT - count,
        "agent_action": "No account matched. Ask the customer to repeat the detail, or offer another way to "
                        "identify the account: mobile number, name and address, or account number.",
    }


def _address_matches(given: str, stored: str) -> bool:
    """Fuzzy address match: every house number must appear and at least one street word must overlap."""
    given_tokens, stored_tokens = _tokens(given), _tokens(stored)
    numbers = {token for token in given_tokens if token.isdigit()}
    words = given_tokens - numbers
    return numbers <= stored_tokens and bool(words & stored_tokens)


def _fetch_account_by_phone_impl(phone: str, service_address: str) -> dict:
    locked = _tier1_lockout("MobileNum")
    if locked:
        return locked
    e164 = _e164(phone)
    if not e164:
        return _validation_error("a valid ten-digit mobile number")
    blocked = _cis_unavailable(_controls())
    if blocked:
        return blocked
    matches = _run_query("wgl_customers", {"mobile_e164": e164, "tenant": _tenant()}, 10)
    if service_address.strip():
        matches = [item for item in matches if _address_matches(service_address, item.get("service_address", ""))]
    if len(matches) == 1:
        result = _apply_identity(matches[0], "MobileNum")
        result["count"] = 1
        return result
    if matches:
        return _candidates_result(matches, "MobileNum")
    return _identification_failure("MobileNum")
