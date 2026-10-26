import json
import math
import re
import time
from datetime import datetime, timedelta, timezone


def knowledge_base_search(query: str = "", namespace: str = "") -> dict:
    """Searches this brand's knowledge base (FAQ, safety, tariff) in English and returns the best
    matching articles. Answers about rates, tariffs, privacy and safety must come only from these
    articles. Before calling this tool, speak a brief conversational pacing phrase while it runs.

    Args:
        query: The customer's question in plain words (REQUIRED).
        namespace: Knowledge namespace of this brand: WGL_FAQ, WGL_SAFETY or WGL_TARIFF for
            Washington Gas (REQUIRED). WGL_SAFETY also searches the call-before-you-dig (811) article.

    Returns:
        dict with ok, found, articles (topic, text, status; best match first, at most three, or up to
        eight for an assistance-agency question, which honours the state in the query or of the
        identified account and leaves out closed programs) and agent_action; the article text is
        English and must be translated when speaking Spanish.
    """
    try:
        return _finish("knowledge_base_search", _knowledge_base_search_impl(query=query, namespace=namespace))
    except Exception as err:
        return _unexpected_error("knowledge_base_search", err)


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


def _tokens(text: str) -> set:
    """Lowercase alphanumeric words of a name or address."""
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


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


# Words that carry no meaning for a search; short meaningful words are kept (see _SHORT_KEEP).
_STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from get got had has have how i if in into is it its "
    "me my of on or our please so than that the their them then there these they this to up us was we were what when "
    "where which who why will with would you your am about any some tell know want need like just also not no yes "
    "ok okay hi hello thanks thank".split()
)


# Short words that are meaningful in this domain; they are never dropped as stopwords or for being short.
_SHORT_KEEP = frozenset(
    "811 gas fee dc md va amp ach wna pay due bill pin co off out hdd cap ssn tax app cc dig fix hot ac".split()
)


# A searched namespace also includes these articles of another namespace (a fragment matches the document id or the topic).
_NAMESPACE_ALIASES = {
    "WGL_SAFETY": {"WGL_FAQ": ("dig-safe", "811", "call before you dig")},
}


_JURISDICTION_WORDS = {
    "dc": "DC", "district of columbia": "DC", "washington dc": "DC",
    "md": "MD", "maryland": "MD",
    "va": "VA", "virginia": "VA",
}


_ASSISTANCE_RE = re.compile(
    r"assistance (?:program|agenc)|(?:financial|energy|bill|payment|utility|heating) assistance|agenc|liheap|waff|fuel fund|help (?:paying|pay) (?:my |the )?(?:bill|gas)|hardship|low[- ]income|bill relief|energy aid|financial help"
)


def _local_now() -> datetime:
    """Current time in the service territory (America/New_York) as a naive datetime; demo_ctx.demo_clock_override replaces it when set."""
    moment = datetime.now(timezone.utc)
    override = _object_var("demo_ctx").get("demo_clock_override")
    if override:
        try:
            moment = datetime.fromisoformat(str(override).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return moment
        except ValueError:
            print("demo_clock_override is not ISO 8601; the real clock is used")
            moment = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return moment.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        offset = -4 if 3 <= moment.month <= 10 else -5
        return (moment.astimezone(timezone.utc) + timedelta(hours=offset)).replace(tzinfo=None)


def _stem(word: str) -> str:
    """Cheap, symmetric stemming of plurals, -ing and -ed so 'rates' matches 'rate' and 'digging' matches 'dig'."""
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith(("ches", "shes", "xes", "zes")):
        return word[:-2]
    if word.endswith("ing") and len(word) > 5:
        word = word[:-3]
        if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouls":
            word = word[:-1]
        return word
    if word.endswith("ed") and len(word) > 4:
        word = word[:-2]
        if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouls":
            word = word[:-1]
        return word
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _search_terms(text: str) -> set:
    """Meaningful, stemmed search words: stopwords are dropped, short domain words (811, gas, fee, DC, MD, AMP) are kept."""
    terms = set()
    for token in re.findall(r"[a-z0-9]+", str(text).lower()):
        if token in _SHORT_KEEP:
            terms.add(token)
        elif token in _STOPWORDS or len(token) < 2:
            continue
        else:
            terms.add(_stem(token))
    return terms


def _jurisdictions_in(text: str) -> set:
    """Jurisdictions (DC, MD, VA) named in a piece of text, matched on whole words."""
    lowered = str(text).lower().replace("_", " ").replace("-", " ")
    return {code for word, code in _JURISDICTION_WORDS.items() if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lowered)}


def _query_jurisdiction(query: str) -> str:
    """The one jurisdiction named in the query, else the identified customer's own; '' when unknown."""
    named = _jurisdictions_in(query)
    if len(named) == 1:
        return next(iter(named))
    if named:
        return ""
    own = str(_object_var("identity_ctx").get("jurisdiction", "") or "").strip().upper()
    return own if own in ("DC", "MD", "VA") else ""


def _is_closed(item: dict) -> bool:
    """True when an article's own status, text or dates say its window or program has closed (checked against today)."""
    status = str(item.get("status", "") or "").lower()
    text = str(item.get("text_en", "") or "").lower()
    if any(word in status for word in ("closed", "expired", "ended")):
        return True
    if re.search(r"window (?:has |is |was )?closed|enrol?lment (?:is |has )?closed|no longer (?:accepting|available)", text):
        return True
    today = _local_now().strftime("%Y-%m-%d")
    for key in ("valid_through", "valid_until", "expires", "expires_at", "end_date", "window_end", "deadline", "closes"):
        value = str(item.get(key, "") or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and value < today:
            return True
    return False


def _candidate_articles(namespace: str) -> list:
    """Articles of the namespace plus the aliased ones of other namespaces (e.g. the 811 article for WGL_SAFETY), without duplicates."""
    articles = _run_query("wgl_kb_articles", {"namespace": namespace}, 300)
    seen = {item.get("_id") for item in articles}
    for other, fragments in _NAMESPACE_ALIASES.get(namespace, {}).items():
        for item in _run_query("wgl_kb_articles", {"namespace": other}, 300):
            label = f"{item.get('_id', '')} {item.get('topic', '')}".lower()
            if item.get("_id") not in seen and any(fragment in label for fragment in fragments):
                articles.append(item)
                seen.add(item.get("_id"))
    return articles


def _knowledge_base_search_impl(query: str, namespace: str) -> dict:
    if not query.strip():
        return _validation_error("the question to search for")
    if not namespace.startswith(f"{_tenant()}_"):
        return _rule_error("only this brand's knowledge base can be used", "search the matching namespace of this brand")
    articles = _candidate_articles(namespace)
    wanted = _search_terms(query)
    assistance = bool(_ASSISTANCE_RE.search(query.lower()))
    jurisdiction = _query_jurisdiction(query) if assistance else ""
    if assistance:
        # Programs of another state do not apply, and programs whose window has closed are not offered.
        articles = [
            item for item in articles
            if not _is_closed(item)
            and (not jurisdiction or not _jurisdictions_in(f"{item.get('_id', '')} {item.get('topic', '')}")
                 or jurisdiction in _jurisdictions_in(f"{item.get('_id', '')} {item.get('topic', '')}"))
        ]
        # The state itself is a filter, not a keyword.
        wanted = wanted - {"dc", "md", "va", "district", "columbia", "maryland", "virginia"}
        wanted = wanted | {"assistance", "help", "program", "agency"}
    doc_terms = []
    frequency = {}
    for item in articles:
        topic_terms = _search_terms(item.get("topic", ""))
        body_terms = _search_terms(item.get("text_en", ""))
        doc_terms.append((topic_terms, body_terms))
        for term in topic_terms | body_terms:
            frequency[term] = frequency.get(term, 0) + 1
    total = max(len(articles), 1)
    scored = []
    for item, (topic_terms, body_terms) in zip(articles, doc_terms):
        score = 0.0
        for term in wanted:
            weight = math.log(1.0 + total / frequency[term]) if term in frequency else 0.0
            if term in topic_terms:
                score += 3.0 * weight
            elif term in body_terms:
                score += weight
        if score > 0:
            # A focused title (most of its words asked for) beats a long one that merely mentions the word.
            score += 0.5 * len(wanted & topic_terms) / max(len(topic_terms), 1)
            if jurisdiction and jurisdiction in _jurisdictions_in(f"{item.get('_id', '')} {item.get('topic', '')}"):
                score += 0.5
            scored.append((score, item))
    # Deterministic order: best score first, then topic, then document id.
    scored.sort(key=lambda pair: (-round(pair[0], 6), str(pair[1].get("topic", "")).lower(), str(pair[1].get("_id", ""))))
    best = [item for _, item in scored[: 8 if assistance else 3]]
    if not best:
        return {
            "ok": True,
            "found": False,
            "agent_action": "No article matched. Say you do not have that information and offer a representative; "
                            "never answer from general knowledge.",
        }
    return {
        "ok": True,
        "found": True,
        "articles": [{"topic": item.get("topic", ""), "text": item.get("text_en", ""), "status": item.get("status", "")} for item in best],
        "agent_action": "Answer only from these articles, in the caller's language, translating the English text "
                        "when the caller speaks Spanish.",
    }
