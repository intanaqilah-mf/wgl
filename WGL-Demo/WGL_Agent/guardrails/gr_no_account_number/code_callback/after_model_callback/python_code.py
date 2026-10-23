"""Code-callback guardrail gr_no_account_number (after the model): a full account number never appears in output.

BR-GU-021: only the last 4 digits of an account number may be spoken or shown. Narrow on purpose: phone
numbers, dates and short confirmation numbers pass; only long digit runs or grouped account numbers trigger.

PLATFORM GLOBALS (not imported): CallbackContext, LlmResponse.
"""

import re
from dataclasses import dataclass

# Grouped account numbers such as 1234-5678-9012.
_GROUPED_ACCOUNT = re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}\b")
# Phone numbers are allowed: optional +1, then three, three and four digits with separators.
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
# Eleven or more consecutive digits; eleven digits that start with 1 are a phone number with country code.
_LONG_RUN = re.compile(r"\d{11,}")


@dataclass
class GuardrailDecision:
    """Result the platform converts with dataclasses.asdict: decision is OK or TRIGGER."""

    decision: str
    reason: str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


def _looks_like_account_number(text: str) -> bool:
    without_phones = _PHONE.sub(" ", text)
    if _GROUPED_ACCOUNT.search(without_phones):
        return True
    return any(len(run) != 11 or not run.startswith("1") for run in _LONG_RUN.findall(without_phones))


def after_model_guardrail_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> GuardrailDecision:
    text = " ".join((part.text_or_transcript() or "") for part in llm_response.content.parts)
    if _looks_like_account_number(text):
        return GuardrailDecision(decision="TRIGGER", reason="The response contains a full account number.")
    return GuardrailDecision(decision="OK", reason="No full account number found.")
