"""before_agent_callback - root agent.

Runs once per session, before the agent has said anything (the callback fires on every turn, so it
returns early once any agent event exists). It bridges the platform variable telephony-caller-id into
plain variables, derives the channel, applies the language the website visitor picked (ui_language), loads the
tenant configuration, silently looks up the caller by phone number and checks for an outage.

On chat the website shows the welcome and the page chips itself, so this setup now runs before the first model
call of the tapped chip or typed message (no session-start event is needed). Voice is unchanged.

PLATFORM GLOBALS (not imported): CallbackContext, Content, tools.
"""

import time
from typing import Optional

# BR-GU-047: an account lookup slower than this many seconds puts the app in degraded mode.
_CIS_TIMEOUT_SECONDS = 3.0


def _e164(raw: str) -> str:
    """Normalizes a caller id (dashes, bare digits or +1) to +1XXXXXXXXXX; empty when not a US number."""
    digits = "".join(char for char in str(raw) if char.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return ""


def _detect_channel(session: dict, caller_id: str, current: str) -> str:
    """voice or chat from the predefined _session variable, falling back to caller id presence."""
    if isinstance(session, dict):
        if session.get("modality") == "VOICE" or session.get("channel") == "GOOGLE_TELEPHONY_PLATFORM":
            return "voice"
        if session.get("modality") == "TEXT":
            return "chat"
    return "voice" if caller_id else current


def _plain(result):
    """The tool's own dict: unwraps the platform response object and any 'result' wrapper key."""
    if result is not None and not isinstance(result, (dict, list)) and hasattr(result, "json"):
        try:
            result = result.json()
        except Exception:
            return {}
    if isinstance(result, dict) and isinstance(result.get("result"), dict) and "ok" not in result:
        return result["result"]
    return result


def _already_started(callback_context: CallbackContext) -> bool:
    """True once the agent has spoken, so the session setup never runs twice."""
    return any(event.is_agent() for event in callback_context.events)


def _apply_ui_language(state: dict) -> None:
    """The website's English | Spanish toggle (variable ui_language, en or es) sets the conversation language for the first turn."""
    chosen = str(state.get("ui_language", "") or "").strip().lower()
    if chosen in ("en", "es"):
        state["language"] = chosen.upper()


def _open_session(state: dict, tenant: str) -> None:
    """Loads configuration, identifies the caller silently and checks for an outage."""
    tools.get_tenant_config({"tenant": tenant})
    if not state["ani"]:
        return
    started = time.time()
    lookup = _plain(tools.fetch_account_by_ani({"ani": state["ani"], "tenant": tenant}))
    if time.time() - started > _CIS_TIMEOUT_SECONDS:
        state["degraded_mode"] = "true"
    zip_code = lookup.get("service_address_zip", "") if isinstance(lookup, dict) else ""
    if zip_code:
        tools.check_outage_status({"zip": zip_code, "tenant": tenant})


def before_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    state = callback_context.state
    if _already_started(callback_context):
        return None
    session = callback_context.get_variable("_session", {})
    caller_id = str(state.get("telephony-caller-id", "") or "")
    print(f"session start: _session={session!r} telephony-caller-id={caller_id!r}")
    state["telephony_caller_id"] = caller_id
    state["ani"] = _e164(caller_id)
    state["channel"] = _detect_channel(session, caller_id, str(state.get("channel", "voice")))
    _apply_ui_language(state)
    try:
        _open_session(state, str(state.get("tenant_id", "")))
    except Exception as err:
        print(f"session setup failed: {err}")
        state["api_failed"] = "true"
    return None
