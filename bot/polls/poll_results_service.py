import logging
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


async def send_item_rating_results_to_room(matrix_client, room_id: str, menu_item: str, average: float, total_votes: int):
    """Follow-up card sent right after a customer rates an item, showing the
    average rating across every customer who's ever rated it (aggregate, not
    just this one submission)."""
    print(f"📊 RATING RESULTS SERVICE CALLED for {menu_item}: {average} avg / {total_votes} votes")

    # Matrix's canonical JSON forbids raw floats in event content (Synapse
    # rejects them with M_BAD_JSON) — send the average scaled x10 as a plain
    # integer instead; Flutter divides by 10 to get the decimal back.
    dsl = {
        "v": 1,
        "type": "poll_results",
        "data": {
            "question": f"How would you rate your {menu_item}?",
            "poll_type": "rating",
            "menu_item": menu_item,
            "average_x10": round(average * 10),
            "total_votes": total_votes,
            # The client's generated model does json["results"].map() unguarded,
            # so a missing key throws and the card renders as "unsupported".
            # A rating poll has no per-option breakdown — send an empty list.
            "results": [],
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Rating results DSL invalid, not sending")
        return None

    body = f"⭐ Average rating for {menu_item}: {average} (from {total_votes} rating{'s' if total_votes != 1 else ''})"
    content = {
        "msgtype": "m.text",
        "body": body,
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
            # nio doesn't always raise on failure (e.g. rate limiting, rejected
            # payload) — it can return a RoomSendError with no event_id instead.
            status = getattr(response, "status_code", "?")
            msg = getattr(response, "message", repr(response))
            logger.error(f"❌ Rating results send rejected by Matrix: {status} — {msg}")
        print(f"📊 Rating results sent: event_id={event_id}")
        return event_id
    except Exception as e:
        logger.error(f"❌ Rating results send failed: {e}", exc_info=True)
        return None
