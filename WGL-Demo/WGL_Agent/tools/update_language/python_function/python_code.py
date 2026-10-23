def update_language(new_language: str = "") -> dict:
    """Switches the conversation language to English or Spanish when the caller switches. Call it
    before replying in the new language. No spoken pacing is needed; this tool is silent and
    instant.

    Args:
        new_language: EN, ES, English or Spanish (REQUIRED).

    Returns:
        dict with ok, language and agent_action telling you to continue in that language.
    """
    wanted = str(new_language).strip().lower()
    if wanted in ("es", "spanish", "espanol", "español"):
        code, name = "ES", "Spanish"
    elif wanted in ("en", "english"):
        code, name = "EN", "English"
    else:
        return _validation_error("English or Spanish")
    context.state["language"] = code
    return {"ok": True, "language": code, "agent_action": f"Continue the whole conversation in {name} from this reply onward."}


def _error(error_class: str, agent_action: str, **extra) -> dict:
    """Builds a failure result: error_class is validation, business_rule, transient or CIS_DOWN."""
    result = {"ok": False, "error_class": error_class, "agent_action": agent_action}
    result.update(extra)
    return result


def _validation_error(problem: str) -> dict:
    """The input was missing or malformed; the agent should re-ask for that detail."""
    return _error("validation", f"Ask the customer again for the detail that is missing or invalid: {problem}.")
