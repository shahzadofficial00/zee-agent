import logging
from bot.dsl_validator import safe_send_dsl
from bot.matrix_client import matrix_client as _matrix_client
from db import get_poll_answers

logger = logging.getLogger(__name__)


async def send_poll_history_card(matrix_client, room_id: str, sender: str):
    print("📋 POLL HISTORY SERVICE CALLED")

    answers = get_poll_answers(room_id=room_id, sender=sender)

    dsl = {
        "v": 1,
        "type": "poll_history",
        "data": {
            "answers": answers,
            "total": len(answers),
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Poll history DSL invalid, not sending")
        return None

    content = {
        "msgtype": "m.text",
        "body": "Poll History",
        "ai.jaeno.dsl": dsl,
    }

    try:
        response = await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content=content,
        )
        event_id = getattr(response, "event_id", None)
        print(f"📋 Poll history card sent: {len(answers)} answers")
        return event_id
    except Exception as e:
        logger.error(f"❌ Poll history send failed: {e}", exc_info=True)
        return None