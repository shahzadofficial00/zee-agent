from langchain_core.tools import tool


@tool
def show_category(category_name: str) -> str:
    """Show all items in one menu category to the customer.
    Call when customer asks about a CATEGORY of items — not the full menu, not one specific item
    (e.g. "show me your specialty lattes", "what cold drinks do you have", "show me the matcha and frappes").
    Pass the exact category name as category_name.
    """
    return f"CATEGORY_TRIGGERED|{category_name}"
