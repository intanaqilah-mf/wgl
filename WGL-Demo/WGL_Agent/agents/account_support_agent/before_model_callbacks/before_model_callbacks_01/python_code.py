"""before_model_callback - all six agents (shared source).

Deterministic execution only; detection (emergency, handoff, language) stays in the instructions.
Order follows PRD s6: session opening, keypad-as-text, no-input, action triggers (emergency, live agent
handoff, SEMCO voice authentication exit, recording notice replay, return to origin), tool errors, then pending verbatim strings. The model never speaks a verbatim
string itself: the exact text is looked up by key and returned here.

PLATFORM GLOBALS (not imported): CallbackContext, LlmRequest, LlmResponse, Part, tools.
"""

import json
import re
from typing import Optional

# Demo setting: the call opens straight with the welcome. The recording notice is still spoken in full
# whenever the caller asks about recording. Set to True to speak it before the greeting (production).
_SPEAK_CONSENT_AT_START = False

_SESSION_START = "<event>session start</event>"
# The platform renders the chat widget's welcome event as the session start text above (verified against the live
# app); the explicit welcome forms are kept as a safety net. The plain words count only as the very first message.
_OPENING_TAGGED = (_SESSION_START, "<event>welcome</event>")
_OPENING_PLAIN = ("session start", "welcome")
_UNFILLED_PLACEHOLDER = re.compile(r"\{\w+\}")  # a verbatim text that still holds {name} is never spoken
_SILENCE_PATTERN = re.compile(r"<context>no user activity detected for \d+ seconds\.</context>", re.IGNORECASE)
_KEYPAD_PATTERN = re.compile(r"^\s*dtmf:\s*([0-9*#]+)\s*$", re.IGNORECASE)

# PRD s6 / BR-GU-007: the third silence and the third accumulated error each end in a live agent handoff.
_NO_INPUT_LIMIT = 3
_ERROR_LIMIT = 3

# Rev 22 (chat): after the customer is identified and verified the system speaks ONE short personal line. Text written in
# the same reply as an agent transfer is dropped by the platform, so the line travels in state (_welcome_line) and is
# spoken by the agent that takes over: joined to its next text reply (prefix), or as the whole reply when no task was
# pending (alone). The lines are WGL-scoped verbatim strings; a tenant without them (SEMCO) speaks nothing and keeps
# its current behaviour.
_PAYMENT_WORDS = re.compile(r"pay", re.IGNORECASE)  # the saved intent is free text or a label such as pay_bill
_NOT_A_PLAIN_PAYMENT = re.compile(r"arrangement|plan|extension|assist|help|hardship", re.IGNORECASE)

_NO_INPUT_PROMPT = {"EN": "Take your time - I'm still here.", "ES": "Tómese su tiempo, sigo aquí."}
# Used only when the verbatim lookup itself fails, so a safety or handoff turn is never silent.
_HANDOFF_FALLBACK = {"EN": "I'm connecting you with a representative now.", "ES": "Lo comunico con un representante ahora."}

# Handoff guard: a model that reaches for user_requested_agent when the caller never asked for a person (or a
# specialist-only topic) is routed back to the front desk instead of ending the session.
_PERSON_REQUEST = re.compile(
    r"\b(representative|rep|agent|human|person|people|someone|somebody|operator|supervisor|manager|advisor|associate|"
    r"specialist|customer service|talk to|speak (?:to|with)|connect me|transfer me|call me|"
    r"representante|agente|persona|alguien|operador|gerente|supervisor|hablar con|comun[ií]que)\b",
    re.IGNORECASE,
)
_SPECIALIST_TOPIC = re.compile(
    r"medical|life support|oxygen|ventilator|dialysis|death|died|passed away|estate|funeral|bereave|bankrupt|"
    r"name change|change (?:the |my )?name|regulat|commission|attorney general|"
    r"m[eé]dic|fallec|muri[oó]|quiebra|cambio de nombre|regulador",
    re.IGNORECASE,
)

_THIS_AGENT = "account_support_agent"  # this copy of the shared callback belongs to this agent
# PRD s10.5: spoken when SEMCO voice ends authentication after three failed attempts; it never promises a transfer.
_AUTH_EXIT_LINE = {
    "EN": "I'm sorry, I wasn't able to verify your details, so to protect your account I'm going to end this call now. "
          "You're welcome to call us back at any time, or sign in to your online account.",
    "ES": "Lo siento, no pude verificar sus datos, así que para proteger su cuenta voy a terminar esta llamada ahora. "
          "Puede volver a llamarnos cuando quiera o iniciar sesión en su cuenta en línea.",
}
_AUTH_EXIT_REASON = "auth_failure_exit"
# Used only if the approved text cannot be fetched; it carries the utility's own emergency line.
_EMERGENCY_FALLBACK = {
    "WGL": {
        "EN": "If you smell gas, please leave the building immediately. Do not use switches, appliances or phones inside. "
              "From a safe place outside, call 911, and then the Washington Gas emergency line, "
              "eight four four, nine two seven, four four two seven, option one.",
        "ES": "Si huele a gas, salga del edificio de inmediato. No use interruptores, aparatos ni teléfonos adentro. Desde un "
              "lugar seguro afuera, llame al 911 y luego a la línea de emergencias de Washington Gas, "
              "ocho cuatro cuatro, nueve dos siete, cuatro cuatro dos siete, opción uno.",
    },
    "SEMCO": {
        "EN": "If you smell gas, please leave the building immediately. Do not touch any switches or appliances. "
              "From a safe place outside, call the SEMCO Energy emergency line at 8 8 8, 4 2 7, 1 4 2 7.",
        "ES": "Si huele a gas, salga del edificio de inmediato. No toque interruptores ni aparatos. Desde un lugar "
              "seguro afuera, llame a la línea de emergencias de SEMCO Energy al 8 8 8, 4 2 7, 1 4 2 7.",
    },
}

# Spoken on chat when the visitor reports a gas smell again after the emergency card was already shown: the card is
# never repeated, and the visitor is left with the one thing that matters plus an open door.
_EMERGENCY_FOLLOWUP = {
    "WGL": {
        "EN": "Please stay outside, away from the building, and call 911 first, then Washington Gas at 844-927-4427, option 1. "
              "I'm still here whenever you are safe and need anything else.",
        "ES": "Por favor quédese afuera, lejos del edificio, y llame primero al 911 y luego a Washington Gas al 844-927-4427, opción 1. "
              "Sigo aquí cuando esté a salvo y necesite algo más.",
    },
    "SEMCO": {
        "EN": "Please stay outside, away from the building, and call the SEMCO Energy emergency line at 8 8 8, 4 2 7, 1 4 2 7. "
              "I'm still here whenever you are safe and need anything else.",
        "ES": "Por favor quédese afuera, lejos del edificio, y llame a la línea de emergencias de SEMCO Energy al 8 8 8, 4 2 7, 1 4 2 7. "
              "Sigo aquí cuando esté a salvo y necesite algo más.",
    },
}

# Page-aware chat opening (Washington Gas website), kept as the fallback for a client that still sends the welcome
# event. The website itself now renders the same greeting and chips (assets/js/assistant.js, WELCOME) before the
# visitor writes, so the first agent turn on chat is the tapped chip or typed text, not a session-start event.
# page_context is set by the website through the widget variables; each page has a verbatim greeting
# greeting_page_<page> and quick-reply chips, EN and ES. Every chip is something the agent can do today. The Spanish
# page always opens in Spanish.
_PAGE_CHIP_PROMPT = {"EN": "What would you like to do?", "ES": "¿Qué le gustaría hacer?"}
_PAGE_CHIPS = {
    "home": {
        "EN": ["Pay my bill", "Start or stop service", "Report a gas smell", "Check an outage", "Help with my payment"],
        "ES": ["Pagar mi factura", "Iniciar o detener el servicio", "Reportar olor a gas", "Consultar un corte de servicio", "Ayuda con mi pago"],
    },
    "billing": {
        "EN": ["Pay my bill", "Why is my bill high?", "Set up AutoPay", "Payment plans and help"],
        "ES": ["Pagar mi factura", "¿Por qué mi factura es alta?", "Configurar AutoPay", "Planes de pago y ayuda"],
    },
    "service": {
        "EN": ["Start service", "Stop service", "I am moving", "Is gas available at my address"],
        "ES": ["Iniciar servicio", "Detener servicio", "Me estoy mudando", "¿Hay gas disponible en mi dirección?"],
    },
    "safety": {
        "EN": ["Report a gas smell", "My gas is off", "Carbon monoxide alarm", "Call before you dig (811)"],
        "ES": ["Reportar olor a gas", "Mi gas está apagado", "Alarma de monóxido de carbono", "Llame antes de excavar (811)"],
    },
    "assistance": {
        "EN": ["Help paying my bill", "Payment plans", "Energy assistance programs", "Medical or special needs"],
        "ES": ["Ayuda para pagar mi factura", "Planes de pago", "Programas de asistencia de energía", "Necesidades médicas o especiales"],
    },
    "savings": {
        "EN": ["Rebates and how to claim", "Home energy assessment", "Budget Billing"],
        "ES": ["Reembolsos y cómo solicitarlos", "Evaluación de energía del hogar", "Facturación presupuestada"],
    },
    "business": {
        "EN": ["Start service for my business", "Pay my bill", "Added load or split meter", "Service abandonment"],
        "ES": ["Iniciar servicio para mi negocio", "Pagar mi factura", "Carga adicional o medidor dividido", "Abandono de servicio"],
    },
    "choice": {
        "EN": ["How Customer Choice works", "Pay my bill", "Start or stop service"],
        "ES": ["Cómo funciona Customer Choice", "Pagar mi factura", "Iniciar o detener el servicio"],
    },
    "claims": {
        "EN": ["Report damage", "Safety information", "Talk to a person"],
        "ES": ["Reportar daños", "Información de seguridad", "Hablar con una persona"],
    },
    "energy": {
        "EN": ["Home energy assessment", "Budget Billing", "Rebates"],
        "ES": ["Evaluación de energía del hogar", "Facturación presupuestada", "Reembolsos"],
    },
    "contact": {
        "EN": ["Pay my bill", "Report a gas smell", "Talk to a person"],
        "ES": ["Pagar mi factura", "Reportar olor a gas", "Hablar con una persona"],
    },
    "spanish": {
        "EN": ["Pay my bill", "Start or stop service", "Report a gas smell", "Check an outage", "Help with my payment"],
        "ES": ["Pagar mi factura", "Iniciar o detener el servicio", "Reportar olor a gas", "Consultar un corte de servicio", "Ayuda con mi pago"],
    },
}


def _emergency_fallback(state: dict) -> str:
    """The fallback wording for this utility and language; Washington Gas wording when the utility is unknown."""
    by_tenant = _EMERGENCY_FALLBACK.get(str(state.get("tenant_id", "")), _EMERGENCY_FALLBACK["WGL"])
    return by_tenant[_language(state)]


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


def _language(state: dict) -> str:
    return "ES" if str(state.get("language", "EN")).upper() == "ES" else "EN"


def _tenant(state: dict) -> str:
    return str(state.get("tenant_id", ""))


def _user_text(callback_context: CallbackContext) -> str:
    """The caller's latest input as text (a transcript on voice)."""
    texts = [part.text_or_transcript() for part in callback_context.get_last_user_input()]
    return "".join(text for text in texts if text)


def _is_silence(contents: list) -> bool:
    """True when the latest user message is the platform's no-activity signal."""
    return len(contents) > 1 and any(
        _SILENCE_PATTERN.search(part.text) for part in contents[-1].parts if part.text
    )


def _verbatim_text(state: dict, key: str, params: str = "", language: str = "") -> str:
    """Looks up the exact text of a verbatim string (in the session language unless one is given); the tool's pending marker is cleared again."""
    state["_verbatim_pending"] = ""
    result = _plain(tools.get_verbatim_string({"key": key, "language": language or _language(state), "params": params, "deliver": "callback"}))
    state["_verbatim_pending"] = ""
    text = result.get("text", "") if isinstance(result, dict) and result.get("ok") else ""
    if _UNFILLED_PLACEHOLDER.search(text):
        print(f"verbatim text for {key} still holds an unfilled placeholder; it is not spoken")
        return ""
    return text


def _saved_task(state: dict) -> dict:
    """The saved task object (a dict, or JSON text); empty when there is none."""
    value = state.get("saved_task_state")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _welcome_line(state: dict, task_pending: bool) -> str:
    """The one personal line for a chat customer who is identified (and verified); empty on voice, when already spoken or when the tenant has none."""
    if str(state.get("channel", "")) != "chat" or str(state.get("_welcome_line_mode", "")) == "done":
        return ""
    if str(state.get("auth_status", "Guest")) not in ("Identified", "Authenticated"):
        return ""
    intent = str(_saved_task(state).get("pending_intent", ""))
    if not task_pending:
        key = "welcome_verified_idle"
    elif _PAYMENT_WORDS.search(intent) and not _NOT_A_PLAIN_PAYMENT.search(intent):
        key = "welcome_verified_payment"
    else:
        key = "welcome_verified_task"
    return _verbatim_text(state, key)


def _take_welcome_line(state: dict, mode: str) -> str:
    """Returns the waiting personal line when it is waiting in this mode and clears it (never spoken twice)."""
    line = str(state.get("_welcome_line", ""))
    if not line or str(state.get("_welcome_line_mode", "")) != mode:
        return ""
    state["_welcome_line"] = ""
    state["_welcome_line_mode"] = "done"
    return line


def _is_opening(user_text: str, contents: list) -> bool:
    """True for the session start event and the chat widget's welcome event (any case); the plain words only as the first message."""
    cleaned = user_text.strip().lower()
    if cleaned in _OPENING_TAGGED:
        return True
    return cleaned in _OPENING_PLAIN and len(contents) <= 1


def _page_key(state: dict) -> str:
    """The website page the chat was opened from; empty on voice, for a known caller or for an unknown value."""
    if str(state.get("channel", "")) != "chat" or str(state.get("auth_status", "Guest")) in ("Identified", "Authenticated"):
        return ""
    page = str(state.get("page_context", "") or "").strip().lower()
    return page if page in _PAGE_CHIPS else ""


def _widget_call(tool: str, card: dict, summary: str) -> object:
    """A widget (CUSTOM) tool call in the shape the platform requires: args.payload holds the custom element
    name and its card data, plus a one-line summary. The platform delivers it to the web widget as the rich
    element {type: custom_template, name: <element>, payload: <card>} (verified live)."""
    element = "cx-" + tool.replace("_", "-")
    return Part.from_function_call(name=tool, args={"payload": {"name": element, "payload": card}, "summary": summary})


def _page_chips(state: dict, page: str) -> object:
    """The quick-reply chip widget call for the page, in the language of the opening."""
    language = "ES" if page == "spanish" else _language(state)
    return _widget_call(
        "show_quick_actions", {"prompt": _PAGE_CHIP_PROMPT[language], "options": list(_PAGE_CHIPS[page][language])}, "Quick answers for this page"
    )


def _opening_response(state: dict) -> Optional[LlmResponse]:
    """Session start: consent (voice only), then the greeting verbatim; on chat from a known website page, the page greeting and chips."""
    spoken = []
    if _SPEAK_CONSENT_AT_START and str(state.get("channel", "")) == "voice":
        spoken.append(_verbatim_text(state, "quality_monitoring_consent"))
    page = _page_key(state)
    page_text = _verbatim_text(state, f"greeting_page_{page}", language="ES" if page == "spanish" else "") if page else ""
    if page_text:
        spoken.append(page_text)
    else:
        known = str(state.get("auth_status", "Guest")) in ("Identified", "Authenticated")
        spoken.append(_verbatim_text(state, "greeting_main_identified" if known else "greeting_main_guest"))
    text = " ".join(item for item in spoken if item)
    if not text:
        return None
    parts = [Part.from_text(text=text)]
    if page_text:
        parts.append(_page_chips(state, page))
    return LlmResponse.from_parts(parts=parts)


def _track_keypad(state: dict, user_text: str) -> None:
    """Keypad entry arrives as text 'dtmf: <digits>'; keep it for this turn only and never echo it."""
    match = _KEYPAD_PATTERN.match(user_text)
    state["dtmf_digits"] = match.group(1) if match else ""
    if user_text:
        state["utterance"] = "keypad entry" if match else user_text


def _end_with(text: str, widget: Optional[object] = None, escalated: bool = True) -> LlmResponse:
    """Spoken text, an optional widget call, then end_session with the escalation outcome."""
    parts = [Part.from_text(text=text)]
    if widget is not None:
        parts.append(widget)
    parts.append(Part.from_function_call(name="end_session", args={"session_escalated": escalated}))
    return LlmResponse.from_parts(parts=parts)


def _handoff_response(state: dict, reason: str, queue: str, summary: str) -> LlmResponse:
    """Records the simulated handoff, speaks the verbatim handoff text and ends the session."""
    tools.execute_live_agent_handoff({"reason": reason, "context": summary, "queue": queue, "tenant": _tenant(state)})
    text = _verbatim_text(state, "live_agent_handoff") or _HANDOFF_FALLBACK[_language(state)]
    return _end_with(text)


def _auth_exit_response(state: dict) -> LlmResponse:
    """SEMCO voice lockout: records the exit, speaks a warm closing line and ends the call with no transfer."""
    tools.execute_live_agent_handoff({"reason": _AUTH_EXIT_REASON, "context": "Three failed verification attempts on voice; the call was ended without a transfer.", "queue": str(state.get("_escalation_topic", "")) or "billing", "tenant": _tenant(state)})
    return _end_with(_AUTH_EXIT_LINE[_language(state)], escalated=False)


def _consent_response(state: dict) -> Optional[LlmResponse]:
    """BR-GU-001: the recording notice is re-emitted verbatim when the caller asks or interrupted it."""
    text = _verbatim_text(state, "quality_monitoring_consent")
    return LlmResponse.from_parts(parts=[Part.from_text(text=text)]) if text else None


def _emergency_response(state: dict) -> LlmResponse:
    """Verbatim safety instruction, the emergency card on chat, the handoff record, then end_session (voice only)."""
    chat = str(state.get("channel", "")) == "chat"
    if chat and str(state.get("_emergency_card_shown", "false")).lower() == "true":
        # Already shown: never repeat the card or the handoff record; leave the visitor safe and the conversation open.
        state["emergency_detected"] = "false"
        by_tenant = _EMERGENCY_FOLLOWUP.get(_tenant(state), _EMERGENCY_FOLLOWUP["WGL"])
        return LlmResponse.from_parts(parts=[Part.from_text(text=by_tenant[_language(state)])])
    handoff = _plain(tools.execute_live_agent_handoff({"reason": "emergency", "context": "Caller reported a gas emergency.", "queue": "emergency", "tenant": _tenant(state)}))
    text = _verbatim_text(state, "emergency_safety_instruction") or _emergency_fallback(state)
    widget = None
    if chat:
        number = handoff.get("emergency_number", "") if isinstance(handoff, dict) else ""
        widget = _widget_call("show_emergency_card", {"safety_text": text, "emergency_number": number}, "Gas emergency safety instructions")
        # Verified live: a widget call that shares a response with end_session loses its card (the session ends
        # before the card is delivered), so on chat the turn ends with the card and the session stays open. The
        # emergency flag is cleared and the card marked as shown, so the after_model recovery never re-triggers it.
        state["_emergency_card_shown"] = "true"
        state["emergency_detected"] = "false"
        return LlmResponse.from_parts(parts=[Part.from_text(text=text), widget])
    return _end_with(text)


def _settle_after_emergency(state: dict) -> None:
    """The chat emergency leaves the handoff record's _escalation_reason behind (the tool's state write lands after this
    callback, so it cannot be cleared in the same turn). The after_model recovery would turn the next ordinary reply
    into a live agent handoff, so it is cleared at the start of the next turn (verified live)."""
    if (str(state.get("channel", "")) == "chat" and str(state.get("_emergency_card_shown", "false")).lower() == "true"
            and str(state.get("_escalation_reason", "")) == "emergency" and not state.get("_action_trigger")):
        state["_escalation_reason"] = ""
        state["_escalation_topic"] = ""


def _caller_asked_for_person(callback_context: CallbackContext) -> bool:
    """True when one of the caller's last three messages asks for a person or raises a specialist-only topic.

    Fails open (True) when the conversation cannot be read, so a real request is never blocked.
    """
    try:
        texts = []
        for event in reversed(callback_context.events):
            if event.is_user():
                texts.append(" ".join(part.text_or_transcript() or "" for part in event.parts()))
                if len(texts) == 3:
                    break
        joined = " ".join(texts)
    except Exception as err:
        print(f"handoff guard could not read the conversation: {err}")
        return True
    return bool(_PERSON_REQUEST.search(joined) or _SPECIALIST_TOPIC.search(joined))


def _front_desk_response() -> LlmResponse:
    """Sends the caller back to the front desk (root_agent), silently."""
    return LlmResponse.from_parts(parts=[Part.from_function_call(name="transfer_to_agent", args={"agent": "root_agent"})])


def _trigger_response(state: dict, trigger: str, callback_context: CallbackContext) -> Optional[LlmResponse]:
    """Executes the action the instruction asked for through set_session_state."""
    if trigger == "emergency":
        return _emergency_response(state)
    if trigger == "replay_consent":
        return _consent_response(state)
    if trigger == "live_agent_handoff":
        reason = str(state.get("_escalation_reason", "")) or "user_requested_agent"
        if reason == "user_requested_agent" and not _caller_asked_for_person(callback_context):
            print(f"{_THIS_AGENT}: handoff blocked, the caller did not ask for a person; routing to the front desk")
            state["_escalation_reason"] = ""
            state["_escalation_topic"] = ""
            return None if _THIS_AGENT == "root_agent" else _front_desk_response()
        if reason == _AUTH_EXIT_REASON and _tenant(state) == "SEMCO" and str(state.get("channel", "")) == "voice":
            return _auth_exit_response(state)
        queue = str(state.get("_escalation_topic", "")) or "billing"
        return _handoff_response(state, reason, queue, "Customer needs a representative.")
    if trigger == "return_to_origin":
        target = str(state.get("_return_to_agent", ""))
        state["_return_to_agent"] = ""
        line = _welcome_line(state, task_pending=bool(target))
        if line:
            state["_welcome_line"] = line
            state["_welcome_line_mode"] = "prefix" if target else "alone"
        # Always go back, with or without a saved task or a welcome line: an identified caller who asked
        # something else must reach the front desk, not stay with the agent that only identifies.
        destination = target or "root_agent"
        if destination != _THIS_AGENT:
            return LlmResponse.from_parts(
                parts=[Part.from_function_call(name="transfer_to_agent", args={"agent": destination})]
            )
    return None


def _no_input_response(state: dict) -> LlmResponse:
    """Strikes 1 and 2 reassure the caller; strike 3 hands off (no_input_escalation)."""
    strikes = int(state.get("local_noinput_counter") or 0) + 1
    state["local_noinput_counter"] = str(strikes)
    if strikes >= _NO_INPUT_LIMIT:
        queue = str(state.get("_escalation_topic", "")) or "billing"
        return _handoff_response(state, "no_input_escalation", queue, "Caller stayed silent three times.")
    return LlmResponse.from_parts(parts=[Part.from_text(text=_NO_INPUT_PROMPT[_language(state)])])


def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    state = callback_context.state
    user_text = _user_text(callback_context)
    if _is_opening(user_text, llm_request.contents):
        return _opening_response(state)
    if _is_silence(llm_request.contents):
        return _no_input_response(state)
    _track_keypad(state, user_text)
    state["local_noinput_counter"] = "0"

    _settle_after_emergency(state)
    trigger = str(state.get("_action_trigger", ""))
    if trigger:
        state["_action_trigger"] = ""
        response = _trigger_response(state, trigger, callback_context)
        if response is not None:
            return response

    if str(state.get("api_failed", "false")).lower() == "true":
        state["api_failed"] = "false"
        errors = int(state.get("global_err_count") or 0) + 1
        state["global_err_count"] = str(errors)
        if errors >= _ERROR_LIMIT:
            queue = str(state.get("_escalation_topic", "")) or "billing"
            return _handoff_response(state, "system_unavailable", queue, "Repeated system errors.")

    alone = _take_welcome_line(state, "alone")
    if alone:
        return LlmResponse.from_parts(parts=[Part.from_text(text=alone)])

    pending = str(state.get("_verbatim_pending", ""))
    if pending:
        state["_verbatim_pending"] = ""
        key, _, params = pending.partition("|")
        text = _verbatim_text(state, key, params)
        if text:
            line = _take_welcome_line(state, "prefix")
            return LlmResponse.from_parts(parts=[Part.from_text(text=f"{line} {text}" if line else text)])
    return None
