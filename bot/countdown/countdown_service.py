"""Countdown card (JNO-84, story JNO-85).

Read-only: no inbound event, no state, no table. The deadline is just a
timestamp the caller passes, so there is nothing to persist and nothing to
clean up when it lapses — the client greys the card out and that's the end of
it. A bot that *acted* on expiry would be a scheduler, which this epic doesn't
ask for.

Deliberately **not** an LLM tool. A countdown makes a promise about time, and
every LLM-triggered card in this repo has misfired at least once (see the FAQ
section in CLAUDE.md). Callers trigger it deterministically, same as auction
creation and order status.
"""
import logging

from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


async def send_countdown_card(
    room_id: str,
    title: str,
    ends_at: str,
    subtitle: str = "",
    expired_text: str = "",
) -> str | None:
    """Send a card counting down to `ends_at`. Returns the Matrix event_id, or
    None if nothing was sent.

    The event_id is returned rather than a bool so a caller whose deadline can
    be *used early* — the cancel window is the live example — can redact the
    card when that happens. Nothing else can retract a card once it's out: it
    only knows `ends_at`, so it would keep counting down over the top of an
    "order cancelled" message.

    `ends_at` must be ISO 8601 **with a UTC offset** — exactly what
    `datetime.now(timezone.utc).isoformat()` produces, which is the same format
    the auction card already sends, so the client parses both with one code
    path. A naive local timestamp would be read as UTC and render the countdown
    five hours out in Karachi.

    `subtitle`/`expired_text` are omitted from the payload when empty rather
    than sent as "", so the client can fall back to its own default.
    """
    dsl = {
        "v": 1,
        "type": "countdown",
        "data": {
            "title": title,
            "ends_at": ends_at,
            **({"subtitle": subtitle} if subtitle else {}),
            **({"expired_text": expired_text} if expired_text else {}),
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ countdown DSL invalid, not sending")
        return None

    resp = await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": title, "ai.jaeno.dsl": dsl},
    )
    event_id = getattr(resp, "event_id", None)
    logger.info(f"⏳ Countdown sent | {title} | ends {ends_at} | {event_id}")
    return event_id
