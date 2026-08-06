"""Self-check for the countdown card (JNO-84/85). Run: python -m tests.test_countdown

The card itself is trivial. What's worth testing is the deadline arithmetic —
a countdown that disagrees with the SQL rule it represents is worse than no
countdown at all.
"""
import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

# python-olm isn't installed on every dev machine. Same stub as the other suites.
import nio.store
if not hasattr(nio.store, "SqliteStore"):
    class _StubSqliteStore:
        pass
    nio.store.SqliteStore = _StubSqliteStore

from bot.dsl_validator import validate_dsl

ROOM = "!test:jaeno.ai"


def test_card_validates():
    ends = datetime.now(timezone.utc).isoformat()
    ok, err = validate_dsl({
        "v": 1, "type": "countdown",
        "data": {"title": "Free to cancel", "ends_at": ends},
    })
    assert ok, f"minimal countdown rejected: {err}"

    ok, err = validate_dsl({
        "v": 1, "type": "countdown",
        "data": {"title": "T", "ends_at": ends, "subtitle": "s", "expired_text": "e"},
    })
    assert ok, f"full countdown rejected: {err}"

    for bad, why in (
        ({"ends_at": ends}, "no title"),
        ({"title": "T"}, "no ends_at"),
    ):
        ok, _ = validate_dsl({"v": 1, "type": "countdown", "data": bad})
        assert not ok, f"schema accepted a countdown with {why}"
    print("[ok] countdown validates; title and ends_at both required")


async def test_sender_omits_empty_optionals():
    """Empty subtitle/expired_text must be absent, not "" — the client falls
    back to its own default on absence."""
    from bot.countdown import countdown_service

    sent = []

    async def fake(room_id, message_type=None, content=None, **kw):
        sent.append(content)

    class _Resp:
        event_id = "$evt123"

    async def fake_with_id(room_id, message_type=None, content=None, **kw):
        sent.append(content)
        return _Resp()

    real = countdown_service.matrix_client.room_send
    countdown_service.matrix_client.room_send = fake_with_id
    try:
        ends = datetime.now(timezone.utc).isoformat()
        # Returns the event_id, not a bool — the cancel path needs it to redact.
        assert await countdown_service.send_countdown_card(
            ROOM, "Free to cancel", ends) == "$evt123"
        data = sent[0]["ai.jaeno.dsl"]["data"]
        valid, err = validate_dsl(sent[0]["ai.jaeno.dsl"])
        assert valid, f"emitted payload fails schema: {err}"
        assert "subtitle" not in data and "expired_text" not in data, \
            f"empty optionals were sent instead of omitted: {data}"

        sent.clear()
        await countdown_service.send_countdown_card(ROOM, "T", ends, subtitle="s")
        assert sent[0]["ai.jaeno.dsl"]["data"]["subtitle"] == "s"
        print("[ok] sender: payload validates, empty optionals omitted")
    finally:
        countdown_service.matrix_client.room_send = real


async def test_deadline_matches_the_sql_rule():
    """The whole point: the countdown must expire at the same instant
    cancel_last_pending_order() stops accepting the order."""
    import db.connection as conn_mod
    import db.orders as orders_mod
    from db.orders import init_orders_schema, CANCEL_WINDOW_MINUTES
    from bot.router import agent_dispatch

    path = os.path.join(tempfile.mkdtemp(), "cd.db")
    real_connect = conn_mod._connect

    def temp_connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    conn_mod._connect = temp_connect
    orders_mod._connect = temp_connect
    sent = []

    async def fake_send(room_id, title, ends_at, subtitle="", expired_text=""):
        sent.append({"title": title, "ends_at": ends_at})
        return True

    import bot.countdown.countdown_service as cd
    real_card = cd.send_countdown_card
    cd.send_countdown_card = fake_send
    try:
        c = temp_connect()
        init_orders_schema(c.cursor())
        # Only the columns get_order_by_stable_id() actually reads.
        c.execute(
            "INSERT INTO orders (customer_name, room_id, status, stable_order_id, created_at) "
            "VALUES (?,?,?,?,?)",
            ("T", ROOM, "pending", "ORD-TEST01", "2026-08-06 10:00:00"),
        )
        c.commit()
        c.close()

        await agent_dispatch._send_cancel_countdown(ROOM, "ORD-TEST01")
        assert len(sent) == 1, f"no countdown sent: {sent}"

        # created_at + the window, in UTC with an offset the client can parse.
        expected = (datetime(2026, 8, 6, 10, 0, 0, tzinfo=timezone.utc)
                    + timedelta(minutes=CANCEL_WINDOW_MINUTES)).isoformat()
        assert sent[0]["ends_at"] == expected, \
            f"deadline drifted from the SQL rule: {sent[0]['ends_at']} != {expected}"
        # Offset-aware, or the client reads it as local and is 5h out in Karachi.
        assert datetime.fromisoformat(sent[0]["ends_at"]).tzinfo is not None, \
            "ends_at has no UTC offset"

        # An unknown order must not raise into the receipt path.
        sent.clear()
        await agent_dispatch._send_cancel_countdown(ROOM, "ORD-NOPE99")
        assert sent == [], "sent a countdown for an order that doesn't exist"
        print("[ok] deadline == created_at + window, offset-aware, missing order is a no-op")
    finally:
        cd.send_countdown_card = real_card
        conn_mod._connect = real_connect
        orders_mod._connect = real_connect


async def test_cancel_redacts_the_countdown():
    """A card that only knows ends_at keeps ticking "Free to cancel" over the
    top of an "order cancelled" message. Redaction is the only way back."""
    import db.connection as conn_mod
    import db.orders as orders_mod
    from db.orders import init_orders_schema, set_order_countdown_event, get_order_by_stable_id
    from bot import message_handler

    path = os.path.join(tempfile.mkdtemp(), "redact.db")
    real_connect = conn_mod._connect

    def temp_connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    conn_mod._connect = temp_connect
    orders_mod._connect = temp_connect
    redacted = []

    async def fake_redact(room_id, event_id, reason=None):
        redacted.append(event_id)

    real_redact = message_handler.matrix_client.room_redact
    message_handler.matrix_client.room_redact = fake_redact
    try:
        c = temp_connect()
        init_orders_schema(c.cursor())
        c.execute(
            "INSERT INTO orders (customer_name, room_id, status, stable_order_id, created_at) "
            "VALUES (?,?,?,?,?)",
            ("T", ROOM, "pending", "ORD-RED001", "2026-08-06 10:00:00"),
        )
        c.commit()
        c.close()

        set_order_countdown_event("ORD-RED001", "$countdown_evt")
        assert get_order_by_stable_id("ORD-RED001")["countdown_event_id"] == "$countdown_evt", \
            "event_id did not persist on the order row"

        await message_handler._redact_cancel_countdown(ROOM, "ORD-RED001")
        assert redacted == ["$countdown_evt"], f"card not redacted: {redacted}"

        # An order with no countdown (LLM path failure, or pre-upgrade row)
        # must be a silent no-op, never an exception into the cancel flow.
        redacted.clear()
        c = temp_connect()
        c.execute(
            "INSERT INTO orders (customer_name, room_id, status, stable_order_id, created_at) "
            "VALUES (?,?,?,?,?)",
            ("T", ROOM, "pending", "ORD-RED002", "2026-08-06 10:00:00"),
        )
        c.commit()
        c.close()
        await message_handler._redact_cancel_countdown(ROOM, "ORD-RED002")
        await message_handler._redact_cancel_countdown(ROOM, "ORD-NOPE99")
        assert redacted == [], "redacted something that was never sent"
        print("[ok] cancel redacts the card; no countdown / unknown order is a no-op")
    finally:
        message_handler.matrix_client.room_redact = real_redact
        conn_mod._connect = real_connect
        orders_mod._connect = real_connect


async def main():
    test_card_validates()
    await test_sender_omits_empty_optionals()
    await test_deadline_matches_the_sql_rule()
    await test_cancel_redacts_the_countdown()
    print("\nall countdown checks passed")


if __name__ == "__main__":
    asyncio.run(main())
