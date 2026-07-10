from langchain_core.tools import tool

@tool
def send_rating_poll(item_name: str) -> str:
    """
    Send star rating poll(s) asking the customer to rate what they just ordered.
    Call this IMMEDIATELY after ORDER_SAVED is confirmed — once per order.
    Pass EVERY distinct item that was ordered, not just one.

    Args:
        item_name: comma-separated list of every distinct item ordered, e.g.
            "Velvet Coconut Latte, Bono Latte" — one rating card will be sent
            per item. For a single-item order, pass just that one name.
    """
    return f"RATING_POLL_TRIGGERED|{item_name}"