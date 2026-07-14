from langchain_core.tools import tool


@tool
def show_order_history() -> str:
    """Show order history to the customer. Call when customer asks for their orders or order history."""
    return "ORDER_HISTORY_TRIGGERED"
