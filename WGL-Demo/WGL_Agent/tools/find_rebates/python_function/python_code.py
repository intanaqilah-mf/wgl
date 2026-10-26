import json
import time
from datetime import date, datetime, timedelta, timezone


def find_rebates(account_id: str = "", topic: str = "", tenant: str = "") -> dict:
    """Finds the published Washington Gas energy-efficiency rebates that fit the identified customer's state
    and home or equipment, with amounts as published on wgsmartsavings.com, the Maryland deadline countdown, and
    the D.C. pointer to the DC Sustainable Energy Utility. Needs an identified caller. Before calling this tool,
    speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        topic: Optional words from the caller such as furnace, water heater, thermostat, dryer or fireplace, to include programs that are listed only on request.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, jurisdiction, rebates (id, name, amount, deadline, status_note, action_label,
        action_message, days_left, detail, source_url), card (the show_rebate_card arguments), deadline_countdown,
        other_resources, spoken_summary and agent_action.
    """
    try:
        return _finish("find_rebates", _find_rebates_impl(account_id=account_id, topic=topic))
    except Exception as err:
        return _unexpected_error("find_rebates", err)


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


def _money(value) -> float:
    """Rounds an amount to cents."""
    return round(float(value or 0), 2)


def _usage_unit() -> str:
    """Usage unit by tenant: therms for WGL, CCF for SEMCO."""
    return "therms" if _tenant() == "WGL" else "CCF"


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
    parsed = response.json()
    return parsed


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


def _billing_rows(account_id: str) -> list:
    """All billing periods of an account, newest first, each carrying its period key."""
    rows = _run_query("wgl_billing_periods", {"account_id": account_id}, 20)
    for row in rows:
        row["period"] = row.get("period") or row["_id"].removeprefix(f"{account_id}_")
    return sorted(rows, key=lambda row: str(row.get("end", "")), reverse=True)


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


def _parse_date(value):
    """Parses an ISO date or date-time string into a date, or None."""
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _pretty_date(value) -> str:
    """A short written date such as Dec 1, 2026."""
    parsed = _parse_date(value)
    return f"{_MONTHS[parsed.month - 1][:3]} {parsed.day}, {parsed.year}" if parsed else ""


def _period_label(period_key: str) -> str:
    """Turns a YYYY-MM billing period key into a label such as Oct 2025."""
    year, _, month = str(period_key).partition("-")
    return f"{_MONTHS[int(month) - 1][:3]} {year}" if year.isdigit() and month.isdigit() and 1 <= int(month) <= 12 else str(period_key)


_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]


_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


_ORDINALS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth",
    10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth", 16: "sixteenth",
    17: "seventeenth", 18: "eighteenth", 19: "nineteenth", 20: "twentieth", 21: "twenty-first", 22: "twenty-second",
    23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth", 26: "twenty-sixth", 27: "twenty-seventh",
    28: "twenty-eighth", 29: "twenty-ninth", 30: "thirtieth", 31: "thirty-first",
}


def _words(number: int) -> str:
    """Says a whole number from 0 to 99,999 in words, the way a person says it aloud."""
    number = int(number)
    if number < 20:
        return _ONES[number]
    if number < 100:
        tens, rest = divmod(number, 10)
        return _TENS[tens] + (f"-{_ONES[rest]}" if rest else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {_words(rest)}" if rest else "")
    thousands, rest = divmod(number, 1000)
    return f"{_words(thousands)} thousand" + (f" {_words(rest)}" if rest else "")


def _say_money(value) -> str:
    """Says an amount in dollars and cents in words, for voice."""
    cents_total = int(round(abs(float(value or 0)) * 100))
    dollars, cents = divmod(cents_total, 100)
    parts = []
    if dollars or not cents:
        parts.append(f"{_words(dollars)} dollar{'' if dollars == 1 else 's'}")
    if cents:
        parts.append(f"{_words(cents)} cent{'' if cents == 1 else 's'}")
    return " and ".join(parts)


def _say_whole_dollars(value) -> str:
    """Says an amount rounded to whole dollars in words, for voice."""
    dollars = int(round(abs(float(value or 0))))
    return f"{_words(dollars)} dollar{'' if dollars == 1 else 's'}"


def _say_date(value) -> str:
    """Says a date such as December first, for voice."""
    parsed = _parse_date(value)
    return f"{_MONTHS[parsed.month - 1]} {_ORDINALS[parsed.day]}" if parsed else ""


def _say_count(number: int, unit: str) -> str:
    """Says a count with its unit, for example three days."""
    return f"{_words(number)} {unit}{'' if int(number) == 1 else 's'}"


def _when_parts(value, today: date, spoken: bool = False) -> tuple:
    """Returns (day text, hour24, minute) for an ISO date-time; the day text is today, tomorrow or a weekday and date."""
    parsed = _parse_date(value)
    if not parsed:
        return "", None, 0
    delta = (parsed - today).days
    if delta == 0:
        day = "today"
    elif delta == 1:
        day = "tomorrow"
    else:
        day = f"{parsed.strftime('%A')}, " + (_say_date(value) if spoken else f"{_MONTHS[parsed.month - 1]} {parsed.day}")
    text = str(value)
    hour, minute = None, 0
    if "T" in text:
        try:
            hour, minute = int(text[11:13]), int(text[14:16])
        except ValueError:
            hour = None
    return day, hour, minute


def _write_when(value, today: date) -> str:
    """Writes a moment for chat, for example tomorrow around 12 PM."""
    day, hour, minute = _when_parts(value, today)
    if hour is None:
        return day
    suffix = "AM" if hour < 12 else "PM"
    shown = hour % 12 or 12
    return f"{day} around {shown}{':%02d' % minute if minute else ''} {suffix}"


def _say_when(value, today: date) -> str:
    """Says a moment for voice, for example tomorrow around twelve noon."""
    day, hour, minute = _when_parts(value, today, True)
    if hour is None:
        return day
    if hour == 12 and minute == 0:
        clock = "noon"
    elif hour == 0 and minute == 0:
        clock = "midnight"
    else:
        shown = hour % 12 or 12
        part = "in the morning" if hour < 12 else ("in the afternoon" if hour < 17 else "in the evening")
        clock = f"{_words(shown)}{' ' + _words(minute) if minute else ''} {part}"
    return f"{day} around {clock}"


def _say_amount_text(amount: str) -> str:
    """Says a published amount such as $500 to $700 or Up to $2,000 in words, for voice."""
    if amount == "Free":
        return "a free kit"
    prefix = "up to " if amount.startswith("Up to") else ""
    cleaned = amount.replace("Up to ", "").replace("$", "").replace(",", "")
    numbers = [part.strip() for part in cleaned.split(" to ")]
    if all(part.replace(".", "", 1).isdigit() and "." not in part for part in numbers):
        if len(numbers) == 2:
            return f"{_words(int(numbers[0]))} to {_say_whole_dollars(float(numbers[1]))}"
        return prefix + _say_whole_dollars(float(numbers[0]))
    return amount


# Published programs of wgsmartsavings.com (Maryland page, Virginia page and manufacturer rebates page), read on
# 2026-10-17 and again on 2026-10-18. Amounts are as published. DC has no listed Washington Gas program; the FAQ
# points D.C. residents to the District of Columbia Sustainable Energy Utility (DCSEU).
_SRC_MD = "https://wgsmartsavings.com/programs-rebates/md/home-heating"


_SRC_VA = "https://wgsmartsavings.com/programs-rebates/home/va"


_SRC_RINNAI = "https://wgsmartsavings.com/programs-rebates/manufacturer_rebates"


_SRC_DCSEU = "https://www.dcseu.com"


_MD_SUBMIT_BY = "2026-12-01"


_MD_PROGRAM_END = "2026-12-31"


_REBATE_PHONE = "1-833-286-0860, Monday to Friday, 8 a.m. to 5 p.m."


_PROGRAMS = [
    {"id": "md_furnace", "short": "furnace", "for": "a new high-efficiency furnace", "jurisdictions": ("MD",), "name": "High-efficiency furnace", "amount": "$500 to $700",
     "detail": "$500 for an ENERGY STAR furnace at 95.1 percent AFUE or higher, $700 at 97 percent or higher, installed by a participating Home Energy Savings contractor",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("furnace",)},
    {"id": "md_boiler", "short": "boiler", "for": "a new high-efficiency boiler", "jurisdictions": ("MD",), "name": "High-efficiency boiler", "amount": "$475 to $775",
     "detail": "$475 for an ENERGY STAR boiler at 90 percent AFUE or higher, $775 at 95 percent or higher, installed by a participating Home Energy Savings contractor",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("boiler",)},
    {"id": "md_combo", "short": "combination heating unit", "for": "a combination heating unit", "jurisdictions": ("MD",), "name": "Combination heating unit", "amount": "$900",
     "detail": "Combination heating unit at 90 percent AFUE or higher and/or 0.87 UEF or higher, installed by a participating Home Energy Savings contractor",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("combination", "combo")},
    {"id": "md_tuneup", "short": "tune-up", "for": "a furnace or boiler tune-up", "jurisdictions": ("MD",), "name": "Furnace or boiler tune-up", "amount": "$100",
     "detail": "Tune-up by a Home Energy Savings contractor; the service must cost at least $100; one tune-up per equipment in the 2024 to 2026 program cycle",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("tune",)},
    {"id": "md_storage_wh", "short": "storage water heater", "for": "a new storage water heater", "jurisdictions": ("MD",), "name": "Storage water heater", "amount": "$175",
     "detail": "Storage water heater installed by a participating Home Energy Savings contractor",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("water heater",)},
    {"id": "md_tankless_wh", "short": "tankless water heater", "for": "a new tankless water heater", "jurisdictions": ("MD",), "name": "Tankless water heater", "amount": "$450",
     "detail": "Tankless water heater installed by a participating Home Energy Savings contractor",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("tankless",)},
    {"id": "md_dryer", "short": "gas dryer", "for": "an ENERGY STAR gas clothes dryer", "jurisdictions": ("MD",), "name": "ENERGY STAR gas clothes dryer", "amount": "$90",
     "detail": "ENERGY STAR gas clothes dryer bought at Lowe's or The Home Depot",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("dryer",)},
    {"id": "md_fireplace", "short": "gas fireplace", "for": "a gas fireplace", "jurisdictions": ("MD",), "name": "Gas fireplace", "amount": "$75",
     "detail": "Gas fireplace, listed on the Maryland builder update page",
     "deadline": _MD_SUBMIT_BY, "end": _MD_PROGRAM_END, "source": _SRC_MD, "keywords": ("fireplace",)},
    {"id": "va_heating", "short": "furnace or boiler", "for": "a new ENERGY STAR furnace or boiler", "jurisdictions": ("VA",), "name": "High-efficiency furnace or boiler", "amount": "Up to $2,000",
     "detail": "Select ENERGY STAR certified boilers and furnaces; the page lists no deadline",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("furnace", "boiler")},
    {"id": "va_water_heater", "short": "water heater", "for": "a new ENERGY STAR water heater", "jurisdictions": ("VA",), "name": "ENERGY STAR water heater", "amount": "Up to $400",
     "detail": "Select ENERGY STAR certified water heaters; the page lists no deadline",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("water heater", "tankless")},
    {"id": "va_thermostat", "short": "smart thermostat", "for": "a smart thermostat", "jurisdictions": ("VA",), "name": "Smart thermostat", "amount": "$100",
     "detail": "ENERGY STAR certified smart thermostat; the page lists no deadline",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("thermostat",)},
    {"id": "va_tuneup", "short": "tune-up", "for": "a heating tune-up", "jurisdictions": ("VA",), "name": "Home heating tune-up", "amount": "$100",
     "detail": "Furnace or boiler tune-up; the page lists no deadline",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("tune",)},
    {"id": "va_fireplace_tuneup", "short": "fireplace tune-up", "for": "a gas fireplace tune-up", "jurisdictions": ("VA",), "name": "Gas fireplace tune-up", "amount": "$50",
     "detail": "Gas fireplace tune-up; the page lists no deadline",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("fireplace",)},
    {"id": "va_windows", "short": "window", "for": "windows and window treatments", "jurisdictions": ("VA",), "name": "Windows and window treatments", "amount": "Up to $2.50 per sq ft",
     "detail": "Eligible windows and sun shades; the page lists no deadline",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("window",)},
    {"id": "va_kit", "short": "energy conservation kit", "for": "the free energy conservation kit", "jurisdictions": ("VA",), "name": "Free home energy conservation kit", "amount": "Free",
     "detail": "You can request a free kit, and completing a home energy profile also earns one",
     "deadline": "", "end": "", "source": _SRC_VA, "keywords": ("kit", "assessment", "profile"), "claimable": False},
    {"id": "rinnai", "short": "Rinnai", "for": "a select Rinnai tankless water heater", "jurisdictions": ("MD", "DC", "VA"), "name": "Rinnai tankless water heater offer", "amount": "$100",
     "detail": "$100 off select Rinnai tankless water heaters, one claim per household; buy between Sep 1 and Dec 31, 2026, install by Jan 15, 2027, claim due Feb 15, 2027",
     "deadline": "2026-12-31", "end": "2027-02-15", "source": _SRC_RINNAI, "keywords": ("rinnai", "tankless", "water heater")},
]


# Programs listed only when the caller asks about them (their keywords), never pushed.
_ON_REQUEST_ONLY = ("md_combo", "md_dryer", "md_fireplace", "va_fireplace_tuneup", "va_windows")


def _program(program_id: str) -> dict:
    """The published program with this id, or an empty dict."""
    return next((item for item in _PROGRAMS if item["id"] == program_id), {})


def _matches_equipment(program_id: str, heating: str, water: str, has_thermostat: bool) -> tuple:
    """Returns (matches, known) for the account's equipment; known is True when the account data names the equipment."""
    if program_id == "md_furnace":
        return heating in ("furnace", "unknown"), heating == "furnace"
    if program_id == "md_boiler":
        return heating in ("boiler", "unknown"), heating == "boiler"
    if program_id == "va_heating":
        return True, heating in ("furnace", "boiler")
    if program_id in ("md_tuneup", "va_tuneup"):
        return heating in ("furnace", "boiler", "unknown"), heating in ("furnace", "boiler")
    if program_id in ("md_storage_wh", "va_water_heater", "md_tankless_wh", "rinnai"):
        return water in ("storage", "unknown"), water == "storage"
    if program_id == "va_thermostat":
        return not has_thermostat, not has_thermostat
    if program_id == "va_kit":
        return True, False
    return False, False


def _spoken_lead(program: dict, heating: str, age, known: bool, days_left, renting: bool) -> str:
    """One natural spoken sentence on why the program fits this home, written for the ear; the card keeps its short status_note."""
    program_id, target = program["id"], program["for"]
    if known and program_id in ("md_furnace", "md_boiler", "va_heating"):
        if age:
            lead = f"Your {heating} is about {_words(int(age))} years old, so you may qualify for a rebate on {target}"
        else:
            lead = f"You may qualify for a rebate on {target}, since it fits your {heating}"
    elif known and program_id in ("md_tuneup", "va_tuneup"):
        lead = f"A tune-up for your {heating} may qualify for a rebate"
    elif known and program_id == "md_tankless_wh":
        lead = "Upgrading from your storage water heater to a tankless one may qualify for a rebate"
    elif known and program_id in ("md_storage_wh", "va_water_heater", "rinnai"):
        lead = f"You may qualify for a rebate on {target}, to replace your storage water heater"
    elif known and program_id == "va_thermostat":
        lead = f"You do not have a smart thermostat on file, so you may qualify for a rebate on {target}"
    elif program_id == "va_kit":
        lead = "You can request the free energy conservation kit at no cost"
    else:
        lead = f"You may qualify for a rebate on {target}"
    if days_left is not None and days_left < 0:
        lead += ". The submit-by date has passed, so the rebate processing center can tell you whether it can still be reviewed"
    if renting:
        lead += ". It is worth checking with your landlord first"
    return lead


def _match_programs(customer: dict, topic: str, today: date) -> list:
    """Published programs that fit the customer's jurisdiction and home or equipment, best matches first.

    Each entry carries the card fields (id, name, amount, deadline, status_note, action_label, action_message) plus
    days_left, claimable, known_match, detail and source_url. Programs whose end date has passed are dropped.
    """
    jurisdiction = str(customer.get("jurisdiction", ""))
    if customer.get("customer_type") == "Commercial" or customer.get("home_ownership") == "business":
        return []
    heating = str(customer.get("heating_equipment") or "unknown")
    water = str(customer.get("water_heater_type") or "unknown")
    topic_text = str(topic or "").lower()
    age = customer.get("heating_equipment_age_years")
    rows = []
    for program in _PROGRAMS:
        if jurisdiction not in program["jurisdictions"]:
            continue
        matches, known = _matches_equipment(program["id"], heating, water, bool(customer.get("smart_thermostat")))
        asked = any(word in topic_text for word in program["keywords"]) if topic_text else False
        if not ((matches and program["id"] not in _ON_REQUEST_ONLY) or asked):
            continue
        end = _parse_date(program["end"])
        if end and end < today:
            continue
        deadline = _parse_date(program["deadline"])
        days_left = (deadline - today).days if deadline else None
        if known and program["id"] in ("md_furnace", "md_boiler", "va_heating"):
            note = f"Matches your {heating}" + (f", about {int(age)} years old" if age else "")
        elif known and program["id"] in ("md_tuneup", "va_tuneup"):
            note = f"A tune-up fits your {heating}"
        elif known and program["id"] == "md_tankless_wh":
            note = "An upgrade from your storage water heater"
        elif known and program["id"] in ("md_storage_wh", "va_water_heater", "rinnai"):
            note = "Matches your storage water heater"
        elif known and program["id"] == "va_thermostat":
            note = "No smart thermostat on file"
        elif program["id"] == "va_kit":
            note = "Free to request"
        else:
            note = "You may qualify"
        if days_left is not None and days_left < 0:
            note = "Submit-by date has passed; ask the processing center"
        if customer.get("home_ownership") == "rent":
            note += "; check with your landlord first"
        spoken_note = note.replace(f", about {int(age)} years old", f", about {_words(int(age))} years old") if age else note
        spoken_lead = _spoken_lead(program, heating, age, known, days_left, customer.get("home_ownership") == "rent")
        claimable = program.get("claimable", True)
        rows.append({
            "id": program["id"], "short": program["short"], "for": program["for"], "name": program["name"], "amount": program["amount"],
            "deadline": _pretty_date(program["deadline"]), "status_note": note, "status_spoken": spoken_note, "spoken_lead": spoken_lead,
            "action_label": "Start claim" if claimable else "Text me the link",
            "action_message": f"Help me claim the {program['short']} rebate" if claimable else "Text me the link for the free energy conservation kit",
            "days_left": days_left, "claimable": claimable, "known_match": bool(known or (asked and matches)), "detail": program["detail"],
            "source_url": program["source"],
        })
    order = {program["id"]: index for index, program in enumerate(_PROGRAMS)}
    rows.sort(key=lambda entry: (0 if entry["known_match"] else 1, order[entry["id"]]))
    return rows


def _card_rebate(entry: dict) -> dict:
    """The fields the rebate card renders; the deadline is left out when the program lists none."""
    card = {key: entry[key] for key in ("id", "name", "amount", "status_note", "action_label", "action_message")}
    if entry.get("deadline"):
        card["deadline"] = entry["deadline"]
    return card

_MAX_REBATES = 5


def _countdown(rows: list, today: date) -> dict:
    """The Maryland deadline countdown when a Maryland program with a submit-by date is still open."""
    open_rows = [entry for entry in rows if entry["id"].startswith("md_") and entry["days_left"] is not None]
    if not open_rows:
        return {}
    days_left = open_rows[0]["days_left"]
    if days_left >= 0:
        return {
            "submit_by": _pretty_date(_MD_SUBMIT_BY), "program_ends": _pretty_date(_MD_PROGRAM_END), "days_left_to_submit": days_left,
            "message": f"The Maryland programs end {_pretty_date(_MD_PROGRAM_END)}. The site advises sending the application and all documents by {_pretty_date(_MD_SUBMIT_BY)}, which is {days_left} days away, so review and payment finish before the programs end.",
        }
    return {
        "submit_by": _pretty_date(_MD_SUBMIT_BY), "program_ends": _pretty_date(_MD_PROGRAM_END), "days_left_to_submit": days_left,
        "message": f"The advised submit-by date of {_pretty_date(_MD_SUBMIT_BY)} has passed and the Maryland programs end {_pretty_date(_MD_PROGRAM_END)}. Call the rebate processing center to ask whether a late application can still be reviewed.",
    }


def _spoken_for(jurisdiction: str, rows: list, countdown: dict) -> str:
    """One short spoken answer for voice: the best match, its amount, and the deadline when there is one."""
    if jurisdiction == "DC":
        sentence = ("Washington Gas lists no current rebate program for the District. D.C. residents can get rebates from the "
                    "D.C. Sustainable Energy Utility.")
        if rows:
            sentence += f" There is also an offer of {_say_amount_text(rows[0]['amount'])} on {rows[0]['for']} that is open to D.C. customers."
        return sentence + " I can text you the links if you like."
    if not rows:
        return "I do not see a published rebate that fits your home right now. I can text you the rebate page if you like."
    top = rows[0]
    sentence = f"{top['spoken_lead']}."
    if top["amount"] != "Free":
        sentence += f" The rebate is {_say_amount_text(top['amount'])}."
    if countdown and countdown.get("days_left_to_submit", -1) >= 0 and top["id"].startswith("md_"):
        sentence += f" The site advises applying by {_say_date(_MD_SUBMIT_BY)}, that is {_say_count(countdown['days_left_to_submit'], 'day')} from now."
    return sentence + " Would you like me to start a claim, or text you the link?"


def _find_rebates_impl(account_id: str, topic: str) -> dict:
    if _tenant() != "WGL":
        return _rule_error("rebate matching is only available for Washington Gas customers", "answer from the knowledge base instead")
    customer, error = _load_customer(account_id, "Identified")
    if error:
        return error
    today = _today()
    jurisdiction = str(customer.get("jurisdiction", ""))
    rows = _match_programs(customer, topic, today)[:_MAX_REBATES]
    countdown = _countdown(rows, today)
    profile_known = (customer.get("heating_equipment") or "unknown") != "unknown"
    in_arrears = float(customer.get("arrears") or 0) > 0 or bool(customer.get("shutoff_date"))
    other = []
    if jurisdiction == "DC":
        other.append({"name": "District of Columbia Sustainable Energy Utility (DCSEU)", "url": _SRC_DCSEU,
                      "note": "The Washington Gas FAQ says D.C. residents get rebates through DCSEU; no current Washington Gas program is listed for D.C."})
    if customer.get("customer_type") == "Commercial" or customer.get("home_ownership") == "business":
        other.append({"name": "Business programs on WG Smart Savings", "url": "https://wgsmartsavings.com",
                      "note": "Business customers have separate programs; no amounts are listed here."})
    action = (
        "Say these as programs that may fit the customer, never as a guarantee: the program decides eligibility. Amounts, "
        "deadlines and conditions are the ones published on wgsmartsavings.com, and you may say so. Rebates need a participating "
        "Home Energy Savings contractor except retail items. Start with the first rebate only, one at a time."
    )
    if rows and (_var("channel", "chat") or "chat") != "voice":
        action = (
            "On chat your next output must be only the function call show_rebate_card with this result's card: the card is the whole reply, "
            "so write no list of the rebates first. " + action.replace(" Start with the first rebate only, one at a time.", "")
        )
    if not profile_known:
        action += " The account has no heating equipment on file: ask whether the home has a furnace or a boiler before narrowing down."
    if in_arrears:
        action += " The account has a past due balance: answer because the customer asked, keep it brief and kind, and offer payment help afterwards."
    if jurisdiction == "DC":
        action += " For D.C. say plainly that Washington Gas lists no current program and point to DCSEU."
    return {
        "ok": True,
        "jurisdiction": jurisdiction,
        "profile_known": profile_known,
        "in_arrears": in_arrears,
        "rebates": rows,
        "card": {"rebates": [_card_rebate(entry) for entry in rows]},
        "deadline_countdown": countdown,
        "other_resources": other,
        "processing": {"time": "Allow 6 to 8 weeks after a complete application.", "phone": _REBATE_PHONE},
        "spoken_summary": _spoken_for(jurisdiction, rows, countdown),
        "agent_action": action,
    }
