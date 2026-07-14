from langchain_core.tools import tool


@tool
def show_item(item_name: str) -> str:
    """Show a single menu item card with image and price to the customer.
    Call this when customer asks about ONE specific item by name
    (e.g. "show me the zinger burger", "picture of pepperoni pizza", "how much is the cold coffee").
    Pass the exact item name the customer said as item_name.
    """
    from db import get_item_orderable

    if not get_item_orderable(item_name):
        return (
            f"ITEM_TRIGGERED|{item_name}\n"
            f"BANNER_TRIGGERED|warning|{item_name} Unavailable|"
            f"{item_name} isn't available for ordering right now — you can still view it, but it can't be ordered.|"
        )
    return f"ITEM_TRIGGERED|{item_name}"
