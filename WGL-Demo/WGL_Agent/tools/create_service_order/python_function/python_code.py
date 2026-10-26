import json
import re
import time
from datetime import date, datetime, timedelta, timezone


def create_service_order(
    account_id: str = "",
    type: str = "",
    address: str = "",
    date: str = "",
    tenant: str = "",
    slot: str = "",
    stop_slot: str = "",
) -> dict:
    """Creates a service order and its appointment for start, stop, transfer, reconnect or meter_test,
    after the customer picked one of the offered slots. A transfer (a move) creates the stop order for
    the old address and the start order for the new address together: two orders, two confirmation
    numbers (stop first, then start) and one confirmation text that carries both. Needs an
    authenticated caller and a read-back of the service address. Before calling this tool, speak a
    brief conversational pacing phrase while it runs.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        type: start, stop, transfer, reconnect or meter_test (REQUIRED).
        address: Standardized service address, read back to the customer (REQUIRED).
        date: Appointment date, YYYY-MM-DD (REQUIRED).
        tenant: Injected by the system from tenant_id; leave empty.
        slot: The chosen slot exactly as returned by fetch_technician_slots, for example
            2026-10-21 8am-12pm (REQUIRED). For a transfer this is the slot of the start at the new address.
        stop_slot: Transfer only: the chosen slot for closing the old address, exactly as returned by
            fetch_technician_slots with type stop. Leave empty to stop in the same slot as the start.

    Returns:
        dict with ok, confirmation_numbers (stop first for a transfer), orders (type, confirmation_number,
        date, window and address of each order), appointment_id, appointment_ids, date, window, address,
        confirmation_texts_sent and, for a transfer, agent_action. Each booked window is consumed: a slot
        that is already taken is refused. A start or stop earlier than the business-day notice is refused
        with the earliest bookable date (offer later slots). A stop needs get_stop_eligibility to have said
        eligible first. A start at an address that check_service_territory found outside the territory (or
        that check_active_service_at_address found active and occupied) is refused. Submitting the same
        order twice in one session returns the first confirmation with duplicate_suppressed true.
    """
    try:
        return _finish("create_service_order", _create_service_order_impl(account_id=account_id, type=type, address=address, date=date, slot=slot, stop_slot=stop_slot))
    except Exception as err:
        return _unexpected_error("create_service_order", err)


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


def _tenant_config() -> dict:
    """Reads this tenant's wgl_tenant_config document."""
    return _get_doc("wgl_tenant_config", _tenant())


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


# BR-GU-022: no appointments on weekends, public holidays or more than 30 days ahead.
_MAX_BOOKING_DAYS = 30


def _holidays() -> list:
    """Public holidays of this tenant as ISO dates, from the tenant config."""
    return _tenant_config().get("holidays") or []


def _appointment_date_error(day_text: str, holidays: list) -> dict:
    """Returns an error result when the date breaks the booking rules, else an empty dict."""
    try:
        day = date.fromisoformat(str(day_text)[:10])
    except ValueError:
        return _validation_error("the date in year-month-day form")
    today = _today()
    if day < today:
        return _rule_error("a date in the past cannot be booked", "choose a later weekday")
    if day.weekday() >= 5:
        return _rule_error("appointments are not available on weekends", "choose a weekday")
    if day.isoformat() in holidays:
        return _rule_error("appointments are not available on public holidays", "choose another weekday")
    if (day - today).days > _MAX_BOOKING_DAYS:
        return _rule_error("appointments can be booked up to 30 days ahead", "choose an earlier date")
    return {}


def _slot_documents(slot_type: str) -> list:
    """All technician slot days of one type for this tenant."""
    return _run_query("wgl_technician_slots", {"tenant": _tenant(), "type": slot_type}, 60)


def _split_slot(slot: str) -> tuple:
    """Splits 'YYYY-MM-DD window' into (date, window)."""
    day, _, window = str(slot).strip().partition(" ")
    return day, window.strip()


def _slot_is_open(slot_type: str, day: str, window: str) -> bool:
    """True when the technician slot day lists the requested window."""
    return any(
        window == item.get("window")
        for day_doc in _slot_documents(slot_type) if day_doc.get("date") == day
        for item in day_doc.get("slots") or []
    )


# Service notice rule (service agent guideline): start and stop need at least two business days notice; weekends and holidays do not count.
_DEFAULT_NOTICE_BUSINESS_DAYS = 2
_NOTICE_TYPES = ("start", "stop")


def _notice_days() -> int:
    """Business days of notice a start or stop needs: the tenant config value when it has one, else two."""
    config = _tenant_config()
    for key in ("service_notice_business_days", "notice_business_days", "booking_notice_business_days"):
        try:
            days = int(config.get(key))
        except (TypeError, ValueError):
            continue
        if days >= 0:
            return days
    return _DEFAULT_NOTICE_BUSINESS_DAYS


def _earliest_notice_day(holidays: list) -> date:
    """The first date that satisfies the notice rule, counting weekdays that are not holidays from today."""
    day = _today()
    counted = 0
    needed = _notice_days()
    while counted < needed:
        day += timedelta(days=1)
        if day.weekday() < 5 and day.isoformat() not in holidays:
            counted += 1
    return day


def _notice_error(kind: str, day_text: str, holidays: list) -> dict:
    """Refuses a start or stop earlier than the notice rule allows and tells the agent to offer later slots."""
    if kind not in _NOTICE_TYPES:
        return {}
    try:
        day = date.fromisoformat(str(day_text)[:10])
    except ValueError:
        return {}
    earliest = _earliest_notice_day(holidays)
    if day >= earliest:
        return {}
    days = _notice_days()
    return _error(
        "business_rule",
        f"Starting or stopping service needs at least {days} business days notice (weekends and holidays do not count), so "
        f"the earliest date that can be booked is {earliest.isoformat()}. Explain this warmly, do not book the earlier date, "
        f"call fetch_technician_slots with date {earliest.isoformat()} and offer those later slots.",
        earliest_date=earliest.isoformat(),
        notice_business_days=days,
    )


# A technician slot window serves one appointment: wgl_slot_bookings holds one flat row per booked window (status booked or released).
def _booking_id(slot_type: str, day: str, window: str) -> str:
    """Document id of the booking row that consumes one technician slot window."""
    return "_".join(re.findall(r"[A-Za-z0-9]+", f"{_tenant()}_{slot_type}_{day}_{window}"))


def _slot_booking(slot_type: str, day: str, window: str) -> dict:
    """The booking row of one slot window; empty when it was never booked."""
    return _get_doc("wgl_slot_bookings", _booking_id(slot_type, day, window))


def _slot_taken(slot_type: str, day: str, window: str) -> bool:
    """True when the slot window is already booked by an appointment."""
    return _slot_booking(slot_type, day, window).get("status") == "booked"


def _booking_write(slot_type: str, day: str, window: str, appointment_id: str, account_id: str) -> dict:
    """The write that books one slot window for an appointment (a released window is booked again)."""
    existing = _slot_booking(slot_type, day, window)
    fields = {
        "tenant": _tenant(), "type": slot_type, "date": day, "window": window, "status": "booked",
        "appointment_id": appointment_id, "account_id": account_id, "session_id": _session_id(),
    }
    return _set_write("wgl_slot_bookings", _booking_id(slot_type, day, window), fields, "must" if existing else "new")


def _address_key(address: str) -> str:
    """Normalized key of an address, the same one check_service_territory and check_active_service_at_address record."""
    return "_".join(re.findall(r"[a-z0-9]+", str(address).lower()))


def _address_check_error(order_type: str, kind: str, address: str) -> dict:
    """Refuses a start at an address that a check in this session already found outside the territory (or, for a plain start, already active and occupied)."""
    if kind != "start":
        return {}
    session = _get_doc("wgl_sessions", _session_id())
    key = _address_key(address)
    if key and session.get("territory_address") == key and session.get("territory_result") == "out":
        return _error(
            "business_rule",
            "That address is outside the service area, so service cannot be started there and nothing was booked. Explain this kindly "
            "and offer a representative or a callback.",
        )
    if order_type == "start" and key and session.get("active_address") == key and session.get("active_service") is True and session.get("active_occupied") is True:
        return _error(
            "business_rule",
            "Gas service is already active and occupied at that address, so a new start cannot be booked. Offer a transfer of service "
            "instead (stop service at the old address and start it at this one).",
        )
    return {}


def _stop_eligibility_error(customer: dict, order_type: str) -> dict:
    """A stop (or the stop leg of a transfer) needs an eligible account; a plain stop also needs get_stop_eligibility to have run."""
    if order_type not in ("stop", "transfer"):
        return {}
    if customer.get("stop_eligible") is not None:
        eligible = bool(customer["stop_eligible"])
    else:
        eligible = customer.get("credit_status") != "Collections" and not customer.get("shutoff_date")
    if not eligible:
        return _error(
            "business_rule",
            "This account cannot stop service online (it is in collections or has a shutoff notice) and nothing was booked. Explain "
            "kindly and offer a representative.",
        )
    if order_type == "stop":
        session = _get_doc("wgl_sessions", _session_id())
        if session.get("stop_eligibility_account") != customer["_id"] or session.get("stop_eligibility_result") != "eligible":
            return _error(
                "business_rule",
                "The stop eligibility check has not been run for this account in this conversation. Call get_stop_eligibility "
                "quietly now, and when it says eligible call create_service_order again with the same details.",
            )
    return {}


def _order_legs(order_type: str, address: str, day: str, window: str, stop_slot: str) -> list:
    """The orders to create as (kind, address, day, window).

    A transfer closes the old address and opens the new one: the stop takes the stop_slot when one is
    given, otherwise the same day and window as the start.
    """
    if order_type != "transfer":
        return [(order_type, address, day, window)]
    closing = _object_var("service_ctx").get("stop_address") or _object_var("identity_ctx").get("service_address", "")
    stop_day, stop_window = _split_slot(stop_slot) if stop_slot.strip() else (day, window)
    return [("stop", closing, stop_day, stop_window), ("start", address, day, window)]


def _leg_slot_error(kind: str, day: str, window: str, holidays: list, order_type: str = "", where: str = "") -> dict:
    """Returns an error result when one order's date or slot cannot be booked, else an empty dict."""
    if not day or not window:
        return _validation_error("the chosen appointment slot")
    problem = _appointment_date_error(day, holidays)
    if problem:
        return problem
    problem = _notice_error(kind, day, holidays)
    if problem:
        return problem
    if not _slot_is_open(kind, day, window) or _slot_taken(kind, day, window):
        return _rule_error("that time slot is no longer available", "choose another slot")
    return _address_check_error(order_type or kind, kind, where)


def _service_ctx_patch(legs: list) -> dict:
    """Addresses and dates of the orders for service_ctx."""
    patch = {}
    for kind, where, day, _window in legs:
        if kind == "stop":
            patch.update({"stop_address": where, "stop_date": day})
        if kind == "start":
            patch.update({"start_address": where, "start_date": day})
    return patch


_TRANSFER_AGENT_ACTION = (
    "Both orders were created. Say both confirmation numbers in one statement, the stop order first and then the "
    "start order, each with its date. Say once that one confirmation text with both appointments is on its way; "
    "never promise two texts."
)


def _existing_orders(customer: dict, legs: list) -> list:
    """The orders this session already created for the same account, type, date, window and address (one per leg), else an empty list."""
    rows = _run_query("wgl_service_orders", {"account_id": customer["_id"], "source_session": _session_id()}, 50)
    found = []
    for kind, where, day, window in legs:
        match = next((
            row for row in rows
            if row.get("type") == kind and row.get("date") == day and row.get("window") == window
            and _address_key(row.get("address", "")) == _address_key(where)
            and _get_doc("wgl_appointments", f"APT-{row['_id']}").get("status") != "cancelled"
        ), None)
        if not match:
            return []
        found.append(match)
    return found


def _order_result(order_type: str, orders: list, appointment_ids: list, day: str, window: str, address: str, duplicate: bool = False) -> dict:
    """The success result for freshly created orders, or the first result again for a double submit."""
    result = {
        "ok": True, "confirmation_numbers": [order["confirmation_number"] for order in orders], "orders": orders,
        "appointment_id": appointment_ids[0], "appointment_ids": appointment_ids, "date": day, "window": window,
        "address": address, "confirmation_texts_sent": 1,
    }
    if order_type == "transfer":
        result["agent_action"] = _TRANSFER_AGENT_ACTION
    if duplicate:
        result["duplicate_suppressed"] = True
        result["agent_action"] = (result.get("agent_action", "") + " " if result.get("agent_action") else "") + (
            "These orders were already created earlier in this conversation and nothing new was booked. Give the same confirmation "
            "number(s) again; never say they were booked twice."
        )
    return result


def _create_service_order_impl(account_id: str, type: str, address: str, date: str, slot: str, stop_slot: str) -> dict:
    """Validates the request, then writes the orders, their appointments and one confirmation text in one commit."""
    if type not in ("start", "stop", "transfer", "reconnect", "meter_test"):
        return _validation_error("an order type of start, stop, transfer, reconnect or meter_test")
    blocked = _degraded_write_block()
    if blocked:
        return blocked
    customer, error = _load_customer(account_id, "Authenticated")
    if error:
        return error
    if not address.strip():
        return _validation_error("the service address")
    holidays = _holidays()
    problem = _appointment_date_error(date, holidays)
    if problem:
        return problem
    day, window = _split_slot(slot)
    if not window or day != date.strip()[:10]:
        return _validation_error("the chosen appointment slot")
    legs = _order_legs(type, address.strip(), day, window, stop_slot)
    earlier = _existing_orders(customer, legs)
    if earlier:
        return _order_result(
            type, [{"type": row["type"], "confirmation_number": row.get("confirmation_number") or row["_id"], "date": row["date"], "window": row["window"], "address": row["address"]} for row in earlier],
            [f"APT-{row['_id']}" for row in earlier], day, window, address.strip(), duplicate=True,
        )
    problem = _stop_eligibility_error(customer, type)
    if problem:
        return problem
    for kind, where, leg_day, leg_window in legs:
        problem = _leg_slot_error(kind, leg_day, leg_window, holidays, type, where)
        if problem:
            return problem
    if type in ("start", "transfer") and not _controls().get("start_service_api_available", True):
        return _error("business_rule", "The start-service order system is unavailable. Collect the details and call create_callback_ticket with reason start_service.")
    if type == "reconnect" and float(customer.get("arrears") or 0) > 0:
        return _rule_error("the past due balance must be paid before service can be reconnected", "make a payment first")
    if any(not where for _, where, _, _ in legs):
        return _validation_error("the address being closed")
    orders, writes, appointment_ids = [], [], []
    for kind, where, leg_day, leg_window in legs:
        reference = _reference_number("confirmations")
        orders.append({"type": kind, "confirmation_number": reference, "date": leg_day, "window": leg_window, "address": where})
        writes.append(_set_write("wgl_service_orders", reference, {
            "account_id": customer["_id"], "tenant": _tenant(), "type": kind, "address": where,
            "date": leg_day, "window": leg_window, "confirmation_number": reference, "source_session": _session_id(),
        }, "new"))
        appointment_ids.append(f"APT-{reference}")
        writes.append(_set_write("wgl_appointments", appointment_ids[-1], {
            "account_id": customer["_id"], "tenant": _tenant(), "type": kind, "slot": f"{leg_day} {leg_window}", "status": "scheduled",
        }, "new"))
        writes.append(_booking_write(kind, leg_day, leg_window, appointment_ids[-1], customer["_id"]))
    confirmations = [order["confirmation_number"] for order in orders]
    writes.append(_set_write("wgl_notifications", _new_id(), {
        "account_id": customer["_id"], "tenant": _tenant(), "type": "service_confirmation", "channel": "sms",
        "reference": ", ".join(confirmations),
    }, "new"))
    _commit(writes)
    merge_ctx("service_ctx", dict({"active_service_order_id": confirmations[0], "slot_confirmed": True}, **_service_ctx_patch(legs)))
    merge_ctx("appointment_ctx", {"appointment_id": appointment_ids[0], "slot": f"{legs[0][2]} {legs[0][3]}", "status": "scheduled"})
    return _order_result(type, orders, appointment_ids, day, window, address.strip())
