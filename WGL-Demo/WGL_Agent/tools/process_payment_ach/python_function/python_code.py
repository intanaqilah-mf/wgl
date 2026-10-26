import json
import re
import time
from datetime import date, datetime, timedelta, timezone


def process_payment_ach(
    account_id: str = "",
    amount: float = 0.0,
    routing: str = "",
    acct: str = "",
    type: str = "",
    tenant: str = "",
) -> dict:
    """Takes a one-time bank (ACH / eCheck) payment from the bank details the customer gives. Enforces
    the payment amount limits and counts consecutive declines. Needs an authenticated caller who
    heard the required ACH disclosures and agreed. The bank details are never repeated back or
    stored. Before calling this tool, speak a brief conversational pacing phrase while it runs.
    Refuses until payment_pci_preamble, return_fee_warning and ach_authorization_disclosure have all
    been spoken. The same account, amount and bank account paid twice in one session returns the
    first confirmation with duplicate_suppressed true instead of debiting again.

    Args:
        account_id: Leave empty: the identified account is used automatically (any other id is refused).
        amount: Payment amount in dollars the customer confirmed (REQUIRED).
        routing: Nine-digit bank routing number (REQUIRED).
        acct: Bank account number (REQUIRED).
        type: personal or company for tenants that ask; otherwise empty.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, confirmation_number, amount, total_charged, bank_last4, new_balance,
        expected_posting_date, cancellable_until_posted, shutoff_targeted, new_arrears,
        shutoff_cleared (a payment reduces the past due amount and removes the shutoff date once
        it reaches zero); duplicate_suppressed when the first confirmation is returned again; on
        a decline ok is false with declined and declines.
    """
    try:
        return _finish("process_payment_ach", _process_payment_ach_impl(account_id=account_id, amount=amount, routing=routing, acct=acct, type=type))
    except Exception as err:
        return _unexpected_error("process_payment_ach", err)


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


def _money(value) -> float:
    """Rounds an amount to cents."""
    return round(float(value or 0), 2)


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


# PRD s12 WGL-11: amount limits applied before the gateway is called (WGL; error names the constraint).
_MIN_PAYMENT = 1.00


_MAX_PAYMENT = 99999.99


_OVER_BALANCE_TOLERANCE = 5.00


# PRD s12 WGL-15 / BR-GU-041: three consecutive declines force a live-agent handoff.
_MAX_CONSECUTIVE_DECLINES = 3


def _amount_error(amount: float, balance: float) -> dict:
    """Checks the payment amount limits; the error names the violated constraint (BR-GU-040)."""
    if _tenant() != "WGL":
        return {} if amount > 0 else _validation_error("an amount above zero")
    if amount < _MIN_PAYMENT:
        return _rule_error("the minimum payment is $1.00", "enter a higher amount")
    if amount > _MAX_PAYMENT:
        return _rule_error("the maximum payment is $99,999.99", "enter a lower amount")
    if amount > balance + _OVER_BALANCE_TOLERANCE:
        return _rule_error("a payment cannot exceed the balance due by more than $5.00", "enter an amount up to the balance due plus $5.00")
    return {}


# Verbatim texts that must have been spoken before the bank account is debited (billing instruction, Pay_By_Bank).
_ACH_DISCLOSURES = ("payment_pci_preamble", "return_fee_warning", "ach_authorization_disclosure")


def _decline_result(method_label: str, account_id: str) -> dict:
    """Counts a consecutive decline and returns the retry or forced-handoff guidance (BR-GU-041)."""
    count = int(_var("payment_decline_count", "0") or 0) + 1
    _set_var("payment_decline_count", str(count))
    _commit([_set_write("wgl_sessions", _session_id(), {"tenant": _tenant(), "consecutive_declines": count})])
    if count >= _MAX_CONSECUTIVE_DECLINES:
        reason = "system_unavailable"
        if _pending_downpayment():
            _reverse_pa_enrollment(account_id)
            reason = "pa_downpayment_failed"
        return _error(
            "business_rule",
            "Three payments in a row were declined. Say a warm hand-off line, then call set_session_state with "
            f"_action_trigger live_agent_handoff, _escalation_reason {reason} and _escalation_topic billing.",
            declined=True,
            declines=count,
        )
    return _error(
        "business_rule",
        f"The {method_label} payment was declined and nothing was charged. Say so kindly and offer to try a "
        "different card or account before offering a representative.",
        declined=True,
        declines=count,
    )


def _pending_downpayment() -> bool:
    """True while a payment-arrangement down payment is waiting to be paid."""
    return _var("pa_downpayment_status") == "pending"


def _reverse_pa_enrollment(account_id: str) -> None:
    """Reverses the pending payment-arrangement enrollment after a failed down payment (BR-GU-044)."""
    enrollments = _run_query("wgl_pa_enrollments", {"account_id": account_id, "status": "pending_downpayment"})
    writes = [_set_write("wgl_pa_enrollments", item["_id"], {"status": "reversed", "reason": "pa_downpayment_failed"}, "must") for item in enrollments]
    if writes:
        _commit(writes)
    _set_var("pa_downpayment_status", "failed")


def _complete_pa_enrollment(account_id: str) -> list:
    """Builds the writes that activate the pending payment arrangement once the down payment is paid."""
    enrollments = _run_query("wgl_pa_enrollments", {"account_id": account_id, "status": "pending_downpayment"})
    _set_var("pa_downpayment_status", "paid")
    return [_set_write("wgl_pa_enrollments", item["_id"], {"status": "active"}, "must") for item in enrollments]


def _amount_value(amount) -> tuple:
    """The amount rounded to cents; (0.0, error) when it is not a usable number."""
    try:
        value = _money(amount)
    except (TypeError, ValueError):
        return 0.0, _validation_error("a payment amount")
    if value != value or value in (float("inf"), float("-inf")):
        return 0.0, _validation_error("a payment amount")
    return value, {}


def _verbatim_given() -> set:
    """Keys of the verbatim texts already fetched in this session: the payment_disclosures_given flag, else the session event log."""
    given = {item.strip() for item in _var("payment_disclosures_given").split(",") if item.strip()}
    rows = _run_query("wgl_session_events", {"session_id": _session_id(), "event_type": "verbatim_emitted"}, 200)
    for row in rows:
        try:
            detail = json.loads(str(row.get("detail") or "{}"))
        except ValueError:
            continue
        if isinstance(detail, dict) and detail.get("key"):
            given.add(str(detail["key"]))
    return given


def _disclosure_error(required: tuple) -> dict:
    """Refuses to charge until the payment disclosures were fetched (and so spoken) in this session; WGL only."""
    if _tenant() != "WGL":
        return {}
    given = _verbatim_given()
    missing = [key for key in required if key not in given]
    if not missing:
        return {}
    first = missing[0]
    wait = "a clear yes" if first == "ach_authorization_disclosure" else "the customer's reply"
    return _error(
        "business_rule",
        f"A required payment disclosure has not been spoken yet. Call get_verbatim_string with key {first}, let the system "
        f"speak it exactly, wait for {wait}, then call this tool again with the same details. Do not tell the customer "
        "that the payment failed.",
        disclosure_required=missing,
    )


def _duplicate_payment(customer: dict, value: float, method: str, last4_field: str, last4: str) -> dict:
    """The payment this session already took for the same account, amount and card or bank account; empty when none."""
    rows = _run_query("wgl_payments", {"session_id": _session_id(), "account_id": customer["_id"], "amount": value, "method": method}, 20)
    for row in rows:
        if row.get("status") == "cancelled":
            continue
        if str(row.get(last4_field) or "") == str(last4 or ""):
            if not _is_recent(row.get("created_at")):
                continue   # an equal payment taken minutes ago is a deliberate second one, not a repeated call
            return row
    return {}


_DUPLICATE_WINDOW_SECONDS = 300


def _is_recent(stamp) -> bool:
    """True when the timestamp is within the duplicate window of the real clock; an unreadable or missing stamp counts as recent."""
    try:
        created = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds() <= _DUPLICATE_WINDOW_SECONDS
    except (TypeError, ValueError):
        return True


def _duplicate_result(row: dict, customer: dict) -> dict:
    """The first confirmation again, for a payment submitted twice in the same session."""
    fee = _money(row.get("fee"))
    amount = _money(row.get("amount"))
    pending = customer.get("pending_payment") or {}
    posting = row.get("expected_posting_date") or (pending.get("posting_date") if pending.get("payment_id") == row["_id"] else "") or ""
    return {
        "ok": True,
        "duplicate_suppressed": True,
        "confirmation_number": row.get("confirmation_number") or row["_id"],
        "amount": amount,
        "convenience_fee": fee,
        "total_charged": _money(amount + fee),
        "new_balance": _money(customer.get("balance")),
        "expected_posting_date": posting,
        "cancellable_until_posted": _tenant() == "WGL",
        "shutoff_targeted": bool(row.get("shutoff_targeted", customer.get("shutoff_date"))),
        "pa_downpayment_status": _var("pa_downpayment_status"),
        "agent_action": "This payment was already taken earlier in this conversation and was not charged again. Give the same "
                        "confirmation number, amount and posting date as before; never say it was charged twice.",
    }


def _record_payment(customer: dict, amount: float, fee: float, method: str, extra: dict) -> dict:
    """Writes the payment, the customer balance (and the arrears it pays down) and the outbox notification in one atomic commit."""
    confirmation = _reference_number("confirmations")
    posting_date = (_today() + timedelta(days=1)).isoformat()
    new_balance = _money(float(customer.get("balance") or 0) - amount)
    arrears = _money(customer.get("arrears"))
    new_arrears = max(0.0, _money(arrears - amount))
    clears_shutoff = arrears > 0 and new_arrears == 0 and bool(customer.get("shutoff_date"))
    pending = {"type": "P", "amt": amount, "posting_date": posting_date, "cancel_eligible": True, "payment_id": confirmation}
    payment_fields = {
        "account_id": customer["_id"],
        "tenant": _tenant(),
        "amount": amount,
        "fee": fee,
        "method": method,
        "status": "pending",
        "confirmation_number": confirmation,
        "session_id": _session_id(),
        "expected_posting_date": posting_date,
        "shutoff_targeted": bool(customer.get("shutoff_date")),
        "arrears_applied": _money(arrears - new_arrears),
        "shutoff_date_cleared": str(customer.get("shutoff_date") or "") if clears_shutoff else "",
    }
    payment_fields.update(extra)
    customer_fields = {"balance": new_balance, "last_payment_amt": amount, "pending_payment": pending}
    if arrears > 0:
        customer_fields["arrears"] = new_arrears
    if clears_shutoff:
        customer_fields["shutoff_date"] = ""
    writes = [
        _set_write("wgl_payments", confirmation, payment_fields, "new"),
        _set_write("wgl_customers", customer["_id"], customer_fields, "must"),
        _set_write("wgl_notifications", _new_id(), {
            "account_id": customer["_id"],
            "tenant": _tenant(),
            "type": "payment_confirmation",
            "channel": "sms",
            "reference": confirmation,
        }, "new"),
    ]
    if _pending_downpayment():
        writes.extend(_complete_pa_enrollment(customer["_id"]))
    _commit(writes)
    _set_var("payment_decline_count", "0")
    ctx_patch = {"balance": new_balance, "last_payment_amt": amount, "pending_payment_type": "P", "pending_payment_amt": amount}
    if arrears > 0:
        ctx_patch["arrears"] = new_arrears
    if clears_shutoff:
        ctx_patch["shutoff_date"] = ""
    merge_ctx("account_ctx", ctx_patch)
    return {
        "ok": True,
        "confirmation_number": confirmation,
        "amount": amount,
        "convenience_fee": fee,
        "total_charged": _money(amount + fee),
        "new_balance": new_balance,
        "expected_posting_date": posting_date,
        "cancellable_until_posted": _tenant() == "WGL",
        "shutoff_targeted": bool(customer.get("shutoff_date")),
        "pa_downpayment_status": _var("pa_downpayment_status"),
        "new_arrears": new_arrears if arrears > 0 else _money(customer.get("arrears")),
        "shutoff_cleared": clears_shutoff,
    }


def _process_payment_ach_impl(account_id: str, amount: float, routing: str, acct: str, type: str) -> dict:
    blocked = _degraded_write_block()
    if blocked:
        return blocked
    customer, error = _load_customer(account_id, "Authenticated")
    if error:
        return error
    value, problem = _amount_value(amount)
    if problem:
        return problem
    routing_digits, account_digits = _digits(routing), _digits(acct)
    if len(routing_digits) != 9:
        return _validation_error("a nine-digit routing number")
    if not 4 <= len(account_digits) <= 17:
        return _validation_error("the bank account number")
    if type not in ("", "personal", "company"):
        return _validation_error("personal or company")
    # A double submit is answered with the first confirmation before any limit is applied (the balance has already come down).
    duplicate = _duplicate_payment(customer, value, "ach", "bank_last4", account_digits[-4:])
    if duplicate:
        result = _duplicate_result(duplicate, customer)
        result["bank_last4"] = account_digits[-4:]
        return result
    problem = _amount_error(value, float(customer.get("balance") or 0))
    if problem:
        return problem
    required = _object_var("payment_ctx").get("pa_downpayment_amount")
    if _pending_downpayment() and required is not None and abs(value - float(required)) > 0.001:
        return _rule_error("the payment arrangement down payment must be paid in full", f"pay the down payment of ${float(required):,.2f}")
    problem = _disclosure_error(_ACH_DISCLOSURES)
    if problem:
        return problem
    if _injected_failure("gateway", _controls()):
        return _decline_result("bank", customer["_id"])
    result = _record_payment(customer, value, 0.0, "ach", {"bank_last4": account_digits[-4:], "account_type": type})
    result["bank_last4"] = account_digits[-4:]
    return result
