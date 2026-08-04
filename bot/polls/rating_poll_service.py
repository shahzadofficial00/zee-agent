import uuid
import logging
from bot.dsl_validator import safe_send_dsl
from db import save_poll

logger = logging.getLogger(__name__)


async def send_rating_poll_to_room(
    matrix_client, room_id: str, item_name: str, chain_id: str | None = None
):
    print(f"⭐ RATING POLL SERVICE CALLED for {item_name}")
    poll_id = uuid.uuid4().hex

    dsl = {
        "v": 1,
        "type": "poll",
        "data": {
            "poll_id": poll_id,
            "question": f"How would you rate your {item_name}?",
            "poll_type": "rating",
            "rating_style": "stars",
            "rating_max": 5,
            "options": [],
            "allow_multiple": False,
            "submitted": False,
            "selected": None,
        },
    }

    if chain_id:
        dsl["data"]["chain_id"] = chain_id

    if not safe_send_dsl(dsl):
        logger.error("❌ Rating poll DSL invalid, not sending")
        return None

    content = {
        "msgtype": "m.text",
        "body": f"How would you rate your {item_name}?",
        "ai.jaeno.dsl": dsl,
    }

    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content=content,
        )
        event_id = getattr(response, "event_id", None)
        if event_id:
            save_poll(
                room_id, event_id,
                f"How would you rate your {item_name}?",
                [], multi_select=False,
                poll_id=poll_id, poll_type="rating",
            )
            print(f"⭐ Rating poll sent: event_id={event_id}")
        return event_id
    except Exception as e:
        logger.error(f"❌ Rating poll send failed: {e}", exc_info=True)
        return None