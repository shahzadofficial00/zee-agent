from langchain_core.tools import tool


@tool
def send_single_choice_poll(question: str, options: str) -> str:
    """
    Send a single-choice poll to the customer in chat so they can pick exactly
    one option (e.g. size, flavor, a yes/no preference). Use this instead of
    asking in plain text when there's a short, well-defined list of choices.
    Do not use for open-ended questions.

    Args:
        question: the poll question text
        options: comma-separated list of 2+ choices, e.g. "Small, Medium, Large"
    """
    return f"POLL_TRIGGERED|{question}|{options}"
