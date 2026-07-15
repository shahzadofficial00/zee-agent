import logging
from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


async def _send_dsl_card(room_id: str, dsl: dict, body: str):
    if not safe_send_dsl(dsl):
        logger.error(f"❌ {dsl.get('type')} DSL invalid, not sending")
        return None
    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": body, "ai.jaeno.dsl": dsl},
        )
        return getattr(response, "event_id", None)
    except Exception as e:
        logger.error(f"❌ {dsl.get('type')} send failed: {e}", exc_info=True)
        return None


async def send_fulfillment_method_card(room_id: str, order_id: str):
    title = "How would you like to receive your order?"
    dsl = {
        "v": 1,
        "type": "fulfillment_method",
        "data": {"order_id": order_id, "title": title},
    }
    return await _send_dsl_card(room_id, dsl, title)


async def send_name_request_card(room_id: str, order_id: str, method: str):
    prompt = (
        "What's the name for your table?" if method == "dine_in"
        else "What name should we call for pickup?"
    )
    dsl = {
        "v": 1,
        "type": "name_request",
        "data": {"order_id": order_id, "method": method, "title": prompt},
    }
    return await _send_dsl_card(room_id, dsl, prompt)


async def send_car_request_card(room_id: str, order_id: str):
    title = "Share your car details"
    dsl = {"v": 1, "type": "car_request", "data": {"order_id": order_id, "title": title}}
    return await _send_dsl_card(room_id, dsl, title)


async def send_address_request_card(room_id: str, order_id: str):
    title = "Share your delivery address"
    dsl = {"v": 1, "type": "address_request", "data": {"order_id": order_id, "title": title}}
    return await _send_dsl_card(room_id, dsl, title)


async def send_order_status_card(room_id: str, order_id: str, status: str, message: str = ""):
    dsl = {
        "v": 1,
        "type": "order_status",
        "data": {"order_id": order_id, "status": status, "message": message},
    }
    return await _send_dsl_card(room_id, dsl, f"Order status: {status}")


async def send_order_status_update(order_id: str, status: str, message: str = "") -> bool:
    """Staff entry point (JNO-187/188/189) — resolves order_id -> room and sends
    the status card. No LLM, no chat exposure; called directly (e.g. from
    update_order_status.py), same pattern as create_and_send_auction()."""
    from db import get_order_by_stable_id
    order = get_order_by_stable_id(order_id)
    if not order or not order.get("room_id"):
        logger.error(f"❌ send_order_status_update: no room found for order_id={order_id}")
        return False
    await send_order_status_card(order["room_id"], order_id, status, message)
    return True
