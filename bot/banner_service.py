import logging
from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


async def send_banner_card(room_id: str, variant: str, title: str, message: str = "", meta: str = ""):
    dsl = {
        "v": 1,
        "type": "banner",
        "data": {
            "variant": variant,
            "title": title,
            "message": message,
            "meta": meta,
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ banner DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": title or message, "ai.jaeno.dsl": dsl},
    )
    logger.info(f"📢 Banner sent — variant={variant} title={title!r}")
