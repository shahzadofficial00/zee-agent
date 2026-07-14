from langchain_core.tools import tool

@tool
def send_special_instructions_poll(item_name: str, placeholder: str = "e.g. extra hot, less sugar, no ice") -> str:
    """
    Ask the customer if they have any special instructions for ONE specific item
    in their order. Call this AFTER collecting name and phone, BEFORE showing the
    order summary. If the order has multiple distinct items, call this ONCE PER
    ITEM, one after another, waiting for each answer before asking about the next.

    Args:
        item_name: the exact item this question is about, e.g. "Cold Latte" —
            included in the question so multi-item orders aren't ambiguous.
        placeholder: a relevant hint based on that item's category:
            - Hot drinks (Espresso, Latte, Cappuccino, Mocha, Americano): "e.g. extra hot, less sugar, extra shot"
            - Specialty Lattes (Velvet Coconut, Bono, Choco Hazelnut): "e.g. less sweet, extra shot, oat milk"
            - Matcha & Frappes (Matcha Classic, Strawberry Matcha, Caramel Frappe): "e.g. less sweet, no whipped cream, extra matcha"
            - Cold Drinks (Cold Latte, Berry Mojito, Mango Smoothie): "e.g. less ice, no sugar, extra cold"
    """
    return f"OPEN_POLL_TRIGGERED|Any special instructions for your {item_name}?|{placeholder}"