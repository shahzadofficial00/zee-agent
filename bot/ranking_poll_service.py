import uuid
import logging
from bot.dsl_validator import safe_send_dsl
from db import save_poll

logger = logging.getLogger(__name__)


async def send_ranking_poll_to_room(matrix_client, room_id: str, question: str, options: list[str]):
    print(f"🔀 RANKING POLL SERVICE CALLED: {question}")
    poll_id = uuid.uuid4().hex

    dsl = {
        "v": 1,
        "type": "poll",
        "data": {
            "poll_id": poll_id,
            "question": question,
            "poll_type": "ranking",
            "options": options,
            "allow_multiple": False,
            "submitted": False,
            "selected": None,
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Ranking poll DSL invalid, not sending")
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
            save_poll(room_id, event_id, question, options, multi_select=False, poll_id=poll_id, poll_type="ranking")
            print(f"🔀 Ranking poll DSL sent: event_id={event_id}")
        return event_id
    except Exception as e:
        logger.error(f"❌ Ranking poll send failed: {e}", exc_info=True)
        return None
