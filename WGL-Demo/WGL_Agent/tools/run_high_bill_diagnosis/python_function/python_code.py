import json
import time


def run_high_bill_diagnosis(account_id: str = "") -> dict:
    """Explains a high bill or high usage with one call. Runs the billing history, read type, period
    days, weather and weather normalization lookups and returns the raw numbers, an attribution of
    the increase that reconciles to the bill difference, and flags. Explain only from these numbers.
    Reads only; needs an identified caller. Before calling this tool, speak a brief conversational
    pacing phrase while it runs.

    Args:
        account_id: Account id of the identified account (REQUIRED).

    Returns:
        dict with ok, lookups, history, read_type, period_days, weather, usage, wna, bill_delta,
        attribution, reconciliation and flags (estimated_read, unexplained_anomaly,
        seasonal_spike, arrears_present, jurisdiction, wna_applicable); on failure ok is false
        with agent_action.
    """
    try:
        return _finish("run_high_bill_diagnosis", _run_high_bill_diagnosis_impl(account_id=account_id))
    except Exception as err:
        return _unexpected_error("run_high_bill_diagnosis", err)


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


def _wna_applies(customer: dict) -> bool:
    """WNA applies to Washington Gas customers in Virginia only (BR-GU-018, BR-GU-029)."""
    return _tenant() == "WGL" and customer.get("jurisdiction") == "VA"


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


def _period_summary(row: dict) -> dict:
    """The fields of a billing period the model needs."""
    return {
        "period": row["period"],
        "start": row.get("start", ""),
        "end": row.get("end", ""),
        "days": row.get("days"),
        "amount": _money(row.get("amount")),
        "usage": row.get("usage"),
        "unit": _usage_unit(),
        "read_type": row.get("read_type", ""),
        "rate_per_unit": row.get("rate_per_unit"),
    }


# Demo defaults from the TDD (rev 6); wgl_tenant_config.diagnosis overrides them so they can be tuned.
_UNEXPLAINED_MIN_DOLLARS = 15.00


_UNEXPLAINED_PCT_OF_DELTA = 20.0


_SEASONAL_HDD_PCT = 10.0


_SEASONAL_SHARE_PCT = 50.0


_RECENT_PERIODS = 3


def _prior_year_period(period: str) -> str:
    """The same month one year earlier, from a YYYY-MM period key."""
    year, _, month = period.partition("-")
    return f"{int(year) - 1}-{month}" if year.isdigit() else ""


def _diagnosis_failure(lookups: list) -> dict:
    """A required lookup failed: honest error, no figures."""
    _set_var("api_failed", "true")
    return _error(
        "transient",
        "A required lookup failed. Retry once without telling the customer; if it fails again say the account "
        "systems are having trouble, state no figures, and offer a representative (system_unavailable).",
        lookups=lookups,
    )


def _wna_on_current_bill(customer: dict, current: dict) -> bool:
    """WNA explains this bill only when the account has an amount and the current bill carries a WNA line.

    Website: credits appear in full on the August bill, so an October bill whose credit sits on the August bill is not
    explained by it; a Virginia account with no amount (null) has nothing to report either.
    """
    return bool(_wna_applies(customer) and customer.get("wna_amount") is not None and float(current.get("wna_line") or 0) != 0)


def _attribution(previous: dict, current: dict, weather: dict, wna_applies: bool) -> dict:
    """Splits the bill increase into factors; the factors plus the residual always equal the delta."""
    a0, a1 = float(previous.get("amount") or 0), float(current.get("amount") or 0)
    d0, d1 = int(previous.get("days") or 0), int(current.get("days") or 0)
    u0, u1 = float(previous.get("usage") or 0), float(current.get("usage") or 0)
    r0, r1 = float(previous.get("rate_per_unit") or 0), float(current.get("rate_per_unit") or 0)
    h0, h1 = float(weather.get("hdd_prior_period") or 0), float(weather.get("hdd_current") or 0)
    delta = round(a1 - a0, 2)
    weather_usage = round(r0 * u0 * (d1 / d0) * (h1 / h0 - 1), 2) if d0 and h0 else 0.0
    if d0:
        # Clamp: the weather share can never exceed the usage change that actually happened (shoulder months).
        actual_usage_change = round(r0 * (u1 - u0 * (d1 / d0)), 2)
        if weather_usage * actual_usage_change <= 0:
            weather_usage = 0.0
        elif abs(weather_usage) > abs(actual_usage_change):
            weather_usage = actual_usage_change
    factors = {
        "period_length": round(a0 * (d1 - d0) / d0, 2) if d0 else 0.0,
        "weather_usage": weather_usage,
        "wna": round(float(current.get("wna_line") or 0) - float(previous.get("wna_line") or 0), 2) if wna_applies else 0.0,
        "rate_pga": round((r1 - r0) * u1, 2),
        "estimated_read_correction": round(float(current.get("estimate_adjustment") or 0), 2),
    }
    explained = round(sum(factors.values()), 2)
    residual = round(delta - explained, 2)
    return {
        "delta": delta,
        "factors": dict(factors, unexplained_residual=residual),
        "reconciliation": {
            "sum_explained": explained,
            "residual": residual,
            "delta": delta,
            "reconciled": abs(explained + residual - delta) <= 0.01,
            "tolerance": 0.01,
        },
    }


def _flags(customer: dict, current: dict, attribution: dict, hdd_change_pct: float) -> dict:
    """Diagnosis flags using the configurable thresholds."""
    settings = _tenant_config().get("diagnosis") or {}
    delta = attribution["delta"]
    residual = attribution["reconciliation"]["residual"]
    floor = float(settings.get("unexplained_min_dollars", _UNEXPLAINED_MIN_DOLLARS))
    share_of_delta = float(settings.get("unexplained_pct_of_delta", _UNEXPLAINED_PCT_OF_DELTA)) / 100
    seasonal_hdd = float(settings.get("seasonal_hdd_change_pct", _SEASONAL_HDD_PCT))
    seasonal_share = float(settings.get("seasonal_share_of_delta_pct", _SEASONAL_SHARE_PCT)) / 100
    weather_usage = attribution["factors"]["weather_usage"]
    return {
        "estimated_read": current.get("read_type") == "estimated",
        "unexplained_anomaly": abs(residual) > max(floor, share_of_delta * abs(delta)),
        "seasonal_spike": hdd_change_pct >= seasonal_hdd and delta > 0 and weather_usage >= seasonal_share * delta,
        "arrears_present": float(customer.get("arrears") or 0) > 0,
        "jurisdiction": customer.get("jurisdiction", ""),
        "wna_applicable": _wna_on_current_bill(customer, current),
    }


def _wna_not_on_bill(customer: dict) -> dict:
    """The wna block when WNA does not explain the current bill."""
    if _wna_applies(customer) and customer.get("wna_amount") is not None:
        return {"applies": False, "agent_action": "The weather normalization credit or charge is not on the current bill (a credit is applied in full on the August bill). Do not use it to explain this bill."}
    return {"applies": False}


# Rev 25: the factors of the bill change as card-ready rows (chat trend card `reasons`); only non-zero factors, largest first,
# every figure comes from the attribution above. Labels follow the session language.
_REASON_TEXT = {
    "EN": {
        "weather_usage": "Colder weather, more heating",
        "period_up": "Longer billing cycle ({cur} vs {prior} days)",
        "period_down": "Shorter billing cycle ({cur} vs {prior} days)",
        "estimated_read_correction": "Estimated meter read corrected",
        "rate_pga": "Gas cost rate (PGC) change",
        "wna": "Weather normalization adjustment",
        "total_up": "about {amount} higher in total",
        "total_down": "about {amount} lower in total",
    },
    "ES": {
        "weather_usage": "Clima m\u00e1s fr\u00edo, m\u00e1s calefacci\u00f3n",
        "period_up": "Ciclo de facturaci\u00f3n m\u00e1s largo ({cur} frente a {prior} d\u00edas)",
        "period_down": "Ciclo de facturaci\u00f3n m\u00e1s corto ({cur} frente a {prior} d\u00edas)",
        "estimated_read_correction": "Lectura estimada del medidor corregida",
        "rate_pga": "Cambio en la tarifa del costo del gas (PGC)",
        "wna": "Ajuste por normalizaci\u00f3n del clima",
        "total_up": "unos {amount} m\u00e1s en total",
        "total_down": "unos {amount} menos en total",
    },
}
_REASON_ICON = {"weather_usage": "flame", "period_length": "calendar", "estimated_read_correction": "info", "rate_pga": "money", "wna": "info"}


def _reason_rows(attribution: dict, current: dict, previous: dict) -> dict:
    """Card-ready reasons plus the one-line total, from the attribution only."""
    texts = _REASON_TEXT["ES" if _var("language", "EN").upper() == "ES" else "EN"]
    cur_days, prior_days = int(current.get("days") or 0), int(previous.get("days") or 0)
    rows = []
    for key, amount in attribution["factors"].items():
        if key == "unexplained_residual" or abs(float(amount)) < 0.005:
            continue
        if key == "period_length":
            label = texts["period_up" if float(amount) > 0 else "period_down"].format(cur=cur_days, prior=prior_days)
        else:
            label = texts[key]
        rows.append({"label": label, "amount": round(float(amount), 2), "direction": "up" if float(amount) > 0 else "down", "icon": _REASON_ICON.get(key, "info")})
    rows.sort(key=lambda row: abs(row["amount"]), reverse=True)
    delta = float(attribution["delta"])
    total = texts["total_up" if delta >= 0 else "total_down"].format(amount=f"${round(abs(delta)):,}")
    return {"reasons": rows, "reasons_total_note": total}


def _diagnose(customer: dict, rows: list) -> dict:
    """Runs the five lookups and builds the raw diagnosis the agent explains from."""
    lookups = [{"name": "get_billing_history", "status": "ok" if len(rows) >= 2 else "failed", "reason": "" if len(rows) >= 2 else "fewer than two billing periods"}]
    if len(rows) < 2:
        return _diagnosis_failure(lookups)
    current, previous = rows[0], rows[1]
    prior_year = next((row for row in rows if row["period"] == _prior_year_period(current["period"])), None)
    lookups.append({"name": "get_read_type", "status": "ok", "reason": ""})
    lookups.append({"name": "period_days", "status": "ok", "reason": ""})
    weather = _get_doc("wgl_weather_hdd", f"{customer.get('zip', '')}_{current['period']}")
    lookups.append({"name": "nws_hdd_api", "status": "ok" if weather else "failed", "reason": "" if weather else "no weather data for the ZIP and period"})
    if not weather:
        return _diagnosis_failure(lookups)
    wna_applies = _wna_on_current_bill(customer, current)
    lookups.append({"name": "get_wna_amount", "status": "ok" if _wna_applies(customer) else "skipped", "reason": "" if _wna_applies(customer) else "not_wgl_virginia"})
    attribution = _attribution(previous, current, weather, wna_applies)
    hdd_prior_year = float(weather.get("hdd_prior_year") or 0)
    hdd_change_pct = round((float(weather.get("hdd_current") or 0) / hdd_prior_year - 1) * 100, 1) if hdd_prior_year else 0.0
    recent = rows[:_RECENT_PERIODS] + ([prior_year] if prior_year and prior_year not in rows[:_RECENT_PERIODS] else [])
    prior_year_usage = prior_year.get("usage") if prior_year else None
    current_usage = current.get("usage")
    return {
        "ok": True,
        "lookups": lookups,
        "history": [_period_summary(row) for row in recent],
        "read_type": {"current": current.get("read_type", ""), "prior": previous.get("read_type", "")},
        "period_days": {"current": current.get("days"), "prior": previous.get("days"), "delta": int(current.get("days") or 0) - int(previous.get("days") or 0)},
        "weather": {
            "hdd_current": weather.get("hdd_current"),
            "hdd_prior_period": weather.get("hdd_prior_period"),
            "hdd_prior_year": weather.get("hdd_prior_year"),
            "hdd_change_vs_prior_year_pct": hdd_change_pct,
        },
        "usage": {
            "current": current_usage,
            "prior_period": previous.get("usage"),
            "prior_year_same_period": prior_year_usage,
            "change_vs_prior_period": round(float(current_usage or 0) - float(previous.get("usage") or 0), 2),
            "change_vs_prior_year": round(float(current_usage or 0) - float(prior_year_usage), 2) if prior_year_usage is not None else None,
            "unit": _usage_unit(),
        },
        "wna": {"applies": True, "amount": customer.get("wna_amount"), "direction": customer.get("wna_direction")} if wna_applies else _wna_not_on_bill(customer),
        "bill_delta": {
            "current_amount": _money(current.get("amount")),
            "prior_amount": _money(previous.get("amount")),
            "delta_amount": attribution["delta"],
            "delta_pct": round(attribution["delta"] / float(previous.get("amount")) * 100, 1) if float(previous.get("amount") or 0) else 0.0,
        },
        "attribution": attribution["factors"],
        "reconciliation": attribution["reconciliation"],
        "flags": _flags(customer, current, attribution, hdd_change_pct),
        **_reason_rows(attribution, current, previous),
    }


def _run_high_bill_diagnosis_impl(account_id: str) -> dict:
    customer, error = _load_customer(account_id, "Identified")
    if error:
        return error
    return _diagnose(customer, _billing_rows(customer["_id"]))
