import json
import re
import time
from datetime import date, datetime, timedelta, timezone


def process_payment_cc(
    account_id: str = "",
    amount: float = 0.0,
    token: str = "",
    gateway: str = "",
    tenant: str = "",
) -> dict:
    """Charges the card on file. The token is the card-on-file reference from the customer's saved
    payment methods, never a card number. Enforces the payment amount limits and counts consecutive
    declines. Needs an authenticated caller who confirmed the amount and the last 4 digits of the
    card. Before calling this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        account_id: Account id of the authenticated account (REQUIRED).
        amount: Payment amount in dollars the customer confirmed (REQUIRED).
        token: Card-on-file reference returned for the saved card (REQUIRED); never a card number.
        gateway: Payment gateway label of the tenant, for the record.
        tenant: Injected by the system from tenant_id; leave empty.

    Returns:
        dict with ok, confirmation_number, amount, convenience_fee (zero for a Virginia account,
        which pays no card fee), total_charged, card_last4, new_balance, expected_posting_date,
        cancellable_until_posted, shutoff_targeted; on a decline ok is false with declined and
        declines.
    """
    try:
        return _finish("process_payment_cc", _process_payment_cc_impl(account_id=account_id, amount=amount, token=token, gateway=gateway))
    except Exception as err:
        return _unexpected_error("process_payment_cc", err)


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
    """Commits all writes atomically."""
    return _first_dict(_payload(tools.wgl_firestore_commit({"writes": writes})))


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
            "detail": detail,
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


def _today() -> date:
    """Today's date on the demo clock."""
    return _now().date()


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


def _customer_jurisdiction(customer: dict) -> str:
    """Jurisdiction of the account being served: the customer record, else identity_ctx; empty when unknown."""
    return str(customer.get("jurisdiction") or _object_var("identity_ctx").get("jurisdiction") or "").strip().upper()


def _fee_entry(customer: dict) -> dict:
    """The tenant config convenience fee entry for the customer's jurisdiction; empty when none is configured."""
    fees = _tenant_config().get("convenience_fees") or {}
    entry = fees.get(_customer_jurisdiction(customer=customer))
    return entry if isinstance(entry, dict) else {}


def _convenience_fee(customer: dict) -> float:
    """Card convenience fee for the customer's jurisdiction (Maryland and DC charge it, Virginia does not).

    Residential or commercial amount from the tenant config; zero when the jurisdiction has no fee entry.
    """
    entry = _fee_entry(customer=customer)
    return _money(entry.get("commercial" if customer.get("customer_type") == "Commercial" else "residential"))


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


# Washington Gas site FAQ: card payments (with the convenience fee) are limited per transaction. WGL only.
_CARD_LIMIT_RESIDENTIAL = 1000.00


_CARD_LIMIT_COMMERCIAL = 5000.00


def _card_limit_error(amount: float, customer: dict) -> dict:
    """Refuses a card amount above the per-transaction card limit and offers the no-limit alternatives."""
    if _tenant() != "WGL":
        return {}
    commercial = customer.get("customer_type") == "Commercial"
    entry = _fee_entry(customer=customer)
    configured = entry.get("commercial_limit" if commercial else "residential_limit")
    default_limit = _CARD_LIMIT_COMMERCIAL if commercial else _CARD_LIMIT_RESIDENTIAL
    limit = float(configured) if configured else default_limit
    if amount <= limit:
        return {}
    kind = "commercial" if commercial else "residential"
    problem = _rule_error(
        f"card payments are limited to ${limit:,.0f} per transaction for a {kind} account",
        "offer an eCheck or ACH payment from a bank account (no fee and no limit), a personal check or cash, "
        "or paying part of the amount by card now and the rest another way",
    )
    problem["card_limit"] = limit
    return problem


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


def _record_payment(customer: dict, amount: float, fee: float, method: str, extra: dict) -> dict:
    """Writes the payment, the customer balance and the outbox notification in one atomic commit."""
    confirmation = _reference_number("confirmations")
    posting_date = (_today() + timedelta(days=1)).isoformat()
    new_balance = _money(float(customer.get("balance") or 0) - amount)
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
    }
    payment_fields.update(extra)
    writes = [
        _set_write("wgl_payments", confirmation, payment_fields, "new"),
        _set_write("wgl_customers", customer["_id"], {"balance": new_balance, "last_payment_amt": amount, "pending_payment": pending}, "must"),
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
    merge_ctx("account_ctx", {"balance": new_balance, "last_payment_amt": amount, "pending_payment_type": "P", "pending_payment_amt": amount})
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
    }


def _process_payment_cc_impl(account_id: str, amount: float, token: str, gateway: str) -> dict:
    blocked = _degraded_write_block()
    if blocked:
        return blocked
    customer, error = _load_customer(account_id, "Authenticated")
    if error:
        return error
    value = _money(amount)
    if len(_digits(token)) >= 13 and not re.search(r"[A-Za-z]", token):
        return _validation_error("a card-on-file reference, never a card number")
    problem = _amount_error(value, float(customer.get("balance") or 0))
    if problem:
        return problem
    problem = _card_limit_error(value, customer)
    if problem:
        return problem
    cards = _run_query("wgl_payment_methods", {"account_id": customer["_id"], "type": "card"}, 10)
    card = next((item for item in cards if item.get("token_ref") == token.strip()), None)
    if not card:
        return _rule_error("only the card on file can be used", "use the card on file or another payment method")
    required = _object_var("payment_ctx").get("pa_downpayment_amount")
    if _pending_downpayment() and required is not None and abs(value - float(required)) > 0.001:
        return _rule_error("the payment arrangement down payment must be paid in full", f"pay the down payment of {required}")
    if _injected_failure("gateway", _controls()):
        return _decline_result("card", customer["_id"])
    result = _record_payment(customer, value, _convenience_fee(customer), "card", {"card_last4": card.get("last4", ""), "gateway": gateway})
    result["card_last4"] = card.get("last4", "")
    return result
