"""Self-check for events + ticket sales. Run: python -m tests.test_events

The only thing here worth a test is the money path: two customers tapping the
last seat at the same moment must produce exactly one ticket and one refusal.
Everything else is card-shaped and the schema covers it.
"""
import asyncio
import os
import sqlite3
import tempfile
import threading

# python-olm isn't installed on every dev machine. Same stub as the other suites.
import nio.store
if not hasattr(nio.store, "SqliteStore"):
    class _StubSqliteStore:
        pass
    nio.store.SqliteStore = _StubSqliteStore

from bot.dsl_validator import validate_dsl

ROOM = "!test:jaeno.ai"


def _temp_db():
    """Point db.connection at a throwaway file and seed the schema."""
    import db.connection as conn_mod
    import db.events as events_mod

    path = os.path.join(tempfile.mkdtemp(), "events.db")

    def temp_connect():
        c = sqlite3.connect(path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    conn_mod._connect = temp_connect
    events_mod._connect = temp_connect
    c = temp_connect()
    events_mod.init_events_schema(c.cursor())
    c.commit()
    c.close()
    return temp_connect


def test_last_seat_race():
    """Two threads, one seat. Exactly one reference, exactly one None.

    Threads rather than a mocked lock: the point is that real concurrent
    connections hit the real BEGIN IMMEDIATE. Read-then-write outside the
    transaction sells this seat twice and this check is what catches it.
    """
    connect = _temp_db()
    from db.events import reserve_tickets_if_available, get_event_tiers

    c = connect()
    c.execute("UPDATE event_tiers SET remaining = 1 WHERE event_id = 1 AND name = 'Competitor'")
    c.commit()
    c.close()

    results, barrier = [], threading.Barrier(2)

    def grab(user):
        barrier.wait()
        results.append(reserve_tickets_if_available(1, "Competitor", 1, user))

    threads = [threading.Thread(target=grab, args=(f"@u{i}:jaeno.ai",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    won = [r for r in results if r]
    assert len(won) == 1, f"the last seat went to {len(won)} customers: {results}"
    assert won[0].startswith("TKT-"), f"bad reference: {won[0]}"

    remaining = next(t["remaining"] for t in get_event_tiers(1) if t["name"] == "Competitor")
    assert remaining == 0, f"remaining is {remaining}, should be 0"

    c = connect()
    rows = c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    c.close()
    assert rows == 1, f"{rows} ticket rows written for one seat"
    print("[ok] last seat: one success, one refusal, one ticket row")


def test_reserve_rejects_the_obvious():
    connect = _temp_db()
    from db.events import reserve_tickets_if_available

    assert reserve_tickets_if_available(1, "Spectator", 0, "@u:x") is None, "sold 0 tickets"
    assert reserve_tickets_if_available(1, "Spectator", -3, "@u:x") is None, "a negative quantity got through"
    assert reserve_tickets_if_available(1, "Spectator", 999, "@u:x") is None, "oversold the tier"
    assert reserve_tickets_if_available(1, "Nonexistent", 1, "@u:x") is None, "sold a tier that doesn't exist"
    assert reserve_tickets_if_available(99, "Spectator", 1, "@u:x") is None, "sold for an unknown event"
    # Case-insensitive tier match, since the name round-trips through the client.
    assert reserve_tickets_if_available(1, "spectator", 2, "@u:x"), "exact-case tier name required"
    print("[ok] reserve refuses bad quantities, unknown tiers and unknown events")


def test_cards_validate():
    from db.events import get_events, get_event

    _temp_db()
    events = get_events()
    assert events, "seed produced no upcoming events"

    ok, err = validate_dsl({
        "v": 1, "type": "event",
        "data": {"title": "What's On", "events": [{
            "id": "1", "title": "T", "starts_at": "2026-09-01T18:00:00+00:00",
            "ends_at": "2026-09-01T20:00:00+00:00", "location_type": "in_person",
            "location": "Dot Cafe", "online_url": "",
        }]},
    })
    assert ok, f"event card rejected: {err}"

    ok, _ = validate_dsl({"v": 1, "type": "event", "data": {"title": "T", "events": []}})
    assert not ok, "schema accepted an event card with no events"

    ok, err = validate_dsl({
        "v": 1, "type": "event_detail",
        "data": {
            "id": "1", "title": "T", "starts_at": "x", "ends_at": "y",
            "location_type": "online", "location": "", "online_url": "https://x",
            "description": "d",
            # remaining is optional — a count nobody can keep accurate.
            "tiers": [{"name": "Standard", "price": "1000", "remaining": 4},
                      {"name": "Late", "price": "1200"}],
        },
    })
    assert ok, f"event_detail rejected: {err}"

    ok, _ = validate_dsl({
        "v": 1, "type": "event_detail",
        "data": {"id": "1", "title": "T", "starts_at": "x", "ends_at": "y",
                 "location_type": "in_person", "tiers": [{"name": "S", "price": 1000}]},
    })
    assert not ok, "schema accepted a numeric price — the Dart model types it String"

    ok, err = validate_dsl({
        "v": 1, "type": "ticket_confirmation",
        "data": {"event_id": "1", "event_title": "T", "reference": "TKT-ABC123",
                 "tier_name": "Standard", "quantity": 2, "starts_at": "x",
                 "ends_at": "y", "location_type": "in_person", "location": "Dot Cafe"},
    })
    assert ok, f"ticket_confirmation rejected: {err}"
    print("[ok] all three card schemas validate; price stays a string")


async def test_ticket_request_is_deterministic():
    """The inbound half: never reaches the LLM, and a sold-out tier answers with
    a message rather than a card."""
    _temp_db()
    from bot.router import dsl_text_events
    import bot.events.event_service as event_service

    sent_text, sent_cards = [], []

    async def fake_text(room_id, text):
        sent_text.append(text)

    async def fake_card(room_id, event, tier_name, quantity, reference):
        sent_cards.append((event["title"], tier_name, quantity, reference))
        return True

    real_text = dsl_text_events.send_text
    real_card = event_service.send_ticket_confirmation_card
    dsl_text_events.send_text = fake_text
    event_service.send_ticket_confirmation_card = fake_card
    try:
        req = {"type": "ticket_request",
               "data": {"event_id": "1", "tier_name": "Spectator", "quantity": 2}}
        handled, override = await dsl_text_events.handle_dsl_text_event(req, "@u:x", ROOM)
        assert handled and override is None, "ticket_request fell through to the agent"
        assert len(sent_cards) == 1, f"no confirmation card: {sent_cards} {sent_text}"
        assert sent_cards[0][3].startswith("TKT-")

        # Drain the tier, then ask again — must be a plain message, no card.
        import db.events as events_mod
        conn = events_mod._connect()
        conn.execute("UPDATE event_tiers SET remaining = 0 WHERE event_id = 1")
        conn.commit()
        conn.close()

        sent_cards.clear()
        await dsl_text_events.handle_dsl_text_event(req, "@u:x", ROOM)
        assert not sent_cards, "sold a ticket from an empty tier"
        assert any("sold out" in t.lower() for t in sent_text), f"no sold-out message: {sent_text}"

        # An unknown event must not raise into the handler.
        sent_text.clear()
        await dsl_text_events.handle_dsl_text_event(
            {"type": "ticket_request", "data": {"event_id": "404", "tier_name": "x", "quantity": 1}},
            "@u:x", ROOM)
        assert sent_text, "unknown event answered with silence"
        print("[ok] ticket_request: card on success, message when sold out, no LLM")
    finally:
        dsl_text_events.send_text = real_text
        event_service.send_ticket_confirmation_card = real_card


async def test_a_miss_says_no_out_loud():
    """"do you have a sourdough workshop?" must not be answered with a bare
    list card. Seen live: show_event misses, the model falls back to the list
    and writes no prose of its own (MiniMax emits the signal only), so the
    denial has to come from here or it doesn't exist."""
    _temp_db()
    from bot.router import agent_dispatch

    sent_text, sent_cards = [], []

    async def fake_text(room_id, text):
        sent_text.append(text)

    async def fake_events_card(room_id, events, title="What's On"):
        sent_cards.append(len(events))
        return True

    class _Msg:
        def __init__(self, content):
            self.content = content

    real_text = agent_dispatch.send_text
    import bot.events.event_service as event_service
    real_card = event_service.send_events_card
    real_typing = agent_dispatch.matrix_client.room_typing

    async def no_typing(*a, **kw):
        pass

    agent_dispatch.send_text = fake_text
    event_service.send_events_card = fake_events_card
    agent_dispatch.matrix_client.room_typing = no_typing
    try:
        # "EVENTS_CARD" is what the model actually wrote here in production — an
        # invented marker. Sent as the lead-in it would read as gibberish to the
        # customer and be copied back out of history on the next turn.
        result = {"messages": [_Msg("EVENT_NO_MATCH"), _Msg("EVENTS_TRIGGERED"),
                               _Msg("EVENTS_CARD")]}
        clean = await agent_dispatch._dispatch_agent_result(result, ROOM, "@u:x")
        assert sent_cards, "no events card sent after a miss"
        assert sent_text, "a miss was answered with a card and no denial"
        assert clean and " " in clean, f"clean_reply is not a sentence: {clean!r}"
        assert clean == sent_text[0], "history says something different to the customer"
        assert "EVENTS_CARD" not in clean and "EVENTS_CARD" not in sent_text[0],             "an invented marker reached the customer as the reply"
        print("[ok] a miss sends the denial as text, then the list")
    finally:
        agent_dispatch.send_text = real_text
        event_service.send_events_card = real_card
        agent_dispatch.matrix_client.room_typing = real_typing


def test_tools_never_decide_availability():
    """show_event/show_events emit signal strings only — no writes, no seats."""
    _temp_db()
    from agent.tools import show_events, show_event

    assert show_events.invoke({}) == "EVENTS_TRIGGERED"
    assert show_event.invoke({"event_name": "Latte Art Throwdown"}) == \
        "EVENT_TRIGGERED|Latte Art Throwdown"
    assert show_event.invoke({"event_name": "Sourdough Rave"}) == "EVENT_NO_MATCH"
    assert "quantity" not in show_event.args and "quantity" not in show_events.args, \
        "a tool took a quantity — booking belongs to reserve_tickets_if_available()"

    import db.events as events_mod
    conn = events_mod._connect()
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0, \
        "a tool wrote to the tickets table"
    conn.close()
    print("[ok] tools signal only; nothing written, nothing promised")


async def main():
    test_last_seat_race()
    test_reserve_rejects_the_obvious()
    test_cards_validate()
    await test_ticket_request_is_deterministic()
    await test_a_miss_says_no_out_loud()
    test_tools_never_decide_availability()
    print("\nall event checks passed")


if __name__ == "__main__":
    asyncio.run(main())
