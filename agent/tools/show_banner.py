from langchain_core.tools import tool


@tool
def show_banner(variant: str, title: str, message: str = "", meta: str = "") -> str:
    """
    Show an attention-grabbing banner card to the customer instead of plain text.
    Use for things worth visually calling out: a real outage/disruption, a
    caveat the customer should know before proceeding, a confirmation/promo,
    or a neutral announcement.

    Args:
        variant: one of "outage", "critical", "warning", "success", "info".
            - outage/critical: something is broken right now (payments down, kitchen closed)
            - warning: a caveat, not broken (an item is delayed, limited tables left)
            - success: a confirmation or promo (discount, confirmed reservation)
            - info: neutral announcement (new menu items, general FYI)
        title: short headline for the banner
        message: 1-2 sentence body text
        meta: optional small supporting text (e.g. "ETA: 2:00 PM", "Use code WEEKEND20")
    """
    return f"BANNER_TRIGGERED|{variant}|{title}|{message}|{meta}"
