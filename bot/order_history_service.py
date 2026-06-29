import json
import logging
from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl
from db import get_orders_by_room, get_payment_statuses

logger = logging.getLogger(__name__)

async def send_order_history_card(room_id: str):
    orders = get_orders_by_room(room_id)

    stable_ids = [o["stable_order_id"] for o in orders if o.get("stable_order_id")]
    payment_statuses = await get_payment_statuses(stable_ids)

    for o in orders:
        sid = o.get("stable_order_id")
        if sid and sid in payment_statuses:
            o["status"] = payment_statuses[sid]
        o.pop("stable_order_id", None)

    dsl = {
        "v": 1,
        "type": "order_history",
        "data": {
            "orders": orders,
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Order history DSL invalid, not sending")
        return

    content = {
        "msgtype": "m.text",
        "body": "Order History",
        "ai.jaeno.dsl": dsl,
    }

    logger.info(f"📜 Full content being sent: {json.dumps(content, default=str)}")

    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content=content,
        )
        logger.info(f"📜 room_send response: {response}")
        logger.info(f"📜 Order history card sent with {len(orders)} orders")
    except Exception as e:
        logger.error(f"❌ room_send failed: {e}", exc_info=True)