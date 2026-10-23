"""after_model_callback - all six agents (shared source).

Keeps every turn well formed: a spoken line before end_session, a re-ask instead of an empty turn, and
trigger recovery when the model flagged an emergency or handoff in state but forgot the trigger. The
full-account-number safety net is the gr_no_account_number guardrail, not this callback.
PLATFORM GLOBALS (not imported): CallbackContext, LlmResponse, Part.
"""

import json
import re
from typing import Optional

_FAREWELL = {"EN": "Thank you for contacting us. Take care.", "ES": "Gracias por comunicarse con nosotros. Cuídese mucho."}
_THIS_AGENT = "billing_payments_agent"  # this copy of the shared callback belongs to this agent
_REASK = {"EN": "Sorry, I missed that. Could you say it once more?", "ES": "Disculpe, no lo escuché bien. ¿Podría repetirlo?"}


# Last line of defence: an agent text that still holds an unfilled {name} placeholder is never shown. Real speech has no
# curly braces around a plain word, so ordinary text is never touched; function calls and other parts are kept as they are.
_UNFILLED_PLACEHOLDER = re.compile(r"\{[A-Za-z_]\w*\}")
_SAFE_LINE = {
    "EN": "I'm sorry, I can't give you that detail right now. Is there anything else I can help you with?",
    "ES": "Lo siento, no puedo darle ese detalle ahora mismo. \u00bfHay algo m\u00e1s en lo que pueda ayudarle?",
}


def _language(state: dict) -> str:
    return "ES" if str(state.get("language", "EN")).upper() == "ES" else "EN"


def _has_text(parts: list) -> bool:
    return any((part.text_or_transcript() or "").strip() for part in parts)


def _agent_already_spoke(callback_context: CallbackContext) -> bool:
    """True when an earlier model call of this same turn already produced text."""
    for event in reversed(callback_context.events):
        if event.is_user():
            return False
        if event.is_agent() and _has_text(event.parts()):
            return True
    return False


def _prefix_welcome_line(state: dict, parts: list) -> Optional[list]:
    """Rev 22: joins the waiting personal line (chat, after verification) to the first text of this reply, once.

    Text beside a transfer or end_session is never delivered, so such a reply keeps the line waiting for the next one.
    """
    line = str(state.get("_welcome_line", ""))
    if not line or str(state.get("_welcome_line_mode", "")) != "prefix":
        return None
    if any(part.has_function_call("transfer_to_agent") or part.has_function_call("end_session") for part in parts):
        return None
    for index, part in enumerate(parts):
        if part.text and part.text.strip():
            state["_welcome_line"] = ""
            state["_welcome_line_mode"] = "done"
            joined = list(parts)
            joined[index] = Part.from_text(text=f"{line} {part.text}")
            return joined
    # A card (widget) call carries the reply text in its textResponse argument and no text part: the line joins that text.
    for index, part in enumerate(parts):
        call = part.function_call
        args = dict(call.args or {}) if call is not None and str(call.name or "").startswith("show_") else {}
        if isinstance(args.get("textResponse"), str) and args["textResponse"].strip():
            args["textResponse"] = f"{line} {args['textResponse']}"
            state["_welcome_line"] = ""
            state["_welcome_line_mode"] = "done"
            joined = list(parts)
            joined[index] = Part.from_function_call(name=call.name, args=args)
            return joined
    return None


def _save_request_before_auth(state: dict, parts: list) -> None:
    """A transfer to auth_agent with no saved request (the model skipped set_session_state) would lose what the
    caller asked for; the words they just said are saved so the task resumes once they are identified."""
    if str(_saved_flag(state)).lower() == "true":
        return
    sends_to_auth = any(
        part.function_call is not None
        and str(part.function_call.name or "") == "transfer_to_agent"
        and "auth_agent" in str(part.function_call.args or "")
        for part in parts
    )
    if not sends_to_auth or any(part.has_function_call("set_session_state") for part in parts):
        return
    utterance = str(state.get("utterance", "")).strip()
    if not utterance:
        return
    try:
        tools.set_session_state({"pending_intent": utterance[:200], "_return_to_agent": _THIS_AGENT})
    except Exception as err:
        print(f"could not save the request before identification: {err}")


def _saved_flag(state: dict) -> str:
    """The saved flag of saved_task_state (a dict or JSON text); empty when nothing is saved."""
    value = state.get("saved_task_state")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ""
    return str(value.get("saved", "")) if isinstance(value, dict) else ""


def _recover_trigger(state: dict, parts: list) -> None:
    """Sets the trigger for the next model call when state says emergency or handoff but no trigger is set."""
    if state.get("_action_trigger"):
        return
    if any(part.has_function_call("set_session_state") or part.has_function_call("end_session") for part in parts):
        return
    if str(state.get("emergency_detected", "false")).lower() == "true":
        state["_action_trigger"] = "emergency"
    elif state.get("_escalation_reason"):
        state["_action_trigger"] = "live_agent_handoff"

# Rev 23 voice safety net. Chat replies may carry emoji, markdown bullets, **bold** and [label](link) (chat_screen_format); a voice reply
# never should, because speech synthesis would read the marks aloud. This runs ONLY when the channel is voice, only on plain text
# parts, and only removes: emoji characters, the leading "- " or bullet dot of a line, ** markers around bold words, and the
# address of a markdown link (the label stays). Words, digits, dollar signs, hyphens inside words and every function call are kept.
_VOICE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF\u2600-\u27BF\u2B50\u2B55\uFE0F\u200D]")
_VOICE_BULLET = re.compile(r"(?m)^[ \t]*[-*\u2022][ \t]+(?=\S)")
_VOICE_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_VOICE_LINK = re.compile(r"\[([^\]\n]+)\]\((?:https?://|mailto:|tel:)[^)\s]*\)")


def _voice_clean(text: str) -> str:
    """The text without emoji, line bullets, bold markers and link addresses; ordinary text comes back unchanged."""
    cleaned = _VOICE_LINK.sub(r"\1", text)
    cleaned = _VOICE_BOLD.sub(r"\1", cleaned)
    cleaned = _VOICE_BULLET.sub("", cleaned)
    cleaned = _VOICE_EMOJI.sub("", cleaned)
    if cleaned != text:
        cleaned = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", re.sub(r"[ \t]{2,}", " ", cleaned)).strip()
    return cleaned


def _strip_voice_formatting(state: dict, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """On voice only: the same response with emoji and markdown marks removed from its text parts; None when nothing changed."""
    if str(state.get("channel", "")) != "voice":
        return None
    parts = list(llm_response.content.parts)
    changed = False
    for index, part in enumerate(parts):
        if part.text and part.function_call is None:
            cleaned = _voice_clean(part.text)
            if cleaned != part.text and cleaned:
                parts[index] = Part.from_text(text=cleaned)
                changed = True
    return LlmResponse.from_parts(parts=parts) if changed else None


# A reply that carries a <context>...</context> block is the model copying the transcript's own notation for a
# tool call as text. A set_session_state call written that way is turned into the real call (the follow-up text
# next to it assumed the call had happened and is dropped); any other such block is removed from the text.
_LEAKED_CONTEXT = re.compile(r"<context>.*?</context>", re.DOTALL | re.IGNORECASE)
_LEAKED_CALL = re.compile(r"called tool\s*`?(\w+)`?\s*with parameters:\s*(\{.*\})", re.DOTALL | re.IGNORECASE)


def _clean_leaked_context(parts: list) -> Optional[list]:
    """The parts with leaked context blocks recovered or removed; None when no part holds one."""
    cleaned, changed = [], False
    for part in parts:
        blocks = _LEAKED_CONTEXT.findall(part.text) if part.text and part.function_call is None else []
        if not blocks:
            cleaned.append(part)
            continue
        changed = True
        recovered = False
        for block in blocks:
            match = _LEAKED_CALL.search(block)
            if match and match.group(1) == "set_session_state":
                try:
                    args = json.loads(match.group(2))
                except ValueError:
                    args = None
                if isinstance(args, dict) and args:
                    cleaned.append(Part.from_function_call(name="set_session_state", args=args))
                    recovered = True
        remaining = "" if recovered else _LEAKED_CONTEXT.sub("", part.text).strip()
        if remaining:
            cleaned.append(Part.from_text(text=remaining))
    return cleaned if changed else None


def _after_model(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    state = callback_context.state
    parts = list(llm_response.content.parts)
    leak_fixed = _clean_leaked_context(parts)
    if leak_fixed is not None:
        print(f"{_THIS_AGENT}: a <context> block in the reply was recovered or removed")
        return LlmResponse.from_parts(parts=leak_fixed or [Part.from_text(text=_REASK[_language(state)])])
    _recover_trigger(state, parts)
    _save_request_before_auth(state, parts)

    unfilled = [index for index, part in enumerate(parts) if part.text and _UNFILLED_PLACEHOLDER.search(part.text)]
    if unfilled:
        print(f"agent text held an unfilled placeholder and was replaced ({len(unfilled)} part(s))")
        kept = [part for index, part in enumerate(parts) if index not in unfilled]
        return LlmResponse.from_parts(parts=[Part.from_text(text=_SAFE_LINE[_language(state)])] + kept)

    joined = _prefix_welcome_line(state, parts)
    if joined is not None:
        return LlmResponse.from_parts(parts=joined)

    ends_session = any(part.has_function_call("end_session") for part in parts)
    if ends_session and not _has_text(parts) and not _agent_already_spoke(callback_context):
        return LlmResponse.from_parts(parts=[Part.from_text(text=_FAREWELL[_language(state)])] + parts)

    if not _has_text(parts) and not any(part.function_call is not None for part in parts):
        return LlmResponse.from_parts(parts=[Part.from_text(text=_REASK[_language(state)])])
    return None


def after_model_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    """Keeps the turn well formed (see _after_model); on voice first removes any emoji or markdown that slipped into the text."""
    stripped = _strip_voice_formatting(callback_context.state, llm_response)
    result = _after_model(callback_context, stripped if stripped is not None else llm_response)
    return result if result is not None else stripped
