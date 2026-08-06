"""Self-check for the FAQ cards (JNO-54/55/56). Run: python test_faq.py

Covers the two things that can actually break: the payload the Dart card parses,
and the match ladder that decides which FAQ (if any) answers a question.
"""
import asyncio
import os
import sqlite3
import tempfile

# python-olm isn't installed on every dev machine, and without it
# bot/matrix_client.py fails at import. Same stub as test_terms.py.
import nio.store
if not hasattr(nio.store, "SqliteStore"):
    class _StubSqliteStore:
        pass
    nio.store.SqliteStore = _StubSqliteStore

from bot.dsl_validator import validate_dsl

ROOM = "!test:jaeno.ai"


def _card(items):
    return {"v": 1, "type": "faq", "data": {"title": "FAQ", "items": items}}


def test_card_validates():
    one = _card([{"question": "Do you deliver?", "answer": "Yes, in DHA Phase 4."}])
    ok, err = validate_dsl(one)
    assert ok, f"single-entry faq rejected: {err}"

    many = _card([
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ])
    ok, err = validate_dsl(many)
    assert ok, f"accordion faq rejected: {err}"

    # An empty list is legal by schema — the card has its own empty state — but
    # send_faq_card() short-circuits before ever sending one.
    ok, _ = validate_dsl(_card([]))
    assert ok, "empty items rejected"

    # The Dart reads question/answer as String? off a plain map, so a non-string
    # throws in the cast and renders "Unsupported" — same class as menu `price`.
    ok, _ = validate_dsl(_card([{"question": "Q", "answer": 42}]))
    assert not ok, "schema accepted a non-string answer"

    ok, _ = validate_dsl(_card([{"question": "Q"}]))
    assert not ok, "schema accepted an entry with no answer"
    print("[ok] faq card validates; non-string and missing answer rejected")


def _with_temp_db(fn):
    """Run fn against a throwaway SQLite file seeded with the default FAQs."""
    import db.connection as conn_mod
    import db.faqs as faqs_mod
    from db.faqs import init_faqs_schema

    path = os.path.join(tempfile.mkdtemp(), "faq.db")
    real_connect = conn_mod._connect

    def temp_connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    conn_mod._connect = temp_connect
    faqs_mod._connect = temp_connect
    try:
        c = temp_connect()
        init_faqs_schema(c.cursor())
        c.commit()
        c.close()
        fn()
    finally:
        conn_mod._connect = real_connect
        faqs_mod._connect = real_connect


def test_seed_and_matching():
    from db.faqs import get_faqs, get_faq, _SEED_FAQS, init_faqs_schema
    import db.connection as conn_mod

    def body():
        rows = get_faqs()
        assert len(rows) == len(_SEED_FAQS), f"expected seeded rows, got {len(rows)}"
        # Insertion order is the accordion's order (JNO-56) — no sort column.
        assert rows[0]["question"] == _SEED_FAQS[0][0], "seed order not preserved"

        # Exact.
        assert get_faq("How can I pay?")["answer"].startswith("We're cash only")
        # Case and whitespace.
        assert get_faq("  how can i pay?  ") is not None, "match is case/space sensitive"
        # Substring either direction.
        assert get_faq("do you deliver")["question"] == "Do you deliver?"
        assert get_faq("what is your cancellation policy") is not None, "dropped trailing '?' missed"
        # No seeded question may contain a bare "cancel" — message_handler's
        # \bcancel\b fast path intercepts before the agent runs, so such an FAQ
        # is unreachable and mid-checkout it cancels the customer's order.
        import re
        for q, _a in _SEED_FAQS:
            assert not re.search(r'\bcancel\b', q, re.IGNORECASE), \
                f"seeded question is shadowed by the cancel fast path: {q!r}"
        # A genuine miss must return None, not a bad guess — the caller answers
        # normally instead of confidently showing the wrong FAQ. This one scored
        # 0.60 on difflib against "Do you cater for allergies?", which is why
        # there is no fuzzy tier.
        assert get_faq("do you have parking for a minibus") is None, "matched an unrelated question"
        assert get_faq("what payment methods do you take") is None, "matched on noise"
        assert get_faq("") is None and get_faq("   ") is None, "empty question matched"

        # Re-running init must not duplicate the seed.
        c = conn_mod._connect()
        init_faqs_schema(c.cursor())
        c.commit()
        c.close()
        assert len(get_faqs()) == len(_SEED_FAQS), "re-init duplicated the seed rows"

    _with_temp_db(body)
    print("[ok] seed once, ordered, matched exact/case/substring, miss -> None")


async def test_send_paths():
    """Run the real sender and validate what it actually emits."""
    from bot.faq import faq_service

    sent = []

    async def fake_room_send(room_id, message_type=None, content=None, **kw):
        sent.append(content)

    real_send = faq_service.matrix_client.room_send
    faq_service.matrix_client.room_send = fake_room_send
    try:
        ok = await faq_service.send_faq_card(
            ROOM, [{"question": "Do you deliver?", "answer": "Yes."}]
        )
        assert ok and len(sent) == 1
        dsl = sent[0]["ai.jaeno.dsl"]
        valid, err = validate_dsl(dsl)
        assert valid, f"emitted faq payload fails schema: {err}"
        assert len(dsl["data"]["items"]) == 1, "single-entry card lost its item"

        # Empty -> plain text, never an empty card.
        sent.clear()
        ok = await faq_service.send_faq_card(ROOM, [])
        assert ok is False, "empty item list reported as sent"
        assert "ai.jaeno.dsl" not in sent[0], "sent an empty faq card instead of text"
        print("[ok] sender: real payload validates, empty list falls back to text")
    finally:
        faq_service.matrix_client.room_send = real_send


def test_history_placeholder_never_reaches_the_customer():
    """Regression: the model copies bracketed history placeholders back out as
    its own reply. A customer was shown "[FAQ card sent]" three turns running."""
    from bot.router.agent_dispatch import _is_history_placeholder

    for bad in ("[FAQ card sent]",
                "[Poll history card sent]",
                "[Banner sent: Ordering Temporarily Paused]",
                "[Rating poll(s) sent for: Americano, Latte]",
                "  [FAQ card sent]  "):
        assert _is_history_placeholder(bad), f"placeholder would reach the room: {bad!r}"

    for good in ("Hi there! Welcome to Dot Cafe.",
                 "Your order has been sent to the kitchen!",
                 "",
                 "We're cash only [no cards] — you pay on collection."):
        assert not _is_history_placeholder(good), f"blanked a real reply: {good!r}"
    print("[ok] history placeholders blanked, real replies untouched")


async def main():
    test_card_validates()
    test_history_placeholder_never_reaches_the_customer()
    test_seed_and_matching()
    await test_send_paths()
    print("\nall faq checks passed")


if __name__ == "__main__":
    asyncio.run(main())
