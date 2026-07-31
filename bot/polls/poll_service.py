import uuid
import logging
from bot.dsl_validator import safe_send_dsl
from bot.matrix_client import matrix_client
from db import save_poll

logger = logging.getLogger(__name__)


async def send_single_choice_poll_to_room(
    matrix_client, room_id: str, question: str, options: list[str], chain_id: str | None = None
):
    print("🔵 SINGLE CHOICE POLL SERVICE CALLED")
    poll_id = uuid.uuid4().hex

    dsl = {
        "v": 1,
        "type": "poll",
        "data": {
            "poll_id": poll_id,
            "question": question,
            "poll_type": "single_choice",
            "options": options,
            "allow_multiple": False,
            "submitted": False,
            "selected": None,
        },
    }
    if chain_id:
        dsl["data"]["chain_id"] = chain_id

    if not safe_send_dsl(dsl):
        logger.error("❌ Poll DSL invalid, not sending")
        return None

    content = {
        "msgtype": "m.text",
        "body": question,
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
            save_poll(room_id, event_id, question, options, multi_select=False, poll_id=poll_id, poll_type="single_choice")
            print(f"🔵 Single choice poll DSL sent: event_id={event_id}")
        return event_id
    except Exception as e:
        logger.error(f"❌ Poll send failed: {e}", exc_info=True)
        return None