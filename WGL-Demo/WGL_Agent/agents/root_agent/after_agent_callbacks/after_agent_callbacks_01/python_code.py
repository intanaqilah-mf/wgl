"""after_agent_callback - root agent.

Close-out: when the agent's last turn ended the session, writes the session summary (language,
authentication path, route and handoff reason) to the session store. The write is idempotent, so a repeat
only updates the same record. No verification digits are ever part of the summary.

PLATFORM GLOBALS (not imported): CallbackContext, Content, tools.
"""

import json
from typing import Optional


def _session_ended(callback_context: CallbackContext) -> bool:
    """True when the most recent agent event contains an end_session call."""
    for event in reversed(callback_context.events):
        if event.is_agent():
            return any(part.has_function_call("end_session") for part in event.parts())
    return False


def after_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
    if not _session_ended(callback_context):
        return None
    state = callback_context.state
    summary = {
        "language": state.get("language", ""),
        "channel": state.get("channel", ""),
        "route": state.get("route", ""),
        "auth_status": state.get("auth_status", ""),
        "auth_method_tier1": state.get("auth_method_tier1", ""),
        "auth_method_tier2": state.get("auth_method_tier2", ""),
        "escalation_reason": state.get("_escalation_reason", ""),
        "escalation_topic": state.get("_escalation_topic", ""),
    }
    try:
        tools.session_store_write({"session_context": json.dumps(summary)})
    except Exception as err:
        print(f"session close-out write failed: {err}")
    return None
