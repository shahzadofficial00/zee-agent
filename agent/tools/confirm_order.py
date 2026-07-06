import asyncio
import concurrent.futures
import re
import logging
from langchain_core.tools import tool
from agent.state import MENU_PRICES, is_menu_cache_fresh, update_menu_cache

logger = logging.getLogger(__name__)

_QTY_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1,
}


def _strip_qty(item_str: str) -> str:
    """Strip a trailing 'x2' suffix or a leading quantity word/digit, leaving just the item name."""
    item_str = item_str.strip()
    match = re.match(r'^(.+?)\s+[xX]\s*(\d+)$', item_str)
    if match:
        return match.group(1).strip()
    parts = item_str.split(None, 1)
    if len(parts) == 2:
        first = parts[0].lower()
        if first in _QTY_WORDS or first.isdigit():
            return parts[1].strip()
    return item_str


@tool
def confirm_order(items: str, customer_name: str, phone: str) -> str:
    """
    Save a confirmed order to the database.
    Only call this after collecting: items, customer full name, and phone number.

    Args:
        items: a COMMA-separated list of items, one per item — never join items with "and".
            Use "x<qty>" for quantities. Example: "Latte, Espresso x2, Bono Latte"
            (NOT "Latte and Espresso x2").
    """
    try:
        from db import get_ordering_enabled, get_item_orderable

        if not get_ordering_enabled():
            return "ORDERING_DISABLED: Ordering is currently turned off. Apologize and let the customer know they can't order right now."

        # Defend against the LLM joining items with "and" instead of commas.
        items = re.sub(r'\s+and\s+', ', ', items, flags=re.IGNORECASE)

        items_list_check = [i.strip() for i in items.split(",") if i.strip()]
        for item_str in items_list_check:
            name_only = _strip_qty(item_str)
            if not get_item_orderable(name_only):
                return (
                    f"ITEM_NOT_ORDERABLE: {name_only} is not available for ordering right now.\n"
                    f"BANNER_TRIGGERED|warning|{name_only} Unavailable|"
                    f"{name_only} isn't available for ordering right now — please pick something else from the menu.|"
                )
        # ── Prices: use in-memory cache if fresh, otherwise fetch from DB ──
        if is_menu_cache_fresh():
            prices = dict(MENU_PRICES)
        else:
            from db import get_menu_items

            def load_prices():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(get_menu_items())
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor() as executor:
                fetched = executor.submit(load_prices).result(timeout=10)

            if fetched:
                prices = update_menu_cache(fetched)
            else:
                prices = dict(MENU_PRICES)  # stale cache is better than nothing

        # ── Parse items + compute total ──────────────────────────────────────
        def resolve_price(name: str) -> int:
            from db import fuzzy_match_key
            # Strip a trailing "(Medium)"-style size/option suffix before matching —
            # pricing is keyed by base item name only.
            base_name = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
            match = fuzzy_match_key(base_name, list(prices.keys()))
            return prices[match] if match else 0

        items_list = [i.strip() for i in items.split(",") if i.strip()]
        total = 0
        not_found = []
        line_items = []  # structured breakdown for the receipt card

        for item_str in items_list:
            item_str = item_str.strip()

            # format: "latte x2" or "latte X 2"
            match = re.match(r'^(.+?)\s+[xX]\s*(\d+)$', item_str)
            if match:
                name = match.group(1).strip()
                qty  = int(match.group(2))
            else:
                # strip a leading quantity word or digit: "one latte", "2 latte"
                qty = 1
                parts = item_str.split(None, 1)
                if len(parts) == 2:
                    first = parts[0].lower()
                    if first in _QTY_WORDS:
                        qty = _QTY_WORDS[first]
                        item_str = parts[1]
                    elif first.isdigit():
                        qty = int(first)
                        item_str = parts[1]
                name = item_str

            price = resolve_price(name)
            if price == 0:
                not_found.append(name)
            total += price * qty
            line_items.append({"name": name, "qty": qty, "unit_price": price})

        # ── Guard: refuse bad totals instead of saving a Rs. 0 order ─────────
        if not_found or total < 10:
            missing = ", ".join(not_found) if not_found else "the order"
            logger.warning(f"⚠️ confirm_order blocked — unpriced/zero total. missing={not_found} total={total}")
            return (
                f"ORDER_PRICE_ERROR: Couldn't find a price for: {missing}. "
                f"Ask the customer to confirm the exact item name from the menu before ordering."
            )

        # ── Save order ───────────────────────────────────────────────────────
        from db import save_order

        order_id = None

        def run_in_thread():
            nonlocal order_id
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    save_order(
                        name=customer_name,
                        phone=phone,
                        items=items_list,
                        total=total,
                    )
                )
                if result and isinstance(result, dict) and result.get("data"):
                    order_id = str(result["data"][0]["id"])
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.submit(run_in_thread).result(timeout=10)

        if not order_id:
            import uuid
            order_id = uuid.uuid4().hex[:8]
            logger.warning("⚠️ No order_id from DB, using generated fallback")

        import json as _json
        items_str = "\n".join(f"  • {i}" for i in items_list)
        items_payload = ";".join(items_list)
        line_items_payload = _json.dumps(line_items)  # for the receipt card
        return (
            f"ORDER_SAVED\nItems:\n{items_str}\n"
            f"Total: Rs. {total}\n"
            f"Name: {customer_name}\n"
            f"Phone: {phone}\n"
            f"PAYMENT_TRIGGERED|{total}|{customer_name}|{phone}|{order_id}|{items_payload}|{line_items_payload}"
        )
    except Exception as e:
        logger.error(f"confirm_order error: {e}", exc_info=True)
        return "ORDER_ERROR: Could not save order."
