import json
import re
import time
from datetime import date, datetime, timedelta, timezone


def get_verbatim_string(key: str = "", language: str = "", params: str = "", deliver: str = "") -> dict:
    """Looks up a legally required verbatim string by key in the caller's language. The system speaks
    the exact text itself on the next step, so never repeat, paraphrase or add to it. Placeholders
    such as brand, date and hours are filled automatically. No spoken pacing is needed; this tool is
    silent and instant.

    Args:
        key: Verbatim key such as quality_monitoring_consent, greeting_main_guest,
            emergency_safety_instruction, empathy_protocol, live_agent_handoff or after_hours_message
            (REQUIRED).
        language: EN or ES; empty uses the current session language.
        params: Optional JSON object text with extra placeholder values; usually empty. Values the configuration or the
            account already supplies (brand, phone numbers, hours, fees, limits, emergency number) are never overridden.
        deliver: Set by the system to callback when a callback speaks the text; leave empty.

    Returns:
        dict with ok, key, status (confirmed or demo_placeholder), language, text and
        agent_action.
    """
    try:
        return _finish("get_verbatim_string", _get_verbatim_string_impl(key=key, language=language, params=params, deliver=deliver))
    except Exception as err:
        return _unexpected_error("get_verbatim_string", err)


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


def _tenant_config() -> dict:
    """Reads this tenant's wgl_tenant_config document."""
    return _get_doc("wgl_tenant_config", _tenant())


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


# Month names used to fill the {date} placeholder of a verbatim string.
_MONTHS = {
    "EN": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
    "ES": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
}


def _language_code(language: str) -> str:
    """Maps a language hint to EN or ES, falling back to the session language."""
    wanted = str(language).strip() or _var("language", "EN")
    return "ES" if wanted.upper().startswith(("ES", "SPAN")) else "EN"


def _spoken_date(day: date, language_code: str) -> str:
    """A date spoken-style: October 17, 2026 or 17 de octubre de 2026."""
    month = _MONTHS[language_code][day.month - 1]
    if language_code == "ES":
        return f"{day.day} de {month} de {day.year}"
    return f"{month} {day.day}, {day.year}"


def _spoken_date_short(day: date, language_code: str) -> str:
    """A date spoken-style without the year, for greetings: October 17 or 17 de octubre."""
    month = _MONTHS[language_code][day.month - 1]
    return f"{day.day} de {month}" if language_code == "ES" else f"{month} {day.day}"


def _long_date(language_code: str) -> str:
    """Today's date spoken-style."""
    return _spoken_date(day=_today(), language_code=language_code)


def _jurisdiction() -> str:
    """Jurisdiction of the caller: identity_ctx, else the customer record; empty when unknown."""
    identity = _object_var("identity_ctx")
    known = str(identity.get("jurisdiction") or "").strip().upper()
    if known:
        return known
    account_id = str(identity.get("account_id", ""))
    customer = _get_doc("wgl_customers", account_id) if account_id else {}
    return str(customer.get("jurisdiction") or "").strip().upper()


def _fee_values(config: dict) -> dict:
    """Card fee and limit placeholders for the caller's jurisdiction.

    A fee value exists only where the jurisdiction charges one: Virginia has none, so no fee value is returned.
    """
    entry = (config.get("convenience_fees") or {}).get(_jurisdiction())
    entry = entry if isinstance(entry, dict) else {}
    values = {}
    for kind in ("residential", "commercial"):
        fee = entry.get(kind)
        limit = entry.get(f"{kind}_limit")
        if isinstance(fee, (int, float)) and fee > 0:
            values[f"fee_{kind}"] = f"${fee:.2f}"
        if isinstance(limit, (int, float)) and limit > 0:
            values[f"limit_{kind}"] = f"${limit:,.0f}"
    return values


def _verbatim_values(language_code: str) -> dict:
    """Placeholder values for verbatim strings: brand, date, customer name, tenant config entries and card fees."""
    config = _tenant_config()
    values = {
        "brand": config.get("brand_name", ""),
        "date": _long_date(language_code),
        "customer_name": _object_var("identity_ctx").get("customer_name", ""),
    }
    for prefix, group in (("phone", "demo_phones"), ("hours", "hours")):
        for name, value in (config.get(group) or {}).items():
            values[f"{prefix}_{name}"] = value
    values.update(_fee_values(config=config))
    values["emergency_number"] = values.get("phone_emergency", "")
    return values


# Values that come from the tenant configuration (brand, phone numbers, hours, fees, limits, the emergency number) are legally
# required wording: a key the configuration owns is never taken from the model-supplied params (the configuration wins).
_CONFIG_OWNED_PREFIXES = ("phone_", "hours_", "fee_", "limit_")
_CONFIG_OWNED_KEYS = ("brand", "emergency_number")


def _allowed_params(params: str, values: dict) -> dict:
    """The model-supplied placeholder values without any key the configuration or the account already supplies."""
    allowed = {}
    for key, value in _parse_params(params).items():
        owned = key in _CONFIG_OWNED_KEYS or key.startswith(_CONFIG_OWNED_PREFIXES) or bool(str(values.get(key, "")).strip())
        if not owned:
            allowed[key] = value
    return allowed


def _parse_params(params: str) -> dict:
    """Parses the optional JSON object of extra placeholder values."""
    if not str(params).strip():
        return {}
    try:
        parsed = json.loads(params)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Keys whose placeholder values come from the loaded account instead of the model.
_GREETING_KEY = "greeting_main_identified"
_GUEST_KEY = "greeting_main_guest"

# Rev 22 standard welcome. On voice the callback still asks for greeting_main_identified / greeting_main_guest and this
# tool picks the wording from the account data; a tenant that has no such text (tenant_scope) keeps the base key.
_VOICE_GUEST_KEY = "greeting_voice_guest"
_VOICE_IDENTIFIED_KEYS = {
    "default": "greeting_voice_identified",
    "due_soon": "greeting_voice_identified_due_soon",
    "past_due": "greeting_voice_identified_past_due",
    "estimated": "greeting_voice_identified_estimated",
}
_DUE_SOON_DAYS = 5
# Chat: the one personal line after the customer is identified and verified (name only, no figures).
_WELCOME_KEYS = ("welcome_verified_idle", "welcome_verified_payment", "welcome_verified_task")
_NAME_KEYS = tuple(_VOICE_IDENTIFIED_KEYS.values()) + _WELCOME_KEYS
_WNA_KEYS = ("wna_explanation_surcharge", "wna_explanation_credit")
# Rev 23: the keys that may carry a chat-screen wording (fields text_chat_en / text_chat_es on the document). The chat wording is
# read only when the channel is chat; voice always reads text_en / text_es exactly as before. Legal authorizations, fee texts,
# program terms and safety texts are not in this list and stay verbatim on chat too.
_CHAT_VARIANT_KEYS = _WNA_KEYS

# Card fee keys and the placeholder each one needs; the text is refused where the jurisdiction charges no fee.
_FEE_KEYS = {"convenience_fee_residential": "fee_residential", "convenience_fee_commercial": "fee_commercial"}

# Self-service topics spoken in the after-hours text when the model passes none (the instruction gives no params).
_SELF_SERVICE_DEFAULT = {"EN": "your balance, a payment or general account questions", "ES": "su saldo, un pago o preguntas generales sobre su cuenta"}

# A placeholder that is still unfilled in the final text; nothing containing one is ever spoken.
_UNFILLED = re.compile(r"\{\w+\}")

# Spoken in place of the first name when the account holds no name.
_NEUTRAL_NAME = {"EN": "there", "ES": "estimado cliente"}


def _business_name(account_id: str) -> str:
    """The business name of a commercial account, from the customer record; empty when there is none."""
    customer = _get_doc("wgl_customers", account_id) if account_id else {}
    return str(customer.get("business_name") or "").strip()


def _first_name(language_code: str) -> str:
    """First name of the identified caller; the business name for a commercial account; a neutral word without a name."""
    identity = _object_var("identity_ctx")
    full_name = str(identity.get("customer_name", "")).strip()
    if identity.get("customer_type") == "Commercial":
        business = _business_name(str(identity.get("account_id", "")))
        if business or full_name:
            return business or full_name
    if not full_name:
        return _NEUTRAL_NAME[language_code]
    return full_name.split()[0]


def _account_data() -> dict:
    """Account figures for placeholders: the session object, else the customer record read from Firestore."""
    account = _object_var("account_ctx")
    if account.get("due_date") or account.get("wna_amount") is not None:
        return account
    account_id = str(_object_var("identity_ctx").get("account_id", ""))
    customer = _get_doc("wgl_customers", account_id) if account_id else {}
    if not customer:
        return account
    return {
        "balance": customer.get("balance"),
        "due_date": customer.get("due_date", ""),
        "arrears": customer.get("arrears"),
        "wna_amount": customer.get("wna_amount"),
        "wna_direction": customer.get("wna_direction"),
    }


def _greeting_values(language_code: str) -> dict:
    """first_name always; amount and due_date only for a balance due today or later (the bill sentence is optional)."""
    values = {"first_name": _first_name(language_code=language_code)}
    account = _account_data()
    try:
        balance = round(float(account.get("balance") or 0), 2)
        due = date.fromisoformat(str(account.get("due_date") or "")[:10])
    except ValueError:
        return values
    if balance > 0 and due >= _today():
        values["amount"] = f"{balance:,.2f}"
        values["due_date"] = _spoken_date(day=due, language_code=language_code)
        values["due_date_short"] = _spoken_date_short(day=due, language_code=language_code)
    return values


def _latest_read_type(account_id: str) -> str:
    """Read type (actual or estimated) of the latest bill on or before today: this month's period, else the two before."""
    today = _today()
    year, month = today.year, today.month
    for _ in range(3):
        row = _get_doc("wgl_billing_periods", f"{account_id}_{year:04d}-{month:02d}")
        if row:
            return str(row.get("read_type") or "")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return ""


def _greeting_reason() -> str:
    """Why the voice greeting may mention the account: past_due, due_soon, estimated, or default (no real reason).

    Priority past due, due within five days, estimated latest read. An active outage on the caller's ZIP keeps the plain
    greeting, because the outage notice comes first. The greeting never reads an amount.
    """
    if _var("outage_active", "false").lower() == "true":
        return "default"
    account = _account_data()
    try:
        balance = round(float(account.get("balance") or 0), 2)
    except (TypeError, ValueError):
        balance = 0.0
    try:
        arrears = round(float(account.get("arrears") or 0), 2)
    except (TypeError, ValueError):
        arrears = 0.0
    try:
        due = date.fromisoformat(str(account.get("due_date") or "")[:10])
    except ValueError:
        due = None
    today = _today()
    if arrears > 0 or (balance > 0 and due is not None and due < today):
        return "past_due"
    if balance > 0 and due is not None and (due - today).days <= _DUE_SOON_DAYS:
        return "due_soon"
    account_id = str(_object_var("identity_ctx").get("account_id", ""))
    if account_id and _latest_read_type(account_id).lower() == "estimated":
        return "estimated"
    return "default"


def _tenant_allows(document: dict) -> bool:
    """True when the verbatim document exists and its tenant_scope covers this tenant."""
    return bool(document) and str(document.get("tenant_scope") or "BOTH").upper() in ("BOTH", _tenant())


def _verbatim_document(key: str) -> dict:
    """The verbatim document of the key: this tenant's override (id suffix) first, else the shared one."""
    document = _get_doc("wgl_verbatim_strings", f"{key}__{_tenant()}") if _tenant() != "WGL" else {}
    return document or _get_doc("wgl_verbatim_strings", key)


def _voice_greeting_key(key: str) -> str:
    """The voice wording for the greeting key, chosen from the account data; any other key or channel is unchanged."""
    if _var("channel") != "voice" or key not in (_GREETING_KEY, _GUEST_KEY):
        return key
    if key == _GUEST_KEY:
        candidate = _VOICE_GUEST_KEY
    else:
        candidate = _VOICE_IDENTIFIED_KEYS[_greeting_reason()]
    return candidate if _tenant_allows(_verbatim_document(candidate)) else key


def _wna_values() -> dict:
    """Weather normalization amount (always positive; the key says surcharge or credit) from the loaded account."""
    amount = _account_data().get("wna_amount")
    try:
        return {"amount": f"{abs(float(amount)):,.2f}"} if amount is not None else {}
    except (TypeError, ValueError):
        return {}


def _account_values(key: str, language_code: str) -> dict:
    """Placeholder values that come from the account in this session, for the keys that need them."""
    if key == _GREETING_KEY:
        return _greeting_values(language_code=language_code)
    if key in _NAME_KEYS:
        return {k: v for k, v in _greeting_values(language_code=language_code).items() if k in ("first_name", "due_date_short")}
    if key in _WNA_KEYS:
        return _wna_values()
    return {}


def _drop_unfilled_sentences(text: str, values: dict) -> str:
    """Removes the sentences that still hold a placeholder for which no value exists."""
    sentences = re.split(r"(?<=[.?!])\s+", text)
    kept = [item for item in sentences if all(name in values for name in re.findall(r"\{(\w+)\}", item))]
    return " ".join(kept)


def _get_verbatim_string_impl(key: str, language: str, params: str, deliver: str = "") -> dict:
    if not key.strip():
        return _validation_error("the verbatim string key")
    language_code = _language_code(language)
    key = _voice_greeting_key(key)
    document = _verbatim_document(key)
    if not document:
        return _error("validation", f"No verbatim string exists for key {key}. Check the key name and try again.")
    if str(document.get("tenant_scope") or "BOTH").upper() not in ("BOTH", _tenant()):
        return _error(
            "business_rule",
            "That verbatim text is not available for this tenant. Do not say it or anything like it; continue "
            "without it.",
        )
    text = document.get("text_es") if language_code == "ES" else document.get("text_en")
    text = text or document.get("text_en", "")
    if key in _CHAT_VARIANT_KEYS and _var("channel") == "chat":
        chat_text = document.get("text_chat_es") if language_code == "ES" else document.get("text_chat_en")
        text = chat_text or text
    values = _verbatim_values(language_code)
    if key in _FEE_KEYS and _FEE_KEYS[key] not in values:
        return _error(
            "business_rule",
            "No card convenience fee applies to this customer's jurisdiction (Virginia customers are not charged). "
            "Do not say this text or anything like it and do not mention a convenience fee; continue with the next step.",
        )
    values.update(_allowed_params(params, values))
    values.update(_account_values(key=key, language_code=language_code))
    if key in _WNA_KEYS and not values.get("amount"):
        return _error(
            "business_rule",
            "No weather normalization amount is on file for this account. Do not mention a weather normalization "
            "charge or credit.",
        )
    if key == "after_hours_message" and not str(values.get("self_service_options", "")).strip():
        values["self_service_options"] = _SELF_SERVICE_DEFAULT[language_code]
    if key == _GREETING_KEY:
        text = _drop_unfilled_sentences(text=text, values=values)
    filled = re.sub(r"\{(\w+)\}", lambda match: str(values.get(match.group(1), match.group(0))), text)
    leftover = sorted(set(_UNFILLED.findall(filled)))
    if leftover:
        _log_event("verbatim_unfilled_refused", {"key": key, "language": language_code, "placeholders": ",".join(leftover)})
        return _error(
            "business_rule",
            "This text could not be completed for this customer, so it must not be spoken. Do not say the text or "
            "anything like it, do not read out any placeholder, and continue with the next step of the conversation.",
        )
    if key == "amp_enrolment_disclosure":
        merge_ctx("hardship_ctx", {"amp_offered": True, "amp_disclosure_emitted": True})
    if key == "payment_arrangement_disclosure":
        merge_ctx("hardship_ctx", {"pa_disclosure_emitted": True})
    _log_event("verbatim_emitted", {"key": key, "language": language_code, "status": document.get("status", "")})
    if deliver != "callback":
        _set_var("_verbatim_pending", f"{key}|{params}" if str(params).strip() else key)
    return {
        "ok": True,
        "key": key,
        "status": document.get("status", ""),
        "language": language_code,
        "text": filled,
        "agent_action": "The system speaks this exact text on the next step. Do not repeat, paraphrase or add to "
                        "it. Wait for the customer's reply before continuing.",
    }
