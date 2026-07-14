import uuid
import logging
from bot.dsl_validator import safe_send_dsl
from bot.matrix_client import matrix_client as _matrix_client
from db import save_poll

logger = logging.getLogger(__name__)

DEFAULT_QUESTION = "Any special instructions for your order?"

async def send_special_instructions_poll_to_room(
    matrix_client, room_id: str,
    placeholder: str = "e.g. extra hot, less sugar",
    question: str = DEFAULT_QUESTION,
):
    print("📝 SPECIAL INSTRUCTIONS POLL SERVICE CALLED")
    poll_id = uuid.uuid4().hex

    dsl = {
        "v": 1,
        "type": "poll",
        "data": {
            "poll_id": poll_id,
            "question": question,
            "poll_type": "open_text",
            "options": [placeholder],  # used as hint text in text field
            "allow_multiple": False,
            "submitted": False,
            "selected": None,
        },
    }
    # ... rest unchanged

    if not safe_send_dsl(dsl):
        logger.error("❌ Special instructions poll DSL invalid, not sending")
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
            save_poll(room_id, event_id, question, [placeholder], multi_select=False, poll_id=poll_id, poll_type="open_text")
            print(f"📝 Special instructions poll sent: event_id={event_id}")
        return event_id
    except Exception as e:
        logger.error(f"❌ Special instructions poll send failed: {e}", exc_info=True)
        return None