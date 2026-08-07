from langchain_core.tools import tool


@tool
async def get_menu_prices() -> str:
    """Get the menu grouped by category, with real prices.

    Call this BEFORE send_calculator: it is where the exact category names come
    from, and send_calculator only accepts categories spelled the way they
    appear here. Do not read the list out to the customer — call show_menu for
    that. Returns "MENU_PRICES_UNAVAILABLE" if the menu can't be reached, in
    which case do NOT build a calculator.
    """
    from db import get_menu_items
    from agent.state import update_menu_cache

    items = await get_menu_items()
    if not items:
        return "MENU_PRICES_UNAVAILABLE"
    update_menu_cache(items)  # free: confirm_order reads the same cache

    # Grouped by the real `category` column rather than a flat list, because
    # that column is what send_calculator resolves against — a flat list left
    # the model inventing its own groupings, and it padded a coffee estimate
    # with smoothies and mojitos.
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(str(item.get("category", "Other")).strip() or "Other", []).append(item)

    lines = []
    for category in sorted(grouped):
        entries = sorted(grouped[category], key=lambda i: int(i["price"]))
        lines.append(
            f"{category}: " + ", ".join(f"{i['name']} {int(i['price'])}" for i in entries)
        )
    return "\n".join(lines)
