import json

from langchain_core.tools import tool


@tool
async def send_calculator(
    title: str,
    quantity_label: str,
    result_label: str = "Estimated total",
    categories: list[str] | None = None,
    item_name: str = "",
    choice_label: str = "Which one?",
    max_quantity: int = 200,
) -> str:
    """Send an interactive calculator so the customer can estimate a bulk or
    catering cost themselves.

    You do NOT supply prices or the item list — this tool reads both from the
    live menu. You only say WHICH items belong in it, by naming menu categories
    (call get_menu_prices first to see the exact category names), or by naming
    one specific item.

    Use for a cost that depends on a quantity only the customer knows
    ("coffee for 40 people", "25 lattes"). Do NOT use for a normal order (the
    ordering flow totals that) or a single menu price (call show_item).

    Args:
        title: Card heading, e.g. "Coffee Estimate".
        quantity_label: What they're counting, e.g. "Number of guests".
        result_label: What the answer is called, e.g. "Estimated total".
        categories: Menu categories to offer, EXACTLY as get_menu_prices spells
            them, e.g. ["Hot Classics", "Premium Brews"]. Include every
            category the customer asked about and none they didn't.
        item_name: Use INSTEAD of categories when they named one item ("25
            lattes"). Produces a single quantity field with that item's price
            built in — no picker.
        choice_label: Label above the picker, e.g. "Which drink?".
        max_quantity: Largest quantity they may enter.

    Returns "CALC_TRIGGERED|..." — the card is sent, so reply with nothing else.
    On "CALC_NO_CATEGORY|..." retry with a category name from that list. On
    "CALC_NO_ITEM|..." the item isn't on the menu, so tell the customer you'll
    check rather than guessing a price.
    """
    from db import get_menu_items
    from agent.state import update_menu_cache

    items = await get_menu_items()
    if not items:
        return "CALC_MENU_UNAVAILABLE"
    update_menu_cache(items)  # free: confirm_order reads the same cache

    quantity_field = {
        "key": "quantity",
        "label": quantity_label or "How many?",
        "type": "number",
        "min": 1,
        "max": max(1, int(max_quantity or 200)),
        "required": True,
    }

    if item_name:
        # One named item — the price is a constant, so there is nothing to pick.
        wanted = item_name.strip().lower()
        match = next((i for i in items if str(i["name"]).strip().lower() == wanted), None)
        if not match:
            return f"CALC_NO_ITEM|{item_name}"
        fields = [quantity_field]
        formula = f"quantity * {int(match['price'])}"
    else:
        # Category names are matched against the real `category` column, so the
        # option set is exactly one or more real categories — the model cannot
        # pad it with items the customer never asked about, which is what kept
        # happening when it built the list itself.
        wanted = {str(c).strip().lower() for c in (categories or []) if str(c).strip()}
        chosen = [i for i in items if str(i.get("category", "")).strip().lower() in wanted]
        if not chosen:
            valid = sorted({str(i.get("category", "")).strip() for i in items if i.get("category")})
            return "CALC_NO_CATEGORY|" + ", ".join(valid)
        chosen.sort(key=lambda i: int(i["price"]))
        fields = [
            quantity_field,
            {
                "key": "choice",
                "label": choice_label or "Which one?",
                "type": "choice",
                "required": True,
                # Prices come straight off the menu row — never from the model.
                "options": [{"label": str(i["name"]), "value": int(i["price"])} for i in chosen],
            },
        ]
        formula = "quantity * choice"

    payload = {
        "title": title,
        "fields": fields,
        "formula": formula,
        "result_label": result_label,
        "subtitle": "",
        "result_unit": "PKR",
    }
    # JSON rather than the pipe convention: fields are nested objects.
    # json.dumps never emits a newline, which is what the dispatcher needs.
    return "CALC_TRIGGERED|" + json.dumps(payload, ensure_ascii=False)
