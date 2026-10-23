import hashlib
import json
import re
import time


def validate_taxid_last4(account_id: str = "", taxid_last4: str = "", tenant: str = "") -> dict:
    """Checks the last 4 digits of a business account's Tax ID (only when get_auth_options returned
    ssn_tax_flag TAXID). The digits are never repeated back or logged. Before calling this tool,
    speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Account id of the identified account (REQUIRED).
        taxid_last4: The four digits, spoken, keyed or typed (REQUIRED).
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, result (Pass or Fail), attempts_remaining and agent_action; on Pass
        auth_status becomes Authenticated.
    """
    try:
        return _finish("validate_taxid_last4", _validate_taxid_last4_impl(account_id=account_id, taxid_last4=taxid_last4))
    except Exception as err:
        return _unexpected_error("validate_taxid_last4", err)


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


# auth_status ladder (PRD s10.1): write tools need Authenticated, account reads need Identified.
_AUTH_RANK = {"Guest": 0, "Identified": 1, "Authenticated": 2}


def _auth_gate(required: str) -> dict:
    """Refuses when auth_status is below the required level (defence in depth with the instructions)."""
    current = _var("auth_status", "Guest") or "Guest"
    if _AUTH_RANK.get(current, 0) >= _AUTH_RANK[required]:
        return {}
    return _error(
        "business_rule",
        f"auth_status is {current} but {required} is needed. Save the pending task in saved_task_state and "
        "transfer silently to auth_agent; never tell the customer about internal routing.",
        auth_required=required,
    )


# PRD s10.5: three Tier 2 failures across all methods end authentication.
_TIER2_LIMIT = 3


def _tier2_ready(method_ok: bool, problem: str) -> dict:
    """Checks the account is identified and that get_auth_options allowed this method."""
    denied = _auth_gate("Identified")
    if denied:
        return denied
    if not _object_var("identity_ctx").get("account_id"):
        return _validation_error("identified account")
    _, failed_total = _tier2_totals()
    if failed_total >= _TIER2_LIMIT:
        _set_var("auth_attempts_tier2", str(failed_total))
        return _tier2_result("", failed_total)
    if not method_ok:
        return _error(
            "business_rule",
            f"This verification method is not available. {problem} Call get_auth_options first and offer only "
            "the methods it returns.",
        )
    return {}


def _secret_matches(secrets: dict, field: str, supplied: str) -> bool:
    """Compares a supplied value with the stored secret (salted hash when present, else the stored value)."""
    stored_hash = secrets.get(f"{field}_hash")
    if stored_hash:
        digest = hashlib.sha256(f"{secrets.get('salt', '')}{supplied}".encode()).hexdigest()
        return digest == stored_hash
    stored = secrets.get(field)
    return stored is not None and str(stored) == supplied


def _tier2_pass(method: str) -> dict:
    """Marks the caller Authenticated from a successful validation (never from the model)."""
    _set_var("auth_status", "Authenticated")
    _set_var("auth_method_tier2", method)
    _commit([_set_write("wgl_sessions", _session_id(), {
        "tenant": _tenant(),
        "auth_status": "Authenticated",
        "auth_method_tier2": method,
    })])
    return {
        "ok": True,
        "result": "Pass",
        "auth_status": "Authenticated",
        "agent_action": "Authentication succeeded. Continue the pending task without asking again. If "
                        "saved_task_state holds a pending intent, call set_session_state with _action_trigger "
                        "return_to_origin.",
    }


def _tier2_totals() -> tuple:
    """Failed validations so far: (count for each method, total across methods). Kept in flat integer fields
    (tier2_fail_<method>): nested maps written through the Firestore toolset arrive empty, which used to
    leave this count stuck at one."""
    session = _get_doc("wgl_sessions", _session_id())
    counts = {key[len("tier2_fail_"):]: int(value or 0) for key, value in session.items() if key.startswith("tier2_fail_")}
    return counts, sum(counts.values())


def _tier2_fail(method: str) -> dict:
    """Counts a failed validation across all methods and returns only Fail plus attempts_remaining."""
    counts, total = _tier2_totals()
    counts[method] = counts.get(method, 0) + 1
    total += 1
    _set_var("auth_attempts_tier2", str(total))
    _commit([_set_write("wgl_sessions", _session_id(), {"tenant": _tenant(), f"tier2_fail_{method}": counts[method]})])
    return _tier2_result(method, total)


def _tier2_result(method: str, total: int) -> dict:
    """The Fail result for this many failures in all; at the limit it tells the agent to hand off."""
    remaining = max(0, _TIER2_LIMIT - total)
    result = {"ok": True, "result": "Fail", "attempts_remaining": remaining}
    if remaining:
        result["agent_action"] = "The details did not match. Say so kindly and let the customer try again or choose another method from get_auth_options."
        if method == "PIN":
            if _tenant() == "WGL":
                result["agent_action"] += " For a forgotten PIN, send nothing yet: offer another allowed method from get_auth_options (Social Security, Tax ID or card last four, not the PIN); only after that passes can a new PIN be set or an email link be sent."
            else:
                result["agent_action"] += " For a forgotten PIN, offer an email reset link with trigger_password_reset (pin_failure_fallback)."
    elif _tenant() == "SEMCO" and _var("channel") == "voice":
        result["agent_action"] = "Three failures. Call set_session_state with _action_trigger live_agent_handoff and _escalation_reason auth_failure_exit and write no closing line; the system speaks the closing explanation and ends the call with no transfer."
    else:
        result["agent_action"] = "Three failures. Say a warm hand-off line, then call set_session_state with _action_trigger live_agent_handoff and _escalation_reason auth_failure_handoff."
    return result


def _validate_secret(account_id: str, method: str, field: str, supplied: str, min_digits: int, max_digits: int) -> dict:
    """Shared body of validate_ssn_last4, validate_taxid_last4 and validate_portal_pin."""
    digits = _digits(supplied)
    if not min_digits <= len(digits) <= max_digits:
        return _validation_error(f"{min_digits} digits" if min_digits == max_digits else f"{min_digits} to {max_digits} digits")
    controls = _controls()
    blocked = _cis_unavailable(controls)
    if blocked:
        return blocked
    resolved = account_id.strip() or _object_var("identity_ctx").get("account_id", "")
    if resolved != _object_var("identity_ctx").get("account_id", ""):
        return _rule_error("only the identified account can be verified", "continue with that account")
    secrets = _get_doc("wgl_auth_secrets", resolved)
    if secrets and _secret_matches(secrets, field, digits):
        return _tier2_pass(method)
    return _tier2_fail(method)


def _validate_taxid_last4_impl(account_id: str, taxid_last4: str) -> dict:
    blocked = _tier2_ready(_var("ssn_tax_flag") == "TAXID", "Tax ID verification applies only when the account flag is TAXID.")
    if blocked:
        return blocked
    return _validate_secret(account_id, "TaxID", "taxid_last4", taxid_last4, 4, 4)
