import logging
import uuid
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


async def send_tip_request_to_room(
    matrix_client,
    room_id: str,
    recipient_name: str = "",
    presets: list | None = None,
    message: str = "Enjoyed your order? Add a tip for our team!",
    currency: str = "PKR",
) -> str | None:
    """Sends a tip_request DSL card. Returns the generated tip_id, or None on failure."""
    tip_id = uuid.uuid4().hex
    presets = presets or [50, 100, 200]

    dsl = {
        "v": 1,
        "type": "tip_request",
        "data": {
            "tip_id": tip_id,
            "title": "Add a Tip?",
            "message": message,
            "recipient_name": recipient_name,
            "currency": currency,
            "presets": presets,
            "allow_custom": True,
            "allow_decline": True,
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Tip request DSL invalid, not sending")
        return None

    content = {
        "msgtype": "m.text",
        "body": message,
        "ai.jaeno.dsl": dsl,
    }

    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content=content,
        )
        event_id = getattr(response, "event_id", None)
        if event_id is None:
            status = getattr(response, "status_code", "?")
            msg = getattr(response, "message", repr(response))
            logger.error(f"❌ Tip request send rejected by Matrix: {status} — {msg}")
            return None
        logger.info(f"💝 Tip request sent: tip_id={tip_id} event_id={event_id}")
        return tip_id
    except Exception as e:
        logger.error(f"❌ Tip request send failed: {e}", exc_info=True)
        return None
