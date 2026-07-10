from langchain_core.tools import tool

@tool
def send_ranking_poll(question: str, options: str) -> str:
    """
    Ask the customer to drag a short list of options into their preferred order.
    Use this in exactly two situations:
    1. Tie-break: a flavor poll answer maps to two or more categories with an equal
       match count — pass ONLY the tied flavors as options.
    2. Item indecision: the customer names 2+ specific menu items and can't decide
       between them (e.g. "should I get the Cold Latte or the Mango Smoothie?") —
       pass those exact item names as options.
    Do NOT use this for anything else (never for size, confirmation, or a first-pass
    recommendation — those use send_single_choice_poll or send_flavor_preference_poll).

    Args:
        question: e.g. "You picked both equally — which matters more to you?" or
            "Which one sounds best to you?"
        options: comma-separated list of ONLY the tied flavors, or ONLY the named
            items the customer is torn between, e.g. "Cold Latte, Mango Smoothie"
    """
    return f"RANKING_POLL_TRIGGERED|{question}|{options}"
