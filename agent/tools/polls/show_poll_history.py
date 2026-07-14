from langchain_core.tools import tool

@tool
def show_poll_history() -> str:
    """
    Show the customer a history of all polls they have answered in this chat.
    Call this when customer asks:
    - "show my poll history"
    - "what polls did I answer"
    - "show my answers"
    - "poll history"
    """
    return "POLL_HISTORY_TRIGGERED"