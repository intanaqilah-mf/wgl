import hashlib
import json
import re
import time


def reset_portal_pin(account_id: str = "", new_pin: str = "", confirm_pin: str = "", tenant: str = "") -> dict:
    """Sets a new portal PIN in the call or chat after the caller passed Social Security, Tax ID or card
    last four verification in this session (a PIN login does not count). Call it first with only
    account_id to check eligibility and open keypad entry on voice; then call it with new_pin and
    confirm_pin (the same digits entered twice). The digits are never repeated back, spoken or logged.
    Before calling this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        new_pin: The new PIN digits, keyed or typed; leave empty on the first (eligibility) call.
        confirm_pin: The same digits entered a second time; REQUIRED together with new_pin.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok and stage ready (eligible, enter the PIN) or changed (the PIN was set); never
        the PIN itself.
    """
    try:
        return _finish("reset_portal_pin", _reset_portal_pin_impl(account_id=account_id, new_pin=new_pin, confirm_pin=confirm_pin))
    except Exception as err:
        return _unexpected_error("reset_portal_pin", err)


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
        f"This caller is not verified yet (auth_status is {current}, {required} is needed). Your next output must be only the function call "
        "transfer_to_agent with agent auth_agent: write no text and call no other tool, because the request is saved and the caller returns here after verification. "
        "Never tell the caller about internal routing or say the payment cannot be processed.",
        auth_required=required,
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


# Rev 24: the PIN is six digits like every seeded PIN; three bad attempts per session end the in-call reset.
_PIN_LENGTH = 6
_PIN_ATTEMPT_LIMIT = 3
# Only these Tier 2 methods count: a caller who knows the old PIN is not the one who forgot it, and a JWT login is not a verification.
_NON_PIN_METHODS = ("SSN", "TaxID", "CCLast4")

_SAFE_TEXT = {
    "pin_changed_sms": {
        "EN": "Washington Gas: the portal PIN on your account was changed. If this was not you, call us right away.",
        "ES": "Washington Gas: se cambi\u00f3 el PIN del portal de su cuenta. Si no fue usted, ll\u00e1menos de inmediato.",
    },
    "pin_entry_privacy_chat": {
        "EN": "Your PIN shows on screen as you type it, so please do not share it anywhere else.",
        "ES": "Su PIN se ve en pantalla mientras lo escribe, as\u00ed que no lo comparta en ning\u00fan otro lugar.",
    },
    "pin_entry_privacy_voice_spoken": {
        "EN": "The keypad is private, so I will wait for it. If you say the digits instead, please make sure nobody can overhear you.",
        "ES": "El teclado es privado, as\u00ed que esperar\u00e9. Si prefiere decir los d\u00edgitos, aseg\u00farese de que nadie pueda o\u00edrle.",
    },
}


def _secret_matches(secrets: dict, field: str, supplied: str) -> bool:
    """Compares a supplied value with the stored secret (salted hash when present, else the stored value)."""
    stored_hash = secrets.get(f"{field}_hash")
    if stored_hash:
        digest = hashlib.sha256(f"{secrets.get('salt', '')}{supplied}".encode()).hexdigest()
        return digest == stored_hash
    stored = secrets.get(field)
    return stored is not None and str(stored) == supplied


def _language_code() -> str:
    """EN or ES from the session language."""
    return "ES" if _var("language", "EN").upper() == "ES" else "EN"


def _text_for(key: str) -> str:
    """The seeded verbatim text for the key in the session language; a built-in safe sentence when the key is not seeded yet."""
    code = _language_code()
    try:
        document = _get_doc("wgl_verbatim_strings", key)
        text = str(document.get("text_es" if code == "ES" else "text_en", "") or "")
        if text.strip() and "{" not in text:
            return text
    except Exception as err:
        print(f"verbatim lookup for {key} failed: {err}")
    return _SAFE_TEXT[key][code]


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


def _verified_by_non_pin(session_doc: dict) -> bool:
    """True only when this session passed SSN, Tax ID or card last four: the session variable (set by the validate tools) and the stored session row must agree."""
    method = _var("auth_method_tier2")
    if _var("auth_status") != "Authenticated" or method not in _NON_PIN_METHODS:
        return False
    stored = str(session_doc.get("auth_method_tier2") or "")
    return not stored or stored == method


def _trivial(pin: str) -> bool:
    """Rejects one repeated digit, straight runs up or down (123456, 654321, 012345) and a repeated short block (121212, 123123)."""
    if len(set(pin)) == 1:
        return True
    steps = {int(pin[i + 1]) - int(pin[i]) for i in range(len(pin) - 1)}
    if steps in ({1}, {-1}):
        return True
    for size in (2, 3):
        if len(pin) % size == 0 and pin == pin[:size] * (len(pin) // size):
            return True
    return False


def _bad_attempt(session_doc: dict, problem: str, action: str) -> dict:
    """Counts a rejected PIN (never storing digits) and returns the validation result with the attempts left."""
    count = int(session_doc.get("pin_reset_failures") or 0) + 1
    _set_var("pin_reset_failures", str(count))
    _commit([_set_write("wgl_sessions", _session_id(), {"tenant": _tenant(), "pin_reset_failures": count})])
    left = max(0, _PIN_ATTEMPT_LIMIT - count)
    result = _error("validation", action, problem=problem, attempts_remaining=left)
    if not left:
        result["agent_action"] = (
            "Too many attempts. Say kindly that the new PIN could not be set right now, offer the email reset link with "
            "trigger_password_reset instead, and never mention the digits."
        )
    return result


def _reset_portal_pin_impl(account_id: str, new_pin: str, confirm_pin: str) -> dict:
    blocked = _degraded_write_block()
    if blocked:
        return blocked
    customer, error = _load_customer(account_id, "Authenticated")
    if error:
        return error
    account = customer["_id"]
    session_doc = _get_doc("wgl_sessions", _session_id())
    if not _verified_by_non_pin(session_doc):
        return _rule_error(
            "a new PIN can be set only after the caller verifies with the Social Security last four digits, the Tax ID "
            "last four digits or the card last four digits in this conversation",
            "ask for one of those with get_auth_options and the matching validate tool, then try again (the old PIN does not count)",
        )
    if int(session_doc.get("pin_reset_failures") or 0) >= _PIN_ATTEMPT_LIMIT:
        return _rule_error("too many PIN attempts were made in this conversation", "offer the email reset link with trigger_password_reset")
    channel = _var("channel", "chat")
    if not str(new_pin).strip() and not str(confirm_pin).strip():
        _set_var("pin_entry_open", "true")
        note = _text_for("pin_entry_privacy_voice_spoken" if channel == "voice" else "pin_entry_privacy_chat")
        how = (
            "Ask the caller to enter the new six digit PIN on the phone keypad and then to enter it a second time to confirm; "
            "keypad digits arrive as text and in dtmf_digits. If keypad entry is not possible, say the privacy note and accept spoken digits."
            if channel == "voice"
            else "Ask the customer to type the new six digit PIN and then type it again to confirm, and give the privacy note in one short sentence."
        )
        return {"ok": True, "stage": "ready", "pin_length": _PIN_LENGTH, "privacy_note": note,
                "agent_action": how + " Never say, repeat or echo the PIN. Use no emoji."}
    raw_first, raw_second = str(new_pin), str(confirm_pin)
    if re.search(r"[A-Za-z]", raw_first + raw_second):
        return _bad_attempt(session_doc, "digits only", "Ask again for the six digits only, entered twice. Never repeat the PIN.")
    first, second = _digits(raw_first), _digits(raw_second)
    if len(first) != _PIN_LENGTH:
        return _bad_attempt(session_doc, f"{_PIN_LENGTH} digits", f"Ask again for a PIN of exactly {_PIN_LENGTH} digits, entered twice. Never repeat the PIN.")
    if first != second:
        return _bad_attempt(session_doc, "the two entries did not match", "Say the two entries did not match and ask for both again. Never repeat the PIN.")
    if _trivial(first):
        return _bad_attempt(session_doc, "too easy to guess", "Say that PIN is too easy to guess (no repeated digits or straight runs such as 123456) and ask for a different one. Never repeat the PIN.")
    secrets = _get_doc("wgl_auth_secrets", account)
    if secrets and _secret_matches(secrets, "portal_pin", first):
        return _bad_attempt(session_doc, "same as the current PIN", "Say the new PIN must be different from the current one and ask for another. Never repeat the PIN.")
    salt = str(secrets.get("salt") or "") or hashlib.sha256(f"{account}{time.time_ns()}".encode()).hexdigest()[:16]
    digest = hashlib.sha256(f"{salt}{first}".encode()).hexdigest()
    writes = [_set_write("wgl_auth_secrets", account, {
        "account_id": account, "tenant": _tenant(), "salt": salt, "portal_pin_hash": digest, "portal_pin": None,
    })]
    if not customer.get("pin_set"):
        writes.append(_set_write("wgl_customers", account, {"pin_set": True}, "must"))
    mobile = str(customer.get("mobile_e164", ""))
    text = _text_for("pin_changed_sms")
    if mobile:
        writes.append(_set_write("wgl_notifications", _new_id(), {
            "account_id": account, "tenant": _tenant(), "type": "pin_changed_sms", "channel": "sms",
            "to_last4": mobile[-4:], "payload": text,
        }, "new"))
    _commit(writes)
    _set_var("pin_set", "true")
    _set_var("pin_entry_open", "false")
    _set_var("pin_reset_done", "true")
    _log_event("pin_reset", {"account_id": account, "channel": channel, "method": _var("auth_method_tier2")})
    return {
        "ok": True, "stage": "changed", "text_sent_to_last4": mobile[-4:] if mobile else "",
        "agent_action": "Say in one short sentence that the PIN was changed and a text was sent to the number on file; never say or repeat "
                        "the PIN. Then offer to continue with what the caller originally wanted, now verified.",
    }
