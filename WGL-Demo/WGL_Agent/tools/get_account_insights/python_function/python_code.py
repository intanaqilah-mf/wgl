import json
import time
from datetime import date, datetime, timedelta, timezone


def get_account_insights(account_id: str = "", moment: str = "review", customer_said: str = "", task: str = "", topic_response: str = "", tenant: str = "") -> dict:
    """Reviews the identified customer's account and returns ranked, personal, data-backed insights and next steps, with
    strict relevance rules: a value test (a number or date from the account, helps today, an action the agent can take
    now), once per topic per session, one suggestion per reply, at most two proactive offers per chat session and one per
    call, none after a no, none for an upset customer, during an emergency, in a greeting, a simple status answer or a
    payment confirmation, and no savings pitch while the account is past due. Topics: outage or planned work, shutoff or
    past due help, a bill due soon, estimated read, credit, scheduled move, bill change, help when payments were partial,
    cold-weather bill tips, Budget Plan, AutoPay, eBill, rebates, gas appliance safety, home repair plan information and
    My Washington Gas as a next step. Read only except for the session memory. Needs an identified caller. Before calling
    this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        moment: Why you are calling: review (the customer accepted a look at the account), asked (the customer asked for tips or ideas),
            bill_explained (right after a bill explanation), task_done (the customer's task is finished and they replied),
            customer_said (use customer_said), greeting, status or payment_confirmed (these always return nothing), or
            record (only record topic_response). Default review.
        customer_said: Comma-separated signals from what the customer just said: high_bill, paid_in_part, repair_cost, duplicate_bill,
            co_question, appliance_question, upset, shutoff_worry, gas_smell. Leave empty when none.
        task: The task just finished, for example payment, autopay, ebill, budget_plan, duplicate_bill, meter_reading, appointment, bill_explained.
        topic_response: Record the answer to the last suggestion as topic_id:declined or topic_id:accepted, for example autopay:declined.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, insights (id, title, summary, severity, action_label, action_message, spoken_summary), insights_card
        (the show_insights_carousel arguments), top_spoken, suggestion, silent, reason, in_arrears and agent_action.
    """
    try:
        return _finish("get_account_insights", _get_account_insights_impl(
            account_id=account_id, moment=moment, customer_said=customer_said, task=task, topic_response=topic_response))
    except Exception as err:
        return _unexpected_error("get_account_insights", err)

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
        claimable = program.get("claimable", True)
        rows.append({
            "id": program["id"], "short": program["short"], "for": program["for"], "name": program["name"], "amount": program["amount"],
            "deadline": _pretty_date(program["deadline"]), "deadline_iso": program["deadline"], "status_note": note, "status_spoken": spoken_note,
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

_MAX_INSIGHTS = 3


# Urgency order inside one tier: safety (10s), shutoff and past due (20s), due soon (30s), account facts (40s), bill change (50s), savings and tips (60s).
_RANK_SAFETY = 10


# Tiers (Rev 20): 1 personal and backed by an account number or date, 2 personal from what the customer just said,
# 3 situational safety, 4 generic help. Sorting is (tier, rank).
_HEATING_MONTHS = (10, 11, 12, 1, 2, 3)


# Moments the agent passes. Silent moments never produce a suggestion; requested moments are invited by the customer;
# proactive moments are the only ones that count against the caps.
_SILENT_MOMENTS = ("greeting", "status", "payment_confirmed")


_REQUESTED_MOMENTS = ("review", "asked")


_PROACTIVE_MOMENTS = ("bill_explained", "task_done", "customer_said")


_BLOCKING_SIGNALS = ("upset", "shutoff_worry", "gas_smell", "emergency")


_PROACTIVE_CAP = {"chat": 2, "voice": 1}


_RETURN_LIMIT = {"review": 3, "asked": 2, "bill_explained": 1, "task_done": 1, "customer_said": 1}


# topic id -> (moments where it may appear, customer_said signals that bring it up at the customer_said moment).
_TOPIC_RULES = {
    "outage": (("review",), ()),
    "planned_work": (("review",), ()),
    "shutoff": (("review", "asked"), ()),
    "past_due": (("review", "asked"), ()),
    "due_soon": (("review",), ()),
    "estimated_read": (("review",), ()),
    "credit": (("review",), ()),
    "moving": (("review",), ()),
    "bill_up": (("review",), ()),
    "bill_down": (("review",), ()),
    "hardship_partial": (("review", "asked", "task_done", "customer_said"), ("paid_in_part",)),
    "cold_weather_tip": (("bill_explained", "asked", "customer_said"), ("high_bill",)),
    "budget_plan": (("review", "bill_explained", "asked", "customer_said"), ("high_bill",)),
    "autopay": (("review", "task_done", "asked"), ()),
    "ebill": (("review", "task_done", "asked", "customer_said"), ("duplicate_bill",)),
    "rebate": (("review", "asked"), ()),
    "appliance_safety": (("review", "task_done", "asked", "customer_said"), ("co_question", "appliance_question")),
    "home_repair": (("asked", "customer_said"), ("repair_cost",)),
    "manage_account": (("task_done",), ()),
}


# Tasks that the customer could also do alone in My Washington Gas.
_SELF_SERVE_TASKS = ("payment", "autopay", "ebill", "budget_plan", "duplicate_bill", "meter_reading", "appointment", "rebate_claim", "bill_explained")


def _insight(key: str, rank: int, severity: str, title: str, summary: str, spoken: str, label: str, message: str, saving: bool = False,
             fact: str = "", tier: int = 1, hold: bool = None) -> dict:
    """One insight candidate. saving marks the ones that must never be shown to a customer in arrears; fact is the account
    number or date (or the customer's own words for tier 2) that backs the insight; hold withholds it in arrears."""
    return {
        "id": key, "rank": rank, "severity": severity, "title": title, "summary": summary, "spoken_summary": spoken,
        "action_label": label, "action_message": message, "saving": saving, "fact": fact or "account data",
        "tier": tier, "hold": saving if hold is None else hold,
    }


def _value_test(item: dict) -> bool:
    """The value test: a fact that backs it, text, a spoken line and an action the agent can take now. A failure is never shown."""
    needed = ("fact", "title", "summary", "spoken_summary", "action_label", "action_message")
    return all(str(item.get(key, "")).strip() for key in needed)


def _cap(text: str) -> str:
    """Capitalises the first letter of a sentence (amounts spoken in words start in lower case)."""
    return text[:1].upper() + text[1:]


def _channel() -> str:
    """chat or voice; anything unknown is treated as voice, the safer channel."""
    return "chat" if _var("channel", "").lower() == "chat" else "voice"


def _load_memory() -> dict:
    """The session memory of what was shown, declined and accepted (state variable insight_memory)."""
    raw = _object_var("insight_memory")
    return {
        "shown": [str(item) for item in raw.get("shown", [])], "declined": [str(item) for item in raw.get("declined", [])],
        "accepted": [str(item) for item in raw.get("accepted", [])], "ignored": [str(item) for item in raw.get("ignored", [])],
        "offers": int(raw.get("offers") or 0), "closed": bool(raw.get("closed")), "last_offered": str(raw.get("last_offered") or ""),
    }


def _save_memory(memory: dict) -> None:
    """Stores the session memory as JSON in the plain string variable insight_memory."""
    _set_var("insight_memory", json.dumps(memory))


def _apply_response(memory: dict, topic_response: str) -> str:
    """Records topic:declined or topic:accepted. A decline drops the topic and closes every further proactive offer."""
    topic, _, outcome = str(topic_response or "").strip().lower().partition(":")
    if not topic or outcome not in ("declined", "accepted"):
        return ""
    if outcome == "declined":
        if topic not in memory["declined"]:
            memory["declined"].append(topic)
        memory["closed"] = True
    elif topic not in memory["accepted"]:
        memory["accepted"].append(topic)
    if topic not in memory["shown"]:
        memory["shown"].append(topic)
    if memory["last_offered"] == topic:
        memory["last_offered"] = ""
    return f"{topic}:{outcome}"


def _emergency_active() -> bool:
    """True when a gas smell or alarm was reported in this conversation."""
    return _var("emergency_detected", "").lower() == "true" or _var("_emergency_card_shown", "").lower() == "true" or _var("_action_trigger", "") == "emergency"


def _customer_upset() -> bool:
    """True when the session sentiment says the customer is upset (0.6 or higher)."""
    try:
        return float(_var("sentiment_score", "0") or 0) >= 0.6
    except ValueError:
        return False


def _silence_reason(moment: str, signals: set, memory: dict) -> str:
    """Why nothing may be suggested right now; an empty string means a suggestion is allowed."""
    if _emergency_active() or signals & {"gas_smell", "emergency"}:
        return "emergency"
    if moment in _SILENT_MOMENTS:
        return "silent_moment"
    if moment in _REQUESTED_MOMENTS:
        return ""
    if _customer_upset() or "upset" in signals:
        return "customer_upset"
    if "shutoff_worry" in signals:
        return "shutoff_worry"
    if memory["closed"]:
        return "declined_earlier"
    if memory["offers"] >= _PROACTIVE_CAP[_channel()]:
        return "offer_cap"
    return ""


def _allowed(topic: str, moment: str, signals: set, task: str) -> bool:
    """Whether the topic may appear at this moment for these customer signals."""
    moments, topic_signals = _TOPIC_RULES.get(topic, ((), ()))
    if moment not in moments:
        return False
    if moment == "customer_said":
        return bool(signals & set(topic_signals))
    if topic == "manage_account":
        return task in _SELF_SERVE_TASKS
    return True


def _safety_insights(customer: dict, today: date) -> list:
    """Outage on the ZIP (urgent) or planned work in the next seven days (attention)."""
    outage = _get_doc("wgl_outages", f"{_tenant()}_{customer.get('zip', '')}")
    found = []
    if outage.get("active"):
        written, said = _write_when(outage.get("eta", ""), today), _say_when(outage.get("eta", ""), today)
        found.append(_insight(
            "outage", _RANK_SAFETY, "urgent", "Gas outage in your area",
            f"Based on the latest information we have, repairs are expected {written}. A technician then visits each home.",
            f"There is a gas outage in your area. Based on the latest information we have, repairs are expected {said}. Would you like a text when service is restored?",
            "Text me updates", "Text me when my gas is restored", fact=f"outage recorded for ZIP {customer.get('zip', '')}, estimate {written}"))
        return found
    planned = outage.get("planned_maintenance") or {}
    start = _parse_date(planned.get("start", ""))
    if start and 0 <= (start - today).days <= 7:
        written, said = _write_when(planned.get("start", ""), today), _say_when(planned.get("start", ""), today)
        found.append(_insight(
            "planned_work", _RANK_SAFETY + 4, "attention", "Planned work on your street",
            f"Crews are scheduled {written}. Gas service may be briefly interrupted. This is planned work, not an outage.",
            f"There is planned work on your street {said}. Gas service may be briefly interrupted. Would you like a text reminder?",
            "Text me a reminder", "Text me a reminder about the planned work", fact=f"planned work starts {written}"))
    return found


def _payment_insights(customer: dict, arrears: float, today: date) -> list:
    """Shutoff and past due lead with help; a bill due within a week when nothing is past due."""
    found = []
    shutoff = _parse_date(customer.get("shutoff_date") or "")
    if shutoff and shutoff >= today:
        found.append(_insight(
            "shutoff", 20, "urgent", "Let's keep your gas on",
            f"A shutoff is scheduled for {_pretty_date(shutoff.isoformat())}. There are payment plans and assistance programs we can go through together.",
            f"I want to make sure you stay connected. A shutoff is scheduled for {_say_date(shutoff.isoformat())}, and there are payment plans and assistance programs we can go through together. Would you like to look at them?",
            "Help me keep my gas on", "I got a shutoff notice and need help", fact=f"shutoff date {shutoff.isoformat()}"))
    elif arrears > 0:
        found.append(_insight(
            "past_due", 24, "attention", "Help with your past due balance",
            f"There is ${arrears:,.2f} past due. Payment plans and assistance programs are available, at your pace.",
            f"I see {_say_money(arrears)} past due. There are payment plans and assistance programs that can help, and we can go through them at your pace. Would you like to hear them?",
            "See my options", "I need help with my past due balance", fact=f"${arrears:,.2f} past due"))
    balance = float(customer.get("balance") or 0)
    due = _parse_date(customer.get("due_date") or "")
    pending = (customer.get("pending_payment") or {}).get("type")
    if arrears <= 0 and balance > 0 and due and 0 <= (due - today).days <= 7 and not pending:
        days = (due - today).days
        when = "today" if days == 0 else f"on {_pretty_date(due.isoformat())}"
        said = "today" if days == 0 else f"on {_say_date(due.isoformat())}"
        found.append(_insight(
            "due_soon", 30, "attention", "Your bill is due soon", f"${balance:,.2f} is due {when}.",
            f"Your bill of {_say_money(balance)} is due {said}. Would you like to take care of it now?",
            "Pay now", "I'd like to pay my bill", fact=f"${balance:,.2f} due {due.isoformat()}"))
    return found


def _weather_share(customer: dict, current: dict, previous: dict, delta: float) -> int:
    """Percent of a bill increase explained by colder weather, with the same formula and clamp as the diagnosis; 0 when none."""
    weather = _get_doc("wgl_weather_hdd", f"{customer.get('zip', '')}_{current['period']}")
    a0, d0, d1 = float(previous.get("rate_per_unit") or 0), int(previous.get("days") or 0), int(current.get("days") or 0)
    u0, u1 = float(previous.get("usage") or 0), float(current.get("usage") or 0)
    h0, h1 = float(weather.get("hdd_prior_period") or 0), float(weather.get("hdd_current") or 0)
    if not (weather and d0 and h0 and delta > 0):
        return 0
    weather_usage = round(a0 * u0 * (d1 / d0) * (h1 / h0 - 1), 2)
    actual = round(a0 * (u1 - u0 * (d1 / d0)), 2)
    if weather_usage * actual <= 0:
        return 0
    return int(round(min(weather_usage, actual) / delta * 100))


def _bill_change(rows: list) -> tuple:
    """(delta, percent) of the latest bill against the one before it; (0, 0) when there is no comparison."""
    if len(rows) < 2:
        return 0.0, 0.0
    delta = round(float(rows[0].get("amount") or 0) - float(rows[1].get("amount") or 0), 2)
    previous = float(rows[1].get("amount") or 0)
    return delta, (delta / previous * 100 if previous else 0.0)


def _bill_insights(customer: dict, rows: list) -> list:
    """How the latest bill compares with the one before it, and an estimated read."""
    found = []
    if len(rows) >= 2:
        current, previous = rows[0], rows[1]
        delta, pct = _bill_change(rows)
        if delta >= 15 and pct >= 20:
            share = _weather_share(customer, current, previous, delta)
            weather_text = f" Colder weather explains about {share} percent of the rise." if share >= 25 else ""
            weather_said = f" Colder weather explains about {_words(share)} percent of the rise." if share >= 25 else ""
            found.append(_insight(
                "bill_up", 50, "attention", f"Your bill is ${delta:,.0f} higher than last month",
                f"The latest bill is ${float(current.get('amount')):,.2f}.{weather_text}",
                f"Your latest bill is about {_say_whole_dollars(delta)} higher than last month.{weather_said} Would you like me to explain what changed?",
                "Explain my bill", "Why is my bill higher this month?", fact=f"latest bill ${float(current.get('amount')):,.2f}, ${delta:,.2f} more than last month"))
        elif delta <= -15 and pct <= -20:
            found.append(_insight(
                "bill_down", 72, "info", f"Your bill is ${abs(delta):,.0f} lower than last month",
                f"The latest bill is ${float(current.get('amount')):,.2f}.",
                f"Good news: your latest bill is about {_say_whole_dollars(delta)} lower than last month. Would you like to see your bill history?",
                "Show my bill history", "Show me my bill history", fact=f"latest bill ${float(current.get('amount')):,.2f}, ${abs(delta):,.2f} less than last month"))
    if rows and rows[0].get("read_type") == "estimated":
        found.append(_insight(
            "estimated_read", 40, "attention", "Your latest bill used an estimated reading",
            "A real meter reading from you can correct it.",
            "Your latest bill was based on an estimated meter reading. If you can give me a real reading, I can use it to correct the next bill. Would you like to do that?",
            "Submit a reading", "I'd like to submit my meter reading", fact="latest bill read type is estimated"))
    return found


def _account_insights(customer: dict, today: date) -> list:
    """Credit balance and a scheduled move."""
    found = []
    credit = float(customer.get("credit_balance") or 0)
    if credit > 0:
        found.append(_insight(
            "credit", 42, "info", f"You have a ${credit:,.2f} credit",
            "You can leave it to reduce the next bill or ask for a refund.",
            f"You have a credit of {_say_money(credit)} on your account. You can leave it to reduce your next bill, or ask for a refund. Which would you prefer?",
            "Refund my credit", "Can I get a refund of my credit balance?", fact=f"credit balance ${credit:,.2f}"))
    orders = _run_query("wgl_service_orders", {"account_id": customer["_id"]}, 10)
    moving = [order for order in orders if order.get("type") in ("stop", "transfer") and str(order.get("date", ""))[:10] >= today.isoformat()]
    if moving:
        when = str(sorted(moving, key=lambda order: str(order.get("date", "")))[0].get("date", ""))[:10]
        found.append(_insight(
            "moving", 44, "info", "Your move is scheduled", f"Service work is booked for {_pretty_date(when)}.",
            f"I see your move is scheduled for {_say_date(when)}. Would you like the details of that appointment?",
            "See my appointment", "Tell me about my service appointment", fact=f"service work booked {when}"))
    return found


def _partial_payments(customer: dict, today: date) -> list:
    """Payments of the last 90 days that covered only part of a bill (the payment row carries partial true)."""
    rows = _run_query("wgl_payments", {"account_id": customer["_id"]}, 30)
    recent = []
    for row in rows:
        paid = _parse_date(row.get("paid_date") or "")
        if row.get("partial") is True and row.get("status") in ("posted", "processed", "pending") and paid and 0 <= (today - paid).days <= 90:
            recent.append((paid, float(row.get("amount") or 0)))
    return sorted(recent, reverse=True)


def _hardship_insights(customer: dict, rows: list, today: date, arrears: float, signals: set) -> list:
    """Partial payments (or a payment the customer just said was only part): lead with help, never a pitch."""
    if arrears > 0 or customer.get("shutoff_date"):
        return []
    partial = _partial_payments(customer, today)
    balance = float(customer.get("balance") or 0)
    due = _parse_date(customer.get("due_date") or "")
    if partial:
        paid_on, amount = partial[0]
        count = len(partial)
        fact = f"{count} partial payment(s) in 90 days, latest ${amount:,.2f} on {paid_on.isoformat()}"
        summary = f"Your payment of ${amount:,.2f} on {_pretty_date(paid_on.isoformat())} covered part of the bill. If paying in full is hard, installment plans are interest free and there are assistance programs."
        spoken = (f"I see your payment of {_say_money(amount)} on {_say_date(paid_on.isoformat())} covered part of the bill. "
                  "If paying in full is hard, there are interest free payment plans and assistance programs, with no pressure. Would you like to hear about them?")
    elif "paid_in_part" in signals and balance > 0:
        fact = f"balance ${balance:,.2f}" + (f" due {due.isoformat()}" if due else "")
        due_text = f", due {_pretty_date(due.isoformat())}" if due else ""
        due_said = f", due {_say_date(due.isoformat())}" if due else ""
        summary = f"${balance:,.2f} is still open{due_text}. Installment plans are interest free and there are assistance programs, at your pace."
        spoken = f"{_cap(_say_money(balance))} is still open{due_said}. There are interest free payment plans and assistance programs, and no pressure. Would you like to hear about them?"
    else:
        return []
    tier = 1 if partial else 2
    return [_insight("hardship_partial", 26, "attention", "Help if paying in full is hard", summary, spoken,
                     "See my payment options", "I can't pay my whole bill, what are my options?", fact=fact, tier=tier, hold=False)]


def _tip_insights(customer: dict, rows: list, requested: bool) -> list:
    """Cold-weather tip, only when colder weather is a material share of the latest bill rise."""
    found = []
    delta, pct = _bill_change(rows)
    if len(rows) >= 2 and delta >= (15 if requested else 25) and pct >= 20:
        share = _weather_share(customer, rows[0], rows[1], delta)
        if share >= (25 if requested else 40):
            days_now, days_before = int(rows[0].get("days") or 0), int(rows[1].get("days") or 0)
            days_text = f" This bill covered {days_now} days, against {days_before} last time." if days_now and days_before and days_now != days_before else ""
            days_said = f" This bill covered {_say_count(days_now, 'day')}, against {_words(days_before)} last time." if days_now and days_before and days_now != days_before else ""
            found.append(_insight(
                "cold_weather_tip", 52, "saving", "Tips for a colder-weather bill",
                f"Colder weather explains about {share} percent of the ${delta:,.0f} rise.{days_text} Thermostat changes add up, and the Washington Gas tips page has more.",
                f"Colder weather explains about {_words(share)} percent of the rise.{days_said} Small thermostat changes add up. Would you like me to text you the Washington Gas tips page?",
                "Send me bill tips", "Send me the tips for managing my bill",
                saving=True, fact=f"colder weather about {share} percent of a ${delta:,.2f} rise"))
    return found


def _appliance_insights(customer: dict, today: date, signals: set) -> list:
    """Gas appliance safety: a data signal in the heating season, or a safety question from the customer; never otherwise."""
    heating = str(customer.get("heating_equipment") or "unknown")
    equipment = {"furnace": "furnace", "boiler": "boiler"}.get(heating, "gas appliances")
    link_label, link_message = "Send me safety tips", "Send me the gas appliance safety tips"
    if signals & {"co_question", "appliance_question"}:
        return [_insight(
            "appliance_safety", 46, "info", "Gas appliance safety", f"Washington Gas and the U.S. Consumer Product Safety Commission recommend a yearly check of your {equipment} by a qualified technician. Keep vents clear.",
            f"Washington Gas recommends a yearly check of your {equipment} by a qualified technician, and keeping vents clear. Would you like me to text you the safety page?",
            link_label, link_message, fact="the customer asked about gas appliance safety", tier=2, hold=True)]
    if today.month not in _HEATING_MONTHS:
        return []
    signal = ""
    call_type, call_date = str(customer.get("last_service_call_type") or ""), _parse_date(customer.get("last_service_call_date") or "")
    if call_type in ("pilot_relight", "gas_off", "gas_restored", "furnace_repair") and call_date and 0 <= (today - call_date).days <= 45:
        label = {"pilot_relight": "a pilot light visit", "gas_off": "a gas shut-off", "gas_restored": "a gas restore", "furnace_repair": "a heating repair"}[call_type]
        signal = f"{label} on {_pretty_date(call_date.isoformat())}"
        spoken_signal = f"{label} on {_say_date(call_date.isoformat())}"
    else:
        for appointment in _run_query("wgl_appointments", {"account_id": customer["_id"]}, 10):
            booked = _parse_date(str(appointment.get("slot", ""))[:10])
            if appointment.get("type") == "reconnect" and appointment.get("status") == "scheduled" and booked and 0 <= (booked - today).days <= 14:
                signal = f"a reconnect visit booked for {_pretty_date(booked.isoformat())}"
                spoken_signal = f"a reconnect visit booked for {_say_date(booked.isoformat())}"
                break
    if not signal:
        return []
    return [_insight(
        "appliance_safety", 46, "info", "Gas appliance safety before winter",
        f"I see {signal}. Washington Gas recommends a yearly pre-winter check of your {equipment} by a qualified technician, with vents and chimneys clear.",
        f"I see {spoken_signal}. Washington Gas recommends a yearly check of your {equipment} by a qualified technician before winter. Would you like me to text you the safety page?",
        link_label, link_message, fact=signal, tier=3, hold=True)]


def _repair_insights(customer: dict, signals: set) -> list:
    """Home repair plans: information only, after the customer mentions a repair or unexpected cost, for homeowners."""
    if "repair_cost" not in signals or customer.get("home_ownership") != "own" or customer.get("customer_type") == "Commercial":
        return []
    return [_insight(
        "home_repair", 80, "info", "Optional home repair plans",
        "Washington Gas lists optional home repair plans from HomeServe, an independent company separate from Washington Gas, starting at $5.49 a month. Choosing one or not never affects your gas service.",
        "For information only, Washington Gas lists optional home repair plans from HomeServe, an independent company that is separate from Washington Gas. Choosing one or not never affects your gas service. Would you like me to text you the page?",
        "Send me the page", "Send me the home repair plans page",
        saving=True, fact="owner on file; the customer mentioned a repair or unexpected cost", tier=2)]


def _manage_account_insights(customer: dict, task: str) -> list:
    """My Washington Gas as the next step after a task the customer could do alone; the lowest priority."""
    if task not in _SELF_SERVE_TASKS:
        return []
    if customer.get("portal_account_exists"):
        return [_insight(
            "manage_account", 90, "info", "Manage your account anywhere",
            "You can do this yourself any time in your online account at my.washingtongas.com or in the My Washington Gas app.",
            "You can also do this yourself any time in your online account or in the My Washington Gas app. Would you like me to text you the link?",
            "Text me the link", "Text me the link to My Washington Gas", fact=f"online account on file; task just finished: {task}", tier=4, hold=False)]
    return [_insight(
        "manage_account", 90, "info", "Manage your account anywhere",
        "You do not have an online account yet. With one you can pay, check your bill and manage your account in My Washington Gas or the app.",
        "You do not have an online account yet. With one you can pay and manage your account any time, online or in the app. Would you like me to set one up now?",
        "Set up my account", "Help me create my online account", fact=f"no online account on file; task just finished: {task}", tier=4, hold=False)]


def _savings_insights(customer: dict, rows: list, today: date, signals: set = frozenset()) -> list:
    """Savings and plan ideas; only ever called for a customer who is not in arrears."""
    found = []
    matches = [match for match in _match_programs(customer, "", today) if match["known_match"] and match["claimable"]]
    if matches:
        top = matches[0]
        urgent = top["days_left"] is not None and 0 <= top["days_left"] <= 60
        if urgent:
            summary = f"You may qualify for {top['amount']} back on {top['for']}. The site advises applying by {top['deadline']}, {top['days_left']} days from now."
            spoken = (f"You may qualify for a rebate of {_say_amount_text(top['amount'])} for {top['for']}. "
                      f"The site advises applying by {_say_date(top['deadline_iso'])}, which is {_say_count(top['days_left'], 'day')} from now. Would you like to hear more?")
            title = f"Rebate deadline in {top['days_left']} days"
        else:
            summary = f"You may qualify for {top['amount']} back on {top['for']}."
            spoken = f"You may qualify for a rebate of {_say_amount_text(top['amount'])} for {top['for']}. Would you like to hear more?"
            title = "You may qualify for a rebate"
        found.append(_insight("rebate", 56 if urgent else 64, "saving", title, summary, spoken, "See my rebates", "What rebates can I get?", True,
                              fact=f"{top['name']} matches the home profile on file, {top['amount']}"))
    amounts = [float(row.get("amount") or 0) for row in rows[:12]]
    if not customer.get("budget_plan_enrolled") and len(amounts) >= 9 and min(amounts) > 0 and max(amounts) / min(amounts) >= 2.5:
        found.append(_insight(
            "budget_plan", 60, "saving", "Bills that swing with the seasons",
            f"Your bills ran from ${min(amounts):,.0f} to ${max(amounts):,.0f} over the last year. A Budget Plan spreads winter heating costs over the year so you pay the same amount every month.",
            f"Your bills ran from about {_say_whole_dollars(min(amounts))} to {_say_whole_dollars(max(amounts))} over the last year. A Budget Plan spreads winter heating costs over the year so you pay the same amount every month. Would you like to hear how it works?",
            "Tell me about Budget Plan", "Tell me about the Budget Plan", True,
            fact=f"12-month bills ${min(amounts):,.2f} to ${max(amounts):,.2f}"))
    found += _autopay_insights(customer, today)
    found += _ebill_insights(customer, today, signals)
    return found


def _autopay_insights(customer: dict, today: date) -> list:
    """AutoPay from the checking account on file, when a due date is near or the bills are paid by hand every month."""
    if customer.get("autopay_enrolled"):
        return []
    banks = [method for method in _run_query("wgl_payment_methods", {"account_id": customer["_id"]}, 10) if method.get("type") == "bank"]
    if not banks:
        return []
    balance = float(customer.get("balance") or 0)
    due = _parse_date(customer.get("due_date") or "")
    pending = (customer.get("pending_payment") or {}).get("type")
    if balance > 0 and due and 0 <= (due - today).days <= 21 and not pending:
        fact = f"${balance:,.2f} due {due.isoformat()}, paid by hand, no AutoPay, checking account on file"
        lead = f"${balance:,.2f} is due {_pretty_date(due.isoformat())}. "
        lead_said = f"Your bill of {_say_money(balance)} is due {_say_date(due.isoformat())}. "
    else:
        manual = [row for row in _run_query("wgl_payments", {"account_id": customer["_id"]}, 30)
                  if row.get("status") == "posted" and row.get("partial") is not True and (_parse_date(row.get("paid_date") or "") or date.min) >= today - timedelta(days=120)]
        if len(manual) < 2:
            return []
        fact = f"{len(manual)} bills paid by hand in 120 days, no AutoPay, checking account on file"
        lead = f"You paid {len(manual)} bills by hand in the last four months. "
        lead_said = f"You paid {_words(len(manual))} bills by hand in the last four months. "
    return [_insight(
        "autopay", 66, "info", "AutoPay from your checking account",
        lead + "With AutoPay the bill is taken from your checking account on the due date, and you still get your monthly bill.",
        lead_said + "With AutoPay it comes out of the checking account on file on the due date, and you still get your monthly bill. Would you like me to set that up?",
        "Set up AutoPay", "Set up AutoPay for me", True, fact=fact)]


def _ebill_insights(customer: dict, today: date, signals: set) -> list:
    """eBill, only after a paper bill problem on the account or a duplicate-bill request, never as a general pitch."""
    if customer.get("ebil_flag"):
        return []
    issue = str(customer.get("bill_delivery_issue") or "")
    issue_date = _parse_date(customer.get("bill_delivery_issue_date") or "")
    if issue in ("returned_mail", "not_received") and issue_date and 0 <= (today - issue_date).days <= 90:
        what = "was returned undelivered" if issue == "returned_mail" else "did not arrive"
        fact, tier = f"paper bill {what} on {issue_date.isoformat()}", 1
        lead = f"Your paper bill {what} on {_pretty_date(issue_date.isoformat())}. "
        lead_said = f"Your paper bill {what} on {_say_date(issue_date.isoformat())}. "
    elif "duplicate_bill" in signals:
        fact, tier = "bill is delivered on paper by mail; the customer asked for a copy", 2
        lead, lead_said = "Your bill comes on paper by mail. ", "Your bill comes on paper by mail. "
    else:
        return []
    return [_insight(
        "ebill", 62, "info", "Get your bill by email with eBill",
        lead + "With eBill you get an email alert each month when your bill posts, and paper bills stop within one or two billing cycles.",
        lead_said + "With eBill you get an email each month when your bill posts, and paper bills stop within one or two billing cycles. Would you like to switch?",
        "Switch to eBill", "Sign me up for eBill", True, fact=fact, tier=tier)]


def _all_candidates(customer: dict, rows: list, today: date, arrears: float, signals: set, requested: bool, task: str = "") -> list:
    """Every insight the account data or the customer's words support, before moment, memory and arrears filtering."""
    candidates = _safety_insights(customer, today) + _payment_insights(customer, arrears, today)
    candidates += _bill_insights(customer, rows) + _account_insights(customer, today)
    candidates += _hardship_insights(customer, rows, today, arrears, signals)
    candidates += _savings_insights(customer, rows, today, signals)
    candidates += _tip_insights(customer, rows, requested)
    candidates += _appliance_insights(customer, today, signals)
    candidates += _repair_insights(customer, signals)
    candidates += _manage_account_insights(customer, task)
    return candidates


def _clean_signals(customer_said: str) -> set:
    """The customer_said argument as a set of lower-case signal words."""
    return {part.strip().lower() for part in str(customer_said or "").replace(";", ",").split(",") if part.strip()}


def _silent_result(reason: str, in_arrears: bool, moment: str) -> dict:
    """The answer when nothing may be suggested: no insights, no card, and an instruction to stay quiet about it."""
    return {
        "ok": True, "insights": [], "insights_card": {"insights": []}, "top_spoken": "", "suggestion": None, "silent": True,
        "reason": reason, "moment": moment, "in_arrears": in_arrears,
        "agent_action": "Make no suggestion, offer or tip now and do not mention this check. Carry on with what the customer asked.",
    }


def _get_account_insights_impl(account_id: str, moment: str = "review", customer_said: str = "", task: str = "", topic_response: str = "") -> dict:
    if _tenant() != "WGL":
        return _rule_error("account insights are only set up for Washington Gas customers", "continue with the request")
    moment = str(moment or "review").strip().lower() or "review"
    if moment not in _SILENT_MOMENTS + _REQUESTED_MOMENTS + _PROACTIVE_MOMENTS + ("record",):
        return _validation_error("moment: review, asked, bill_explained, task_done, customer_said, greeting, status, payment_confirmed or record")
    memory = _load_memory()
    recorded = _apply_response(memory, topic_response)
    if moment == "record":
        _save_memory(memory)
        result = _silent_result("recorded", False, moment)
        result["recorded"] = recorded
        return result
    signals = _clean_signals(customer_said)
    task = str(task or "").strip().lower()
    customer, error = _load_customer(account_id, "Identified")
    if error:
        return error
    # An offer that was shown and then neither accepted nor declined was ignored: that topic stays dropped.
    last = memory["last_offered"]
    if last and last not in memory["accepted"] and last not in memory["declined"] and last not in memory["ignored"]:
        memory["ignored"].append(last)
    memory["last_offered"] = ""
    today = _today()
    arrears = float(customer.get("arrears") or 0)
    in_arrears = arrears > 0 or bool(customer.get("shutoff_date"))
    reason = _silence_reason(moment, signals, memory)
    if not reason and in_arrears and moment in _PROACTIVE_MOMENTS:
        reason = "account_in_arrears"  # a customer with a past due balance or shutoff date gets help, never an unasked offer
    if reason:
        _save_memory(memory)
        return _silent_result(reason, in_arrears, moment)
    requested = moment in _REQUESTED_MOMENTS
    rows = _billing_rows(customer["_id"])
    candidates = [item for item in _all_candidates(customer, rows, today, arrears, signals, requested, task) if _allowed(item["id"], moment, signals, task)]
    if in_arrears:
        candidates = [item for item in candidates if not item["hold"]]
    taken = set(memory["shown"]) | set(memory["declined"])
    candidates = [item for item in candidates if item["id"] not in taken and _value_test(item)]
    ids = {item["id"] for item in candidates}
    if ids & {"shutoff", "past_due"}:
        candidates = [item for item in candidates if item["id"] != "hardship_partial"]
    if task:
        candidates = [item for item in candidates if item["id"] != task]
    candidates.sort(key=lambda item: (item["tier"], item["rank"]))
    limit = _RETURN_LIMIT.get(moment, 1)
    chosen = candidates[:min(limit, _MAX_INSIGHTS)]
    if not chosen:
        _save_memory(memory)
        return _silent_result("nothing_relevant", in_arrears, moment)
    keys = ("id", "title", "summary", "severity", "action_label", "action_message", "spoken_summary")
    insights = [{key: item[key] for key in keys} for item in chosen]
    card = [{key: item[key] for key in keys if key != "spoken_summary"} for item in chosen]
    seen = chosen if _channel() == "chat" else chosen[:1]
    memory["shown"] += [item["id"] for item in seen if item["id"] not in memory["shown"]]
    memory["last_offered"] = seen[0]["id"]
    if moment in _PROACTIVE_MOMENTS:
        memory["offers"] += 1
    _save_memory(memory)
    if requested:
        action = (
            "One insight at a time: on voice speak top_spoken only, then wait; speak the next spoken_summary only if the customer wants more. "
            "On chat show the card and add one short sentence. Never read the card text again. State only what the insights say."
        )
    else:
        action = (
            "This is the one suggestion for this reply, and only after the customer's task is done. On voice say top_spoken as one short question; "
            "on chat show the card with show_insights_carousel and add one short sentence. Never add a second suggestion. "
            "When the customer says no or ignores it, call get_account_insights with moment record and topic_response set to the id and declined, and never raise it again. "
            "When the customer says yes, do what the action says and call it with moment record and topic_response set to the id and accepted."
        )
    if in_arrears:
        action += " The account has a past due balance or a shutoff date: lead with help and kindness, and make no saving, tip, rebate, AutoPay, eBill or Budget Plan offer in this conversation."
    return {
        "ok": True, "insights": insights, "insights_card": {"insights": card}, "top_spoken": insights[0]["spoken_summary"], "suggestion": insights[0],
        "silent": False, "reason": "", "moment": moment, "facts": {item["id"]: item["fact"] for item in chosen},
        "in_arrears": in_arrears, "more_available": len(candidates) > len(chosen), "agent_action": action,
    }
