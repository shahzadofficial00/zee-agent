import asyncio
import concurrent.futures
import re
import logging
from langchain_core.tools import tool
from agent.state import MENU_PRICES, CACHED_MENU, is_menu_cache_fresh, update_menu_cache
from db import save_order, save_reservation

logger = logging.getLogger(__name__)


@tool
def show_menu() -> str:
    """Show the cafe menu to the customer. Call this when customer asks for menu."""
    return "MENU_TRIGGERED"

@tool
def confirm_order(items: str, customer_name: str, phone: str) -> str:
    """
    Save a confirmed order to the database.
    Only call this after collecting: items, customer full name, and phone number.
    """
    try:
        from db import get_ordering_enabled, get_item_orderable

        if not get_ordering_enabled():
            return "ORDERING_DISABLED: Ordering is currently turned off. Apologize and let the customer know they can't order right now."

        items_list_check = [i.strip() for i in items.split(",") if i.strip()]
        for item_str in items_list_check:
            name_only = re.sub(r'\s*[xX]\s*\d+$', '', item_str).strip()
            if not get_item_orderable(name_only):
                return f"ITEM_NOT_ORDERABLE: {name_only} is not available for ordering right now. Apologize and ask the customer to pick something else."
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
        _QTY_WORDS = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "a": 1, "an": 1,
        }

        def resolve_price(name: str) -> int:
            name = name.lower().strip()
            if name in prices:
                return prices[name]
            # substring fallback: "latte" matches a key, or key matches the phrase
            for key, val in prices.items():
                if key in name or name in key:
                    return val
            return 0

        items_list = [i.strip() for i in items.split(",") if i.strip()]
        total = 0
        not_found = []
        line_items = []  # NEW: structured breakdown for the receipt card

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


@tool
def confirm_reservation(date: str, time: str, guests: int, customer_name: str, phone: str) -> str:
    """
    Save a confirmed reservation to the database.
    Only call this after collecting: date, time, guests, customer full name, and phone.
    """
    try:
        reservation_time = f"{date} {time}"

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    save_reservation(
                        name=customer_name,
                        phone=phone,
                        time=reservation_time,
                        guests=guests,
                    )
                )
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            future.result(timeout=10)

        return (
            f"RESERVATION_SAVED\nDate: {date}\nTime: {time}\n"
            f"Guests: {guests}\nName: {customer_name}\nPhone: {phone}"
        )
    except Exception as e:
        logger.error(f"confirm_reservation error: {e}")
        return "RESERVATION_ERROR: Could not save reservation."


@tool
def show_order_history() -> str:
    """Show order history to the customer. Call when customer asks for their orders or order history."""
    return "ORDER_HISTORY_TRIGGERED"

@tool
def show_item(item_name: str) -> str:
    """Show a single menu item card with image and price to the customer.
    Call this when customer asks about ONE specific item by name
    (e.g. "show me the zinger burger", "picture of pepperoni pizza", "how much is the cold coffee").
    Pass the exact item name the customer said as item_name.
    """
    return f"ITEM_TRIGGERED|{item_name}"


@tool
def show_category(category_name: str) -> str:
    """Show all items in one menu category to the customer.
    Call when customer asks about a CATEGORY of items — not the full menu, not one specific item
    (e.g. "show me your specialty lattes", "what cold drinks do you have", "show me the matcha and frappes").
    Pass the exact category name as category_name.
    """
    return f"CATEGORY_TRIGGERED|{category_name}"



@tool
def send_single_choice_poll(question: str, options: str) -> str:
    """
    Send a single-choice poll to the customer in chat so they can pick exactly
    one option (e.g. size, flavor, a yes/no preference). Use this instead of
    asking in plain text when there's a short, well-defined list of choices.
    Do not use for open-ended questions.

    Args:
        question: the poll question text
        options: comma-separated list of 2+ choices, e.g. "Small, Medium, Large"
    """
    return f"POLL_TRIGGERED|{question}|{options}"