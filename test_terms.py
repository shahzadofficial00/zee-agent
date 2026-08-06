"""Self-check for the terms gate (JNO-90). Run: python test_terms.py

Covers the branching that actually decides whether an order gets placed and
what lands in the agreement record. Supabase is stubbed — this asserts on the
flow logic, not the network.
"""
import asyncio
from unittest.mock import patch

# python-olm isn't installed on every dev machine (it's a compiled dependency,
# painful on Windows), and without it `from nio.store import SqliteStore` in
# bot/matrix_client.py fails at import — which the terms logic doesn't care
# about. Stub just enough to get the module imported. The bot itself still
# needs the real thing to run; see CLAUDE.md → Matrix Client.
import nio.store
if not hasattr(nio.store, "SqliteStore"):
    class _StubSqliteStore:
        pass
    nio.store.SqliteStore = _StubSqliteStore

from bot.dsl_validator import validate_dsl
from bot.terms.terms_service import (
    TERMS_ID, TERMS_VERSION, TERMS_TITLE, TERMS_BODY,
    TERMS_SIGNING_LEVEL, TERMS_ALLOW_DECLINE,
)  # noqa: F401 — TERMS_BODY is used by the snapshot assertions below
from bot.router.state import order_flows
from bot.router.dsl_text_events import handle_dsl_text_event

SENDER = "@test:jaeno.ai"
ROOM = "!test:jaeno.ai"


def _card():
    return {
        "v": 1,
        "type": "terms",
        "data": {
            "terms_id": TERMS_ID,
            "version": TERMS_VERSION,
            "title": TERMS_TITLE,
            "body": TERMS_BODY,
            "signing_level": TERMS_SIGNING_LEVEL,
            "allow_decline": TERMS_ALLOW_DECLINE,
        },
    }


def _response(**overrides):
    data = {
        "terms_id": TERMS_ID,
        "version": TERMS_VERSION,
        "agreed": True,
        "method": "tap",
        "signed_at": "2026-08-05T14:22:10Z",
    }
    data.update(overrides)
    return {"v": 1, "type": "terms_response", "data": data}


def test_card_validates():
    ok, err = validate_dsl(_card())
    assert ok, f"terms card rejected by schema: {err}"

    bad = _card()
    bad["data"]["signing_level"] = "fingerprint"
    ok, _ = validate_dsl(bad)
    assert not ok, "schema accepted a signing_level outside the enum"

    missing = _card()
    del missing["data"]["version"]
    ok, _ = validate_dsl(missing)
    assert not ok, "schema accepted a terms card with no version"
    print("[ok] card validates; bad signing_level and missing version rejected")


async def _run(dsl, saved, placed, stage="awaiting_terms"):
    """Drive one inbound terms_response with Supabase + order placement stubbed."""
    order_flows.clear()
    if stage:
        order_flows[SENDER] = {"stage": stage}

    def fake_save(**kwargs):
        saved.append(kwargs)
        return True

    async def fake_confirm(sender, room_id):
        placed.append(sender)

    with patch("db.save_agreement", fake_save), \
         patch("bot.router.order_flow._send_final_confirm_poll", fake_confirm), \
         patch("bot.router.dsl_text_events.send_text", _noop):
        return await handle_dsl_text_event(dsl, SENDER, ROOM)


async def _noop(*args, **kwargs):
    return None


async def test_agree_places_order():
    saved, placed = [], []
    await _run(_response(), saved, placed)
    assert len(saved) == 1, "agreement was not recorded"
    assert saved[0]["agreed"] is True
    assert placed == [SENDER], "confirm poll not sent after agreeing"
    # The flow has to leave awaiting_terms, or the customer's "Yes" to the
    # confirm poll lands in a stage that ignores it and the order never places.
    assert order_flows[SENDER]["stage"] == "final_confirm", "flow stuck at awaiting_terms"
    print("[ok] agree -> recorded, then confirm poll sent")


async def test_decline_cancels_order():
    saved, placed = [], []
    await _run(_response(agreed=False), saved, placed)
    assert len(saved) == 1, "a decline must still be recorded"
    assert saved[0]["agreed"] is False
    assert saved[0]["signature"] is None, "a decline must not carry a signature"
    assert placed == [], "confirm poll sent despite a decline"
    assert SENDER not in order_flows, "declined order flow was not cleared"
    print("[ok] decline -> recorded, order cancelled, flow cleared")


async def test_method_is_stored_not_signing_level():
    """The card asks for `signing_level`; the record must keep whatever the
    device actually delivered as `method` — that gap is JNO-93/94/95."""
    saved, placed = [], []
    await _run(_response(method="typed", signature="Adnan Shahzad"), saved, placed)
    assert saved[0]["method"] == "typed"
    assert saved[0]["signature"] == "Adnan Shahzad"
    print("[ok] delivered method + signature stored verbatim")


async def test_unknown_version_dropped():
    saved, placed = [], []
    await _run(_response(version="1999-01-01"), saved, placed)
    assert saved == [], "recorded consent to a version we never published"
    assert placed == [], "advanced the flow off an unknown terms version"
    print("[ok] unknown terms version dropped")


async def test_bad_method_dropped():
    saved, placed = [], []
    await _run(_response(method="thumbprint"), saved, placed)
    assert saved == [], "recorded an agreement with a method outside the enum"
    assert placed == [], "advanced the flow off a bad method"
    print("[ok] bad method dropped")


async def test_response_without_parked_order():
    """A stale card tapped from scrollback: consent is real and gets recorded,
    but there's no order to place."""
    saved, placed = [], []
    await _run(_response(), saved, placed, stage=None)
    assert len(saved) == 1, "consent outside a checkout must still be recorded"
    assert placed == [], "advanced a flow that was not parked"
    print("[ok] orphan response recorded without advancing a flow")


def test_history_card_validates():
    """The history payload the Dart card actually reads (JNO-98)."""
    card = {
        "v": 1,
        "type": "terms_history",
        "data": {
            "agreements": [
                {"title": "Order Terms", "version": "2026-08-05", "agreed": True,
                 "method": "typed", "signed_at": "2026-08-05T14:22:10Z"},
                {"title": "Order Terms", "version": "2026-07-01", "agreed": False,
                 "method": "tap", "signed_at": "2026-07-01T09:00:00Z"},
            ]
        },
    }
    ok, err = validate_dsl(card)
    assert ok, f"terms_history rejected by schema: {err}"

    # An empty history is legitimate — the card has its own empty state.
    ok, err = validate_dsl({"v": 1, "type": "terms_history", "data": {"agreements": []}})
    assert ok, f"empty history rejected: {err}"

    # `agreed` must be a real bool: the Dart reads it as `bool?`, so a SQLite
    # 0/1 would come through as null and silently render as "Agreed".
    bad = {"v": 1, "type": "terms_history", "data": {"agreements": [
        {"title": "T", "version": "1", "agreed": 1, "method": "tap"}]}}
    ok, _ = validate_dsl(bad)
    assert not ok, "schema accepted an integer `agreed`"
    print("[ok] terms_history validates; int `agreed` rejected")


def test_sqlite_roundtrip():
    """The storage half, against a real throwaway SQLite file."""
    import tempfile, os, sqlite3
    import db.connection as conn_mod
    from db.terms import (
        init_agreements_schema, has_agreed, save_agreement, get_agreements,
        MAX_SIGNATURE_CHARS,
    )

    path = os.path.join(tempfile.mkdtemp(), "t.db")
    real_connect = conn_mod._connect

    def temp_connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    conn_mod._connect = temp_connect
    import db.terms as terms_mod
    terms_mod._connect = temp_connect
    try:
        c = temp_connect()
        init_agreements_schema(c.cursor())
        c.commit()
        c.close()

        assert not has_agreed(SENDER, TERMS_ID, TERMS_VERSION), "unsigned customer read as agreed"

        # A decline is recorded, but must NOT satisfy the gate.
        save_agreement(SENDER, ROOM, TERMS_ID, TERMS_VERSION, False, "tap", None, "t0")
        assert not has_agreed(SENDER, TERMS_ID, TERMS_VERSION), "a decline counted as consent"

        # Changing your mind upgrades the same row rather than adding a second.
        save_agreement(SENDER, ROOM, TERMS_ID, TERMS_VERSION, True, "typed", "Adnan", "t1")
        assert has_agreed(SENDER, TERMS_ID, TERMS_VERSION), "agreement after decline not honoured"
        assert len(get_agreements(SENDER)) == 1, "decline→agree left a duplicate row"

        # Redelivery of the same event must not overwrite or duplicate.
        save_agreement(SENDER, ROOM, TERMS_ID, TERMS_VERSION, True, "tap", None, "t2")
        rows = get_agreements(SENDER)
        assert len(rows) == 1, "re-sync duplicated an agreement"
        assert rows[0]["method"] == "typed", "redelivery clobbered the stored method"

        # The snapshot is the point of the body column: the row must keep the
        # text that customer saw, not whatever TERMS_BODY says today (JNO-97).
        save_agreement(SENDER, ROOM, TERMS_ID, "v-old", True, "tap", None, "t9",
                       body="OLD WORDING — clause 4 said something else")
        old = [a for a in get_agreements(SENDER) if a["version"] == "v-old"][0]
        assert old["body"] == "OLD WORDING — clause 4 said something else", \
            "stored body was not preserved"
        assert TERMS_BODY not in (old["body"] or ""), \
            "old agreement is rendering today's wording"

        # Rows predating the column carry no body — the card must be able to
        # tell "no copy available" from "blank copy".
        save_agreement(SENDER, ROOM, TERMS_ID, "v-nobody", True, "tap", None, "t10")
        nb = [a for a in get_agreements(SENDER) if a["version"] == "v-nobody"][0]
        assert nb["body"] is None, "missing body should stay None, not empty string"

        # A bumped version re-prompts.
        assert not has_agreed(SENDER, TERMS_ID, "2099-01-01"), "new version did not re-prompt"

        # Oversized signature is rejected outright.
        ok = save_agreement(
            SENDER, ROOM, TERMS_ID, "2099-01-01", True, "drawn",
            "x" * (MAX_SIGNATURE_CHARS + 1), "t3",
        )
        assert ok is False, "oversized signature was accepted"
        assert not has_agreed(SENDER, TERMS_ID, "2099-01-01"), "oversized signature still stored"
        print("[ok] sqlite: decline/upgrade/redelivery/version-bump/size-cap")
    finally:
        conn_mod._connect = real_connect
        terms_mod._connect = real_connect


async def test_history_send_end_to_end():
    """Actually run send_terms_history_card() against real rows and validate
    what it emits — the hand-written payload above proves nothing about the
    function that builds the real one."""
    import tempfile, os, sqlite3
    import db.connection as conn_mod
    import db.terms as terms_mod
    from db.terms import init_agreements_schema, save_agreement
    from bot.terms import terms_service

    path = os.path.join(tempfile.mkdtemp(), "h.db")
    real_connect = conn_mod._connect

    def temp_connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    conn_mod._connect = temp_connect
    terms_mod._connect = temp_connect
    sent = []

    async def fake_room_send(room_id, message_type=None, content=None, **kw):
        sent.append(content)

    real_send = terms_service.matrix_client.room_send
    terms_service.matrix_client.room_send = fake_room_send
    try:
        c = temp_connect()
        init_agreements_schema(c.cursor())
        c.commit()
        c.close()

        # One row with a body, one without (predates the column).
        save_agreement(SENDER, ROOM, TERMS_ID, "v1", True, "typed", "Adnan", "2026-08-01T10:00:00Z",
                       body="V1 WORDING")
        save_agreement(SENDER, ROOM, TERMS_ID, "v2", False, "tap", None, "2026-08-02T10:00:00Z")

        ok = await terms_service.send_terms_history_card(ROOM, SENDER)
        assert ok, "send_terms_history_card returned False — payload was blocked"
        assert len(sent) == 1, "expected exactly one event"

        dsl = sent[0]["ai.jaeno.dsl"]
        valid, err = validate_dsl(dsl)
        assert valid, f"emitted history payload fails schema: {err}"

        rows = dsl["data"]["agreements"]
        assert len(rows) == 2, f"expected 2 agreements, got {len(rows)}"

        by_version = {r["version"]: r for r in rows}
        assert by_version["v1"]["body"] == "V1 WORDING", "stored body not sent"
        assert by_version["v1"]["agreed"] is True
        assert isinstance(by_version["v1"]["agreed"], bool), "`agreed` must be a JSON bool"

        # The row with no body must OMIT the key, not send null — the card
        # keys its "View copy" affordance off presence.
        assert "body" not in by_version["v2"], "null body sent instead of omitted"
        assert by_version["v2"]["agreed"] is False, "decline not carried through"

        # Newest first.
        assert rows[0]["version"] == "v2", "history not ordered newest-first"

        # An empty history must still send (the card has an empty state).
        sent.clear()
        ok = await terms_service.send_terms_history_card(ROOM, "@nobody:jaeno.ai")
        assert ok and sent[0]["ai.jaeno.dsl"]["data"]["agreements"] == []
        print("[ok] history send: real payload validates, body omitted when absent")
    finally:
        terms_service.matrix_client.room_send = real_send
        conn_mod._connect = real_connect
        terms_mod._connect = real_connect


async def main():
    test_card_validates()
    test_history_card_validates()
    test_sqlite_roundtrip()
    await test_history_send_end_to_end()
    await test_agree_places_order()
    await test_decline_cancels_order()
    await test_method_is_stored_not_signing_level()
    await test_unknown_version_dropped()
    await test_bad_method_dropped()
    await test_response_without_parked_order()
    print("\nall terms checks passed")


if __name__ == "__main__":
    asyncio.run(main())
