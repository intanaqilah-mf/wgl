"""Code-callback guardrail gr_no_account_number (after the model): a full account number never appears in output.

BR-GU-021: only the last 4 digits of an account number may be spoken or shown. Narrow on purpose: phone
numbers, dollar amounts, ZIP codes, dates and short confirmation numbers pass. Triggers on grouped account numbers, on very
long digit runs and on a bare ten-digit number that is this customer's own account number: one that ends with the last four
digits of the identified account (identity_ctx.account_number_last4) and is not the caller's own phone number, or one that
equals an account number the customer keyed in or a tool returned in this session.

PLATFORM GLOBALS (not imported): CallbackContext, LlmResponse.
"""

import json
import re
from dataclasses import dataclass

# Grouped account numbers such as 1234-5678-9012.
_GROUPED_ACCOUNT = re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}\b")
# Phone numbers are allowed: optional +1, then three, three and four digits with separators.
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
# Eleven or more consecutive digits; eleven digits that start with 1 are a phone number with country code.
_LONG_RUN = re.compile(r"\d{11,}")
# A bare run of exactly ten digits (the seeded account numbers have exactly ten digits).
_TEN_RUN = re.compile(r"(?<![\w.])\d{10}(?![\w])")
# An account number a tool returned: account_number, "account number" or account_num followed by ten digits.
_ACCOUNT_FIELD = re.compile(r"account[_ ]?(?:number|num|no)\W{1,6}(\d{10})(?!\d)", re.IGNORECASE)


@dataclass
class GuardrailDecision:
    """Result the platform converts with dataclasses.asdict: decision is OK or TRIGGER."""

    decision: str
    reason: str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


def _ten_digits(text: str) -> str:
    """The last ten digits of a phone-like value, or an empty string when it holds fewer."""
    digits = re.sub(r"\D", "", str(text or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def _looks_like_account_number(text: str, known: frozenset = frozenset(), last4: str = "", own_phones: frozenset = frozenset()) -> bool:
    """True when the text holds a full account number.

    known: ten-digit account numbers seen in this session; last4: last four digits of the identified account;
    own_phones: ten-digit phone numbers of the caller, which are never treated as the account number.
    """
    without_phones = _PHONE.sub(" ", text)
    if _GROUPED_ACCOUNT.search(without_phones):
        return True
    if any(len(run) != 11 or not run.startswith("1") for run in _LONG_RUN.findall(without_phones)):
        return True
    for run in _TEN_RUN.findall(without_phones):
        if run in known:
            return True
        if len(last4) == 4 and run.endswith(last4) and run not in own_phones:
            return True
    # A known account number written like a phone number (301-555-0105) is still an account number.
    return any(_ten_digits(match.group(0)) in known for match in _PHONE.finditer(text))


def _identity(state) -> dict:
    """identity_ctx as a dict (it may be stored as a dict or as JSON text); empty when it is not available."""
    try:
        value = state.get("identity_ctx")
        if isinstance(value, str) and value.strip():
            value = json.loads(value)
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _session_numbers(callback_context) -> frozenset:
    """Ten-digit account numbers in this session: the ones keyed into fetch_account_by_number and any a tool result carried."""
    found = set()
    try:
        for event in callback_context.events:
            for part in event.parts():
                call = getattr(part, "function_call", None)
                if call is not None and str(getattr(call, "name", "") or "") == "fetch_account_by_number":
                    args = getattr(call, "args", None)
                    digits = re.sub(r"\D", "", str(args.get("account_num", "")) if hasattr(args, "get") else str(args or ""))
                    if len(digits) == 10:
                        found.add(digits)
                response = getattr(part, "function_response", None)
                if response is not None:
                    found.update(_ACCOUNT_FIELD.findall(str(response)))
    except Exception as err:
        print(f"could not read the session numbers: {err}")
    return frozenset(found)


def after_model_guardrail_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> GuardrailDecision:
    text = " ".join((part.text_or_transcript() or "") for part in llm_response.content.parts)
    known, last4, own_phones = frozenset(), "", frozenset()
    try:
        state = callback_context.state
        last4 = re.sub(r"\D", "", str(_identity(state).get("account_number_last4", "") or ""))
        own_phones = frozenset(item for item in (_ten_digits(state.get("ani", "")), _ten_digits(state.get("telephony_caller_id", ""))) if item)
        known = _session_numbers(callback_context)
    except Exception as err:
        print(f"could not read the session for the account number check: {err}")
    if _looks_like_account_number(text, known=known, last4=last4, own_phones=own_phones):
        return GuardrailDecision(decision="TRIGGER", reason="The response contains a full account number.")
    return GuardrailDecision(decision="OK", reason="No full account number found.")
