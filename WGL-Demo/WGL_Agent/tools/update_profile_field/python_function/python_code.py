import json
import re
import time


def update_profile_field(account_id: str = "", field: str = "", value: str = "", tenant: str = "") -> dict:
    """Updates the phone number, email or mailing address on the account after the customer confirmed
    the change. A name change is never done here and always goes to a representative. Needs an
    authenticated caller. Before calling this tool, speak a brief conversational pacing phrase while
    it runs.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        field: phone, email or mailing_address (REQUIRED).
        value: The new value (REQUIRED); a mailing address should be validated first.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, field, previous_value and new_value (phone shown as last 4 digits only, email masked).
    """
    try:
        return _finish("update_profile_field", _update_profile_field_impl(account_id=account_id, field=field, value=value))
    except Exception as err:
        return _unexpected_error("update_profile_field", err)


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


# Toll-free area codes can never be a customer's own number (800 and 888 numbers are test numbers in demos).
_TOLL_FREE_AREA_CODES = ("800", "833", "844", "855", "866", "877", "888")


def _e164(raw: str) -> str:
    """Normalizes a phone number to +1XXXXXXXXXX; returns an empty string when it is not a valid NANP number.

    Valid means ten digits (or eleven with a leading 1) where the area code and the exchange start with 2 to 9, neither is an
    N11 service code, the area code is not toll free and the number is not one repeated digit.
    """
    digits = _digits(raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    area, exchange = digits[:3], digits[3:6]
    if area[0] not in "23456789" or exchange[0] not in "23456789":
        return ""
    if area[1:] == "11" or exchange[1:] == "11" or area in _TOLL_FREE_AREA_CODES or len(set(digits)) == 1:
        return ""
    return f"+1{digits}"


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


def _degraded_write_block() -> dict:
    """Blocks transactional tools while the app is in degraded mode (BR-GU-047)."""
    if _var("degraded_mode", "false").lower() != "true":
        return {}
    return _error(
        "CIS_DOWN",
        "Transactions are unavailable right now. Say the account systems are temporarily unavailable "
        "and offer a representative or a callback shortly.",
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
        return {}, _error("business_rule", "Only the identified account can be used. Leave account_id empty: it is filled in automatically.")
    customer = _get_doc("wgl_customers", resolved)
    if not customer or customer.get("tenant") != _tenant():
        return {}, _error("business_rule", "No matching account was found. Ask the customer to confirm the details and try again.")
    return customer, {}


# Profile fields the customer may change and the stored field each one maps to.
_PROFILE_FIELDS = {"phone": "mobile_e164", "email": "email_on_file", "mailing_address": "mailing_address"}


def _clean_profile_value(field: str, value: str) -> str:
    """Validates and normalizes the new value; returns an empty string when it is not valid."""
    if field == "phone":
        number = _e164(value)
        # NANP: the area code and the exchange start with 2 to 9
        return number if number and number[2] in "23456789" and number[5] in "23456789" else ""
    if field == "email":
        return value.strip() if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()) else ""
    return value.strip()


def _shown(field: str, text: str) -> str:
    """Phone numbers are shown as the last 4 digits only and an email address is masked (first letter and domain)."""
    if field == "email":
        local, _, domain = str(text).partition("@")
        return f"{local[:1]}***@{domain}" if domain else ""
    return text[-4:] if field == "phone" else text


def _phone_changed_text() -> str:
    """The text to the old number: the seeded phone_changed_sms verbatim text, else a built-in safe sentence."""
    spanish = _var("language", "EN").upper() == "ES"
    try:
        document = _get_doc("wgl_verbatim_strings", "phone_changed_sms")
        text = str(document.get("text_es" if spanish else "text_en", "") or "")
        if text.strip() and "{" not in text:
            return text
    except Exception as err:
        print(f"verbatim lookup for phone_changed_sms failed: {err}")
    if spanish:
        return "Washington Gas: el n\u00famero de tel\u00e9fono de su cuenta fue cambiado. Si no fue usted, ll\u00e1menos de inmediato."
    return "Washington Gas: the phone number on your account was changed. If this was not you, call us right away."


def _locked_block(customer: dict) -> dict:
    """Refuses a change while the online account is locked: the caller must unlock it first (unlock_account)."""
    if not customer.get("account_locked"):
        return {}
    return _error(
        "business_rule",
        "The online account is locked, so this change is refused until it is unlocked. Offer to unlock it now: the caller must be "
        "verified, then call unlock_account, then repeat the change.",
        account_locked=True,
    )


def _update_profile_field_impl(account_id: str, field: str, value: str) -> dict:
    if field == "name":
        return _error("business_rule", "A name change always goes to a representative. Offer a warm hand-off with set_session_state; do not change it yourself.")
    if field not in _PROFILE_FIELDS:
        return _validation_error("phone, email or mailing_address")
    blocked = _degraded_write_block()
    if blocked:
        return blocked
    customer, error = _load_customer(account_id, "Authenticated")
    if error:
        return error
    locked = _locked_block(customer)
    if locked:
        return locked
    cleaned = _clean_profile_value(field, value)
    if not cleaned:
        return _validation_error(f"a valid {field.replace('_', ' ')}")
    stored_key = _PROFILE_FIELDS[field]
    previous = customer.get(stored_key, "")
    if field == "phone" and cleaned == previous:
        return _validation_error("a number different from the one on file")
    writes = [_set_write("wgl_customers", customer["_id"], {stored_key: cleaned}, "must")]
    notified = bool(field == "phone" and previous)
    if notified:
        # Rev 24: the OLD number gets a text that it was replaced (mock); the text carries no digits of the new number.
        writes.append(_set_write("wgl_notifications", _new_id(), {
            "account_id": customer["_id"],
            "tenant": _tenant(),
            "type": "phone_changed_sms",
            "channel": "sms",
            "to_last4": previous[-4:],
            "payload": _phone_changed_text(),
        }, "new"))
    _commit(writes)
    result = {"ok": True, "field": field, "previous_value": _shown(field, previous), "new_value": _shown(field, cleaned)}
    if field == "phone":
        result["old_number_notified"] = notified
    return result
