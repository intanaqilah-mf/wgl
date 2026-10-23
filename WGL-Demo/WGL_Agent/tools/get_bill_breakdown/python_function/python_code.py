import json
import time
from datetime import date, datetime, timezone


def get_bill_breakdown(account_id: str = "", period: str = "", tenant: str = "") -> dict:
    """Returns what the customer's latest bill is made of (gas supply, delivery, rider surcharges and adjustments)
    with amounts that add up to the bill total, shaped for the bill breakdown card. Use period (YYYY-MM) for an
    older bill. Needs an identified caller. Before calling this tool, speak a brief conversational pacing phrase
    while it runs.

    Args:
        account_id: Account id of the identified account (REQUIRED).
        period: Optional billing period such as 2026-09; leave empty for the latest bill.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, period, total, items (label, amount), supply_share_pct, delivery_share_pct, card (the
        show_bill_breakdown_card arguments), spoken_summary and agent_action.
    """
    try:
        return _finish("get_bill_breakdown", _get_bill_breakdown_impl(account_id=account_id, period=period))
    except Exception as err:
        return _unexpected_error("get_bill_breakdown", err)


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
        f"auth_status is {current} but {required} is needed. Save the pending task in saved_task_state and "
        "transfer silently to auth_agent; never tell the customer about internal routing.",
        auth_required=required,
    )


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
    known = _object_var("identity_ctx").get("account_id", "")
    resolved = str(account_id).strip() or known
    if not resolved:
        return {}, _validation_error("account_id")
    if known and resolved != known:
        return {}, _rule_error("only the account being served in this conversation can be used", "continue with that account")
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


def _today() -> date:
    """Today's date on the demo clock."""
    return _now().date()


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

def _group_items(line_items: list) -> list:
    """Groups the bill lines into Gas supply, Delivery (customer charge plus distribution) and the other lines as they are named."""
    supply = delivery = 0.0
    others = []
    for line in line_items:
        kind, amount = str(line.get("item", "")), float(line.get("amount") or 0)
        if kind.startswith("gas_cost"):
            supply += amount
        elif kind in ("customer_charge", "distribution"):
            delivery += amount
        else:
            others.append({"label": str(line.get("label", kind)), "amount": _money(amount)})
    items = [{"label": "Gas supply", "amount": _money(supply)}, {"label": "Delivery", "amount": _money(delivery)}]
    return items + others


def _get_bill_breakdown_impl(account_id: str, period: str) -> dict:
    customer, error = _load_customer(account_id, "Identified")
    if error:
        return error
    rows = _billing_rows(customer["_id"])
    if not rows:
        return _rule_error("there is no bill on file", "offer to check again later or connect a representative")
    wanted = str(period).strip()
    row = next((item for item in rows if item["period"] == wanted), None) if wanted else rows[0]
    if row is None:
        return _rule_error("no bill exists for that period", "choose one of the recent billing periods")
    items = [part for part in _group_items(row.get("line_items") or []) if part["amount"] != 0]
    total = _money(row.get("amount"))
    drift = round(total - sum(part["amount"] for part in items), 2)
    if items and drift:
        items[0]["amount"] = _money(items[0]["amount"] + drift)
    supply = next((part["amount"] for part in items if part["label"] == "Gas supply"), 0.0)
    delivery = next((part["amount"] for part in items if part["label"] == "Delivery"), 0.0)
    supply_pct = int(round(supply / total * 100)) if total else 0
    delivery_pct = int(round(delivery / total * 100)) if total else 0
    spoken = (
        f"Of your {_say_money(total)} bill, about {_say_whole_dollars(supply)} is the gas itself and about "
        f"{_say_whole_dollars(delivery)} is delivery, which is the customer charge and the cost of getting gas to your home."
    )
    extras = [part for part in items if part["label"] not in ("Gas supply", "Delivery")]
    if extras:
        spoken += f" The rest is {', '.join(part['label'] for part in extras)}."
    return {
        "ok": True,
        "period": row["period"],
        "total": total,
        "items": items,
        "supply_share_pct": supply_pct,
        "delivery_share_pct": delivery_pct,
        "card": {"total": total, "items": items},
        "spoken_summary": spoken,
        "agent_action": (
            "Explain only the items returned: gas supply is the purchased gas charge, delivery is the customer charge plus the "
            "distribution charge. Do not mention taxes or fees that are not in the items. The rider and adjustment lines are named exactly as listed."
        ),
    }
