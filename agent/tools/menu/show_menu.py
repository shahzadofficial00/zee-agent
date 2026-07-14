from langchain_core.tools import tool


@tool
def show_menu() -> str:
    """Show the cafe menu to the customer. Call this when customer asks for menu."""
    return "MENU_TRIGGERED"
