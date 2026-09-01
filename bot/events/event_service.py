"""Event + ticket cards.

Three outbound types, one inbound (`ticket_request`, handled deterministically
in bot/router/dsl_text_events.py — same path bid_confirmation takes).

Everything the customer sees here is a snapshot: `remaining` is stale the
moment it renders, and so is the client's sold-out state. Neither decides
anything — db.reserve_tickets_if_available() does, in one locked transaction.
Same relationship the auction card's min-bid has to place_bid_if_higher().
"""
import logging

from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


def _event_fields(e: dict) -> dict:
    """The seven fields every one of these cards carries.

    `id` is a string like the menu cards' (`str(i["id"])`) — the generated Dart
    model reads it as a String, and a raw int falls back to an "Unsupported"
    card. Timestamps go out exactly as stored: ISO-8601 with a UTC offset,
    written that way so the client parses them the same as `auction.ends_at`.
    """
    return {
        "id": str(e["id"]),
        "title": str(e["title"]),
        "starts_at": str(e["starts_at"]),
        "ends_at": str(e["ends_at"]),
        "location_type": str(e["location_type"]),
        "location": str(e.get("location") or ""),
        "online_url": str(e.get("online_url") or ""),
    }


async def send_events_card(room_id: str, events: list[dict], title: str = "What's On") -> bool:
    """The list card. Returns False if nothing was sent."""
    if not events:
        await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text",
                     "body": "Nothing on the calendar right now — ask me again soon!"},
        )
        return False

    dsl = {
        "v": 1,
        "type": "event",
        "data": {"title": title, "events": [_event_fields(e) for e in events]},
    }
    if not safe_send_dsl(dsl):
        logger.error("❌ event DSL invalid, not sending")
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": title, "ai.jaeno.dsl": dsl},
    )
    logger.info(f"🎪 Events card sent — {len(events)} events")
    return True


async def send_event_detail_card(room_id: str, event: dict) -> bool:
    """One event with its ticket tiers. `event` is a db.get_event() row."""
    tiers = []
    for t in event.get("tiers", []):
        tier = {
            # Pre-formatted string, same convention as menu prices — the Dart
            # model types it String and throws on a number.
            "name": str(t["name"]),
            "price": str(int(t["price"])),
        }
        # Optional on purpose: it's a count nobody can keep accurate, so a
        # caller with nothing trustworthy omits it rather than shipping a lie.
        if t.get("remaining") is not None:
            tier["remaining"] = int(t["remaining"])
        tiers.append(tier)

    dsl = {
        "v": 1,
        "type": "event_detail",
        "data": {
            **_event_fields(event),
            "description": str(event.get("description") or ""),
            "tiers": tiers,
        },
    }
    if not safe_send_dsl(dsl):
        logger.error("❌ event_detail DSL invalid, not sending")
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": event["title"], "ai.jaeno.dsl": dsl},
    )
    logger.info(f"🎪 Event detail sent — {event['title']} | {len(tiers)} tiers")
    return True


async def send_ticket_confirmation_card(room_id: str, event: dict, tier_name: str,
                                        quantity: int, reference: str) -> bool:
    """Sent only after reserve_tickets_if_available() returned a reference —
    the seats are already committed by the time this card exists."""
    dsl = {
        "v": 1,
        "type": "ticket_confirmation",
        "data": {
            "event_id": str(event["id"]),
            "event_title": str(event["title"]),
            "reference": str(reference),
            "tier_name": str(tier_name),
            "quantity": int(quantity),
            "starts_at": str(event["starts_at"]),
            "ends_at": str(event["ends_at"]),
            "location_type": str(event["location_type"]),
            "location": str(event.get("location") or event.get("online_url") or ""),
        },
    }
    if not safe_send_dsl(dsl):
        logger.error("❌ ticket_confirmation DSL invalid, not sending")
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": f"Ticket {reference}", "ai.jaeno.dsl": dsl},
    )
    logger.info(f"🎟️ Ticket confirmation sent | {reference} | {quantity}x {tier_name}")
    return True
