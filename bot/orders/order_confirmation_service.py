import json
import logging
from datetime import datetime
from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl
from db import get_menu_items

logger = logging.getLogger(__name__)


async def send_order_confirmation_card(
    room_id: str,
    line_items: list[dict],
    total: int,
    customer_name: str,
    order_id: str,
    user_id: str,

):
    """
    JNO-117 — Send an order-confirmation receipt card (before payment).
    `line_items` is a list of {"name": str, "qty": int, "unit_price": int}.
    Looks up each item's image from Supabase (menu_items) and builds the
    reorder text used by the card's "Reorder Same" button.
    """
    menu_items = await get_menu_items()
    image_by_name = {m["name"].lower().strip(): (m.get("image") or "") for m in menu_items}

    items_for_card = []
    reorder_parts = []
    for li in line_items:
        name = li.get("name", "")
        qty = li.get("qty", 1)
        unit_price = li.get("unit_price", 0)
        items_for_card.append({
            "name": name,
            "qty": qty,
            "price": unit_price * qty,
            "image": image_by_name.get(name.lower().strip(), ""),
        })
        reorder_parts.append(f"{name} x{qty}")

    raw_order_text = ", ".join(reorder_parts)

    dsl = {
        "v": 1,
        "type": "order_confirmation",
        "data": {
            "order_id": str(order_id),
            "customer_name": customer_name,
            "location": "Dot Cafe",
            "created_at": f"{datetime.now().day} {datetime.now().strftime('%B, %I:%M%p')}",
            "items": items_for_card,
            "total_amount": total,
            "currency": "PKR",
            "raw_order_text": raw_order_text,
            "user_id": user_id,
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Order confirmation DSL invalid, not sending")
        return

    content = {
        "msgtype": "m.text",
        "body": "Order Confirmation",
        "ai.jaeno.dsl": dsl,
    }

    logger.info(f"🧾 Full content being sent: {json.dumps(content, default=str)}")

    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content=content,
        )
        logger.info(f"🧾 room_send response: {response}")
        logger.info(f"🧾 Order confirmation card sent — {len(items_for_card)} items, total Rs {total}")
    except Exception as e:
        logger.error(f"❌ room_send failed: {e}", exc_info=True)


async def send_order_confirmation_card_v2(
    room_id: str,
    line_items: list[dict],
    total: int,
    customer_name: str,
    order_id: str,
    user_id: str,
    fulfillment: dict,
    version: int = 2,
):
    """
    JNO-184/JNO-185 — same receipt card as v1, plus a `fulfillment` block
    ({method, summary}) the client renders as a _FulfillmentSummaryRow.
    v1 stays untouched/registered for in-flight messages predating this flow.

    JNO-240 — `version=3` emits the identical payload as the cash receipt.
    v3's handler renders a cash line instead of the gateway button and simply
    ignores customer_name/user_id/raw_order_text, so no separate sender (or
    schema def — the schema's order_confirmation branch has no `v`
    discriminator) is needed for it.
    """
    menu_items = await get_menu_items()
    image_by_name = {m["name"].lower().strip(): (m.get("image") or "") for m in menu_items}

    items_for_card = []
    reorder_parts = []
    for li in line_items:
        name = li.get("name", "")
        qty = li.get("qty", 1)
        unit_price = li.get("unit_price", 0)
        items_for_card.append({
            "name": name,
            "qty": qty,
            "price": unit_price * qty,
            "image": image_by_name.get(name.lower().strip(), ""),
        })
        reorder_parts.append(f"{name} x{qty}")

    raw_order_text = ", ".join(reorder_parts)

    dsl = {
        "v": version,
        "type": "order_confirmation",
        "data": {
            "order_id": str(order_id),
            "customer_name": customer_name,
            "location": "Dot Cafe",
            "created_at": f"{datetime.now().day} {datetime.now().strftime('%B, %I:%M%p')}",
            "items": items_for_card,
            "total_amount": total,
            "currency": "PKR",
            "raw_order_text": raw_order_text,
            "user_id": user_id,
        },
    }

    # Omit the block entirely rather than sending empty strings — a cash order
    # (v3) can reach here without fulfillment, and both clients check for the
    # key's presence, so `{"method": "", "summary": ""}` renders a blank row.
    if fulfillment:
        dsl["data"]["fulfillment"] = {
            "method": fulfillment.get("method", ""),
            "summary": fulfillment.get("summary", ""),
        }

    if not safe_send_dsl(dsl):
        logger.error("❌ Order confirmation v2 DSL invalid, not sending")
        return

    content = {
        "msgtype": "m.text",
        "body": "Order Confirmation",
        "ai.jaeno.dsl": dsl,
    }

    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content=content,
        )
        logger.info(f"🧾 Order confirmation v{version} card sent — {len(items_for_card)} items, total Rs {total}")
    except Exception as e:
        logger.error(f"❌ room_send failed: {e}", exc_info=True)