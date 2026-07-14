import uuid
import logging
from bot.dsl_validator import safe_send_dsl
from bot.matrix_client import matrix_client as _matrix_client
from db import save_poll

logger = logging.getLogger(__name__)

QUESTION = "What flavors do you enjoy?"
OPTIONS = ["Bold & Strong", "Creamy", "Chocolatey", "Sweet", "Nutty", "Fruity", "Earthy & Matcha"]


async def send_flavor_preference_poll_to_room(matrix_client, room_id: str):
    print("🟣 FLAVOR POLL SERVICE CALLED")
    poll_id = uuid.uuid4().hex

    dsl = {
        "v": 1,
        "type": "poll",
        "data": {
            "poll_id": poll_id,
            "question": QUESTION,
            "poll_type": "multi_select",
            "options": OPTIONS,
            "allow_multiple": True,
            "submitted": False,
            "selected": None,
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Flavor poll DSL invalid, not sending")
        return None

    content = {
        "msgtype": "m.text",
        "body": QUESTION,
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
            save_poll(room_id, event_id, QUESTION, OPTIONS, multi_select=True, poll_id=poll_id, poll_type="multi_select")
            print(f"🟣 Flavor poll DSL sent: event_id={event_id}")
        return event_id
    except Exception as e:
        logger.error(f"❌ Flavor poll send failed: {e}", exc_info=True)
        return None