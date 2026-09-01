"""
Staff/dev CLI — push event + ticket cards into the room without the LLM.
Same "manually invoked, no chat exposure" pattern as test.py (auctions) and
update_order_status.py.

Usage:
    python send_events.py                            # the events list card
    python send_events.py "Latte Art"                # one event + its ticket tiers
    python send_events.py "Latte Art" Spectator 2    # buy 2 — reserve + confirmation card

The third form runs the real reserve_tickets_if_available(), so it decrements
`remaining` for real. It's the same call the inbound ticket_request handler
makes, minus the app: use it to see the confirmation card and to drain a tier
down to its last seat before testing sold-out in the app.
"""
import sys
import asyncio
from dotenv import load_dotenv
load_dotenv()

import db
from bot.matrix_client import matrix_client
from bot.events.event_service import (
    send_events_card, send_event_detail_card, send_ticket_confirmation_card,
)
from config import BOT_PASSWORD, ROOM_ID


async def main():
    db.init_db()
    args = sys.argv[1:]

    await matrix_client.login(BOT_PASSWORD)
    # One sync first: the encrypted send path looks the room up in client.rooms,
    # which is empty until a sync has landed (same reason auto_join defers its
    # greeting in main.py).
    await matrix_client.sync(timeout=5000)

    try:
        if not args:
            events = db.get_events()
            print(f"Sending {len(events)} events: {[e['title'] for e in events]}")
            await send_events_card(ROOM_ID, events)
            return

        event = db.get_event(args[0])
        if not event:
            print(f"❌ No event matching {args[0]!r}. On the calendar: "
                  f"{[e['title'] for e in db.get_events()]}")
            return

        if len(args) == 1:
            print(f"Sending detail card: {event['title']} | tiers {event['tiers']}")
            await send_event_detail_card(ROOM_ID, event)
            return

        tier_name = args[1]
        quantity = int(args[2]) if len(args) > 2 else 1
        # The bot's own id stands in for a customer — this is a smoke test, and
        # nothing downstream reads tickets.user_id yet.
        reference = db.reserve_tickets_if_available(
            event["id"], tier_name, quantity, matrix_client.user_id)
        if not reference:
            print(f"😔 Sold out / refused: {quantity}x {tier_name!r} for {event['title']}. "
                  f"Tiers now: {db.get_event_tiers(event['id'])}")
            return
        print(f"🎟️ {reference} — {quantity}x {tier_name}. "
              f"Tiers now: {db.get_event_tiers(event['id'])}")
        await send_ticket_confirmation_card(ROOM_ID, event, tier_name, quantity, reference)
    finally:
        await matrix_client.close()


if __name__ == "__main__":
    asyncio.run(main())
