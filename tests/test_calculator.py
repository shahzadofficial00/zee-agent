"""Self-check for the calculator card (epic JNO-233, story JNO-237).
Run: python -m tests.test_calculator

The card itself is a payload. What's worth testing is the formula gate — this
is the one place in the repo where a string written by an LLM describes
something another runtime will evaluate, so "no arbitrary code execution" has
to be true by construction, not by hope.
"""
import asyncio

# python-olm isn't installed on every dev machine. Same stub as the other suites.
import nio.store
if not hasattr(nio.store, "SqliteStore"):
    class _StubSqliteStore:
        pass
    nio.store.SqliteStore = _StubSqliteStore

from bot.dsl_validator import validate_dsl
from bot.calculator.calculator_service import (
    MAX_FIELDS, validate_fields, validate_formula, send_calculator_card,
)

ROOM = "!test:jaeno.ai"

FIELDS = [
    {"key": "guests", "label": "Number of guests", "type": "number", "min": 1, "max": 200, "required": True},
    {"key": "hours", "label": "Hours", "type": "number", "min": 1},
    {"key": "tier", "label": "Package", "type": "choice",
     "options": [{"label": "Standard", "value": 850}, {"label": "Premium", "value": 1400}]},
]
KEYS = {"guests", "hours", "tier"}


def test_formula_rejects_everything_but_arithmetic():
    assert validate_formula("guests * tier + hours * 2000", KEYS) is None
    assert validate_formula("(guests + 2) * 850 / 4", KEYS) is None
    assert validate_formula("-guests + 5", KEYS) is None

    for bad, why in (
        ('__import__("os").system("ls")', "arbitrary code execution"),
        ("guests.__class__", "attribute access"),
        ("open('/etc/passwd')", "a function call"),
        ("9 ** 9 ** 9", "exponentiation (a one-token DoS)"),
        ("guests % 3", "modulo"),
        ("[guests for guests in range(9)]", "a comprehension"),
        ("guests if hours else 0", "a conditional"),
        ("guests > 3", "a comparison"),
        ("'abc' + 'def'", "string constants"),
        ("unknown * 2", "an undeclared field"),
        ("guests / 0", "division by a literal zero"),
        ("", "an empty formula"),
        ("guests +", "a syntax error"),
    ):
        assert validate_formula(bad, KEYS) is not None, f"formula gate allowed {why}: {bad!r}"
    print(f"[ok] formula gate: 3 valid accepted, 13 unsafe/invalid rejected")


def test_field_list_bounds():
    assert validate_fields(FIELDS) is None

    nine = [{"key": f"f{i}", "label": f"F{i}", "type": "number"} for i in range(MAX_FIELDS + 1)]
    assert validate_fields(nine) is not None, f"accepted {MAX_FIELDS + 1} fields"
    assert validate_fields(nine[:MAX_FIELDS]) is None, f"rejected exactly {MAX_FIELDS} fields"

    for bad, why in (
        ([], "an empty field list"),
        ([{"key": "2x", "label": "L", "type": "number"}], "a key that isn't an identifier"),
        ([{"key": "import", "label": "L", "type": "number"}], "a keyword as a key"),
        ([{"key": "a", "label": "L", "type": "number"},
          {"key": "a", "label": "L2", "type": "number"}], "a duplicate key"),
        ([{"key": "a", "label": "", "type": "number"}], "a field with no label"),
        ([{"key": "a", "label": "L", "type": "text"}], "an unsupported field type"),
        ([{"key": "a", "label": "L", "type": "choice"}], "a choice field with no options"),
        ([{"key": "a", "label": "L", "type": "choice",
           "options": [{"label": "X", "value": "850"}]}], "a non-numeric option value"),
        ([{"key": "a", "label": "L", "type": "choice",
           "options": [{"label": "X", "value": True}]}], "a bool option value"),
        # Observed live: the model had real menu prices in context and still
        # asked the customer to type one, so 20 coffees quoted as 40 PKR.
        ([{"key": "item_price", "label": "Price per item", "type": "number"}],
         "a customer-entered price (key and label)"),
        ([{"key": "amt", "label": "Cost per head", "type": "number"}],
         "a customer-entered cost (label only)"),
        ([{"key": "rate_per_box", "label": "How many?", "type": "number"}],
         "a customer-entered rate (key only)"),
    ):
        assert validate_fields(bad) is not None, f"field gate allowed {why}"

    # The guard is a word list, so it must not swallow legitimate quantities.
    # "crates" contains "rate" and "Feet" contains "fee" — a substring test
    # rejected both, which is why this matches on word boundaries.
    for good in (
        [{"key": "guests", "label": "Number of guests", "type": "number"}],
        [{"key": "hours", "label": "Hours", "type": "number"}],
        [{"key": "boxes", "label": "How many boxes?", "type": "number"}],
        [{"key": "crates", "label": "How many crates?", "type": "number"}],
        [{"key": "feet", "label": "Feet of decking", "type": "number"}],
        # The RATE belongs here — as a choice option value, not an input.
        [{"key": "drink", "label": "Which drink?", "type": "choice",
          "options": [{"label": "Latte", "value": 30}]}],
    ):
        assert validate_fields(good) is None, f"field gate rejected a valid field: {good}"
    print(f"[ok] field gate: cap of {MAX_FIELDS} holds, 12 bad lists rejected, "
          "6 valid ones (incl. crates/feet) accepted")


def test_card_validates():
    ok, err = validate_dsl({
        "v": 1, "type": "calculator",
        "data": {"title": "Catering estimate", "fields": FIELDS,
                 "formula": "guests * tier", "result_label": "Estimated total"},
    })
    assert ok, f"minimal calculator rejected: {err}"

    ok, err = validate_dsl({
        "v": 1, "type": "calculator",
        "data": {"title": "T", "subtitle": "S", "fields": FIELDS, "formula": "guests",
                 "result_label": "R", "result_unit": "PKR"},
    })
    assert ok, f"full calculator rejected: {err}"

    base = {"title": "T", "fields": FIELDS, "formula": "guests", "result_label": "R"}
    for drop in ("title", "fields", "formula", "result_label"):
        bad = {k: v for k, v in base.items() if k != drop}
        ok, _ = validate_dsl({"v": 1, "type": "calculator", "data": bad})
        assert not ok, f"schema accepted a calculator with no {drop}"

    # The schema is the second copy of the cap. quicktype drops maxItems from
    # the generated Dart model, so this proves only the Python gate — the client
    # enforces its own inside render().
    nine = [{"key": f"f{i}", "label": f"F{i}", "type": "number"} for i in range(MAX_FIELDS + 1)]
    ok, _ = validate_dsl({"v": 1, "type": "calculator",
                          "data": {**base, "fields": nine}})
    assert not ok, f"schema accepted {MAX_FIELDS + 1} fields"
    print("[ok] calculator schema: required keys enforced, maxItems holds")


async def test_sender_blocks_unsafe_and_omits_empty_optionals():
    from bot.calculator import calculator_service

    sent = []

    async def fake(room_id, message_type=None, content=None, **kw):
        sent.append(content)
        return type("R", (), {"event_id": "$evt1"})()

    original = calculator_service.matrix_client.room_send
    calculator_service.matrix_client.room_send = fake
    try:
        ok = await send_calculator_card(
            ROOM, title="Catering estimate", fields=FIELDS,
            formula="guests * tier + hours * 2000", result_label="Estimated total",
        )
        assert ok, "valid calculator was not sent"
        data = sent[0]["ai.jaeno.dsl"]["data"]
        assert "subtitle" not in data and "result_unit" not in data, \
            f"empty optionals were sent as empty strings: {data}"

        before = len(sent)
        blocked = await send_calculator_card(
            ROOM, title="Evil", fields=FIELDS,
            formula='__import__("os").system("ls")', result_label="Total",
        )
        assert not blocked and len(sent) == before, "an unsafe formula reached room_send"

        blocked = await send_calculator_card(
            ROOM, title="Too many", result_label="Total", formula="f0",
            fields=[{"key": f"f{i}", "label": f"F{i}", "type": "number"} for i in range(MAX_FIELDS + 1)],
        )
        assert not blocked and len(sent) == before, "an over-cap field list reached room_send"
    finally:
        calculator_service.matrix_client.room_send = original
    print("[ok] sender: valid card sent, empty optionals omitted, unsafe payloads blocked")


async def test_result_offers_the_order_and_yes_runs_it():
    """JNO-236 — the result is answered deterministically: summarise, offer the
    order, and let a plain "yes" run the ordinary order flow. It never reaches
    the LLM, which used to answer a calculator with another calculator."""
    from bot.router import dsl_text_events as dte
    from bot.router.dsl_text_events import handle_dsl_text_event
    from bot.router.state import awaiting_calculator_order
    from agent import state as agent_state

    agent_state.MENU_PRICES.clear()
    agent_state.MENU_PRICES.update({"latte": 30, "americano": 40})

    user = "@x:jaeno.ai"
    sent = []

    async def fake_send_text(room_id, text):
        sent.append(text)

    original = dte.send_text
    dte.send_text = fake_send_text
    try:
        handled, message = await handle_dsl_text_event({
            "v": 1, "type": "calculator_result",
            "data": {"title": "Coffee Estimate",
                     "inputs": [{"label": "Number of guests", "value": "20"},
                                {"label": "Which drink?", "value": "Americano"}],
                     "result_label": "Estimated total", "result_value": "800",
                     "result_unit": "PKR"},
        }, user, ROOM)

        assert handled is True, "the result was handed to the LLM again"
        assert message is None
        assert awaiting_calculator_order.get(user) == "I want to order: Americano x20", \
            f"wrong order line: {awaiting_calculator_order.get(user)!r}"
        assert "800" in sent[0] and "Americano" in sent[0], sent
        assert "Shall I place that order" in sent[0], sent

        # A calculator with nothing orderable in it must not invent an order.
        # "Premium" is close enough to "Spanish Latte Premium" that a fuzzy
        # match would turn a generic tier into a coffee order — hence exact.
        agent_state.MENU_PRICES["spanish latte premium"] = 1999
        awaiting_calculator_order.pop(user, None)
        sent.clear()
        handled, _ = await handle_dsl_text_event({
            "v": 1, "type": "calculator_result",
            "data": {"title": "Solar savings",
                     "inputs": [{"label": "Panels", "value": "12"},
                                {"label": "Tier", "value": "Premium"}],
                     "result_label": "Estimated savings", "result_value": "58000"},
        }, user, ROOM)
        assert handled is True
        assert user not in awaiting_calculator_order, \
            "invented an order for something that isn't on the menu"

        # Untrusted input: a hostile payload must not produce an unbounded message.
        sent.clear()
        await handle_dsl_text_event({
            "v": 1, "type": "calculator_result",
            "data": {"title": "T" * 5000,
                     "inputs": [{"label": "L" * 5000, "value": "V" * 5000}] * 50,
                     "result_value": "9" * 5000},
        }, user, ROOM)
        assert len(sent[0]) < 1000, f"unbounded calculator_result text: {len(sent[0])} chars"
    finally:
        dte.send_text = original
        awaiting_calculator_order.pop(user, None)
        agent_state.MENU_PRICES.clear()
    print("[ok] result summarised, order offered from real menu rows, junk ignored")


async def test_inbound_result_cannot_derail_a_checkout():
    """An old card is tappable forever from scrollback. Falling through while a
    checkout waits on freeform text would land the estimate as the customer's
    name or phone number — and save_customer() would persist it."""
    from bot.router import dsl_text_events as dte
    from bot.router.dsl_text_events import handle_dsl_text_event

    payload = {
        "v": 1, "type": "calculator_result",
        "data": {"title": "Catering estimate", "inputs": [{"label": "Guests", "value": "40"}],
                 "result_label": "Estimated total", "result_value": "58000", "result_unit": "PKR"},
    }
    user = "@mid:jaeno.ai"
    sent = []

    async def fake_send_text(room_id, text):
        sent.append(text)

    original = dte.send_text
    dte.send_text = fake_send_text
    try:
        for stage, expect in (
            ("await_name", "name"),
            ("await_phone", "phone"),
            ("final_confirm", "finish the order"),
        ):
            sent.clear()
            dte.order_flows[user] = {"stage": stage}
            handled, message = await handle_dsl_text_event(payload, user, ROOM)
            assert handled is True, f"calculator_result fell through at stage {stage!r}"
            assert message is None
            assert len(sent) == 1 and expect in sent[0].lower(), \
                f"stage {stage!r} got an unhelpful reply: {sent}"
            assert "58000" in sent[0], f"stage {stage!r} dropped the estimate: {sent}"

        # No flow → the normal path: summarise and offer the order.
        dte.order_flows.pop(user, None)
        sent.clear()
        handled, message = await handle_dsl_text_event(payload, user, ROOM)
        assert handled is True and "58000" in sent[0]
        assert "finish the order" not in sent[0].lower(), \
            "the mid-checkout nudge fired with no checkout in progress"
    finally:
        dte.send_text = original
        dte.order_flows.pop(user, None)
        dte.awaiting_calculator_order.pop(user, None)
    print("[ok] mid-checkout taps are acknowledged without corrupting the order flow")


async def test_dispatch_never_writes_a_label_into_history():
    """The third outing for this bug class. clean_reply is re-injected into the
    next agent call, so the model imitates it — writing the card TITLE there
    ("Catering Estimate") taught it to answer catering questions with those two
    words instead of calling the tool, inside three turns. Only a sentence is
    safe; "" is not either, since message_handler turns it into "Got it!"."""
    from bot.router import agent_dispatch as ad
    from bot.calculator import calculator_service as cs

    # The rule applies to EVERY card-only branch, not just this one. Checked at
    # source level because that catches branches this test doesn't drive:
    # poll-history and banner both shipped `clean_reply = ""`, and live that
    # made "Send poll history" answer "Got it!", which the summariser then
    # hardened into "poll history is not a supported functionality".
    import inspect, re as _re
    src = inspect.getsource(ad)
    assert not _re.search(r'clean_reply\s*=\s*([\'"])\1\s*$', src, _re.M), \
        'a dispatch branch assigns an empty clean_reply — message_handler turns that into "Got it!"'

    async def fake_send(room_id, message_type=None, content=None, **kw):
        return type("R", (), {"event_id": "$e"})()

    async def no_typing(*a, **kw):
        pass

    class _Msg:
        def __init__(self, content):
            self.content = content

    signal = "CALC_TRIGGERED|" + __import__("json").dumps({
        "title": "Catering Estimate", "fields": FIELDS,
        "formula": "guests * tier", "result_label": "Estimated total",
    })

    originals = (cs.matrix_client.room_send, ad.matrix_client.room_send, ad.matrix_client.room_typing)
    cs.matrix_client.room_send = fake_send
    ad.matrix_client.room_send = fake_send
    ad.matrix_client.room_typing = no_typing
    try:
        # The model wrote a lead-in — keep it, it's a real sentence.
        lead_in = "I can help with that! Fill this in for an estimate."
        reply = await ad._dispatch_agent_result(
            {"messages": [_Msg(signal), _Msg(lead_in)]}, ROOM, "@u:jaeno.ai")
        assert reply == lead_in, f"model's own sentence was discarded: {reply!r}"

        # The model wrote nothing — must still be a sentence, never the title
        # and never "" (which becomes "Got it!" one layer up).
        reply = await ad._dispatch_agent_result(
            {"messages": [_Msg(signal), _Msg("")]}, ROOM, "@u:jaeno.ai")
        assert reply, "empty clean_reply becomes the literal 'Got it!' in history"
        assert reply != "Catering Estimate", "the card title landed in history again"
        assert " " in reply.strip(), f"clean_reply is a label, not a sentence: {reply!r}"
    finally:
        cs.matrix_client.room_send, ad.matrix_client.room_send, ad.matrix_client.room_typing = originals
    print("[ok] dispatch writes a sentence to history, never the card title or an empty string")


MENU = [
    {"name": "Espresso", "price": 10, "category": "Hot Classics"},
    {"name": "Latte", "price": 30, "category": "Hot Classics"},
    {"name": "Americano", "price": 40, "category": "Hot Classics"},
    {"name": "Mango Smoothie", "price": 999, "category": "Cold Drinks"},
    {"name": "Berry Mojito", "price": 666, "category": "Cold Drinks"},
]


def _with_menu(menu):
    """Swap db.get_menu_items for a fixed list. Returns a restore callable."""
    import db
    original = db.get_menu_items

    async def fake():
        return list(menu)

    db.get_menu_items = fake
    return lambda: setattr(db, "get_menu_items", original)


async def test_options_come_from_the_menu_not_the_model():
    """The model names categories; the server builds the options. It kept
    padding a coffee estimate with smoothies and mojitos when it wrote the list
    itself, and no prompt wording stopped it — so it no longer writes the list."""
    from agent.tools.calculator.send_calculator import send_calculator
    import json as _json

    restore = _with_menu(MENU)
    try:
        out = await send_calculator.ainvoke({
            "title": "Coffee Estimate", "quantity_label": "Number of guests",
            "categories": ["Hot Classics"], "choice_label": "Which drink?",
        })
        assert out.startswith("CALC_TRIGGERED|"), out
        data = _json.loads(out.split("|", 1)[1])
        options = data["fields"][1]["options"]

        labels = [o["label"] for o in options]
        assert labels == ["Espresso", "Latte", "Americano"], f"wrong set, cheapest-first: {labels}"
        assert "Mango Smoothie" not in labels and "Berry Mojito" not in labels, \
            "an un-asked-for category leaked into the options"
        assert [o["value"] for o in options] == [10, 30, 40], "prices are not the menu's"
        assert data["formula"] == "quantity * choice"

        # Two categories → both, still cheapest-first across the merged set.
        out = await send_calculator.ainvoke({
            "title": "Drinks", "quantity_label": "How many?",
            "categories": ["Hot Classics", "Cold Drinks"],
        })
        data = _json.loads(out.split("|", 1)[1])
        assert len(data["fields"][1]["options"]) == 5

        # There is no single-item mode, and adding one back must fail here.
        # Observed live: item_name="Latte" answered "calculate 20 coffee" with
        # `quantity * 30` and the item named nowhere on the card, so it priced
        # every coffee at Latte's 30 AND echoed back no item — which left
        # _derive_order_from_inputs empty and sent the customer's "yes" to the
        # LLM, which replied with a second calculator.
        assert "item_name" not in send_calculator.args, \
            "single-item mode is back — it lets the model narrow a category to one price"

        # Every card is a category card: quantity + a real picker, always.
        for kwargs in (
            {"categories": ["Hot Classics"]},
            {"categories": ["Hot Classics", "Cold Drinks"]},
        ):
            data = _json.loads((await send_calculator.ainvoke(
                {"title": "T", "quantity_label": "n", **kwargs})).split("|", 1)[1])
            assert data["formula"] == "quantity * choice", data["formula"]
            choice = next(f for f in data["fields"] if f["type"] == "choice")
            # Labels are what come back in `inputs`, so the order path sees them.
            assert {o["label"].lower() for o in choice["options"]} <= \
                {m["name"].lower() for m in MENU}, choice

        # No categories at all → the recoverable marker, never a card.
        out = await send_calculator.ainvoke({"title": "X", "quantity_label": "n"})
        assert out.startswith("CALC_NO_CATEGORY|") and "Hot Classics" in out, out
        # Unknown category → same, with the valid names to retry from.
        out = await send_calculator.ainvoke({
            "title": "X", "quantity_label": "n", "categories": ["Pastries"]})
        assert out.startswith("CALC_NO_CATEGORY|") and "Hot Classics" in out, out
    finally:
        restore()

    restore = _with_menu([])
    try:
        out = await send_calculator.ainvoke({
            "title": "X", "quantity_label": "n", "categories": ["Hot Classics"]})
        assert out == "CALC_MENU_UNAVAILABLE", out
    finally:
        restore()
    print("[ok] options are built server-side from the real menu, by category")


async def test_menu_prices_groups_by_category():
    """send_calculator only accepts categories spelled as the menu spells them,
    so this is where the model learns the exact names."""
    from agent.tools.menu.get_menu_prices import get_menu_prices

    restore = _with_menu(MENU)
    try:
        out = await get_menu_prices.ainvoke({})
        assert "Hot Classics: Espresso 10, Latte 30, Americano 40" in out, out
        assert "Cold Drinks: Berry Mojito 666, Mango Smoothie 999" in out, out
    finally:
        restore()

    restore = _with_menu([])
    try:
        out = await get_menu_prices.ainvoke({})
        assert out == "MENU_PRICES_UNAVAILABLE", \
            "an unreachable menu must say so, not return '' the model reads as free"
    finally:
        restore()
    print("[ok] get_menu_prices groups by category and flags an unreachable menu")


async def test_yes_after_the_offer_runs_the_deterministic_order_flow():
    """The whole point: "yes" places the order through the same code path as
    any other order — no LLM rebuilding the item list from chat history."""
    import bot.message_handler as mh
    from bot.router.state import awaiting_calculator_order, order_flows

    user = "@y:jaeno.ai"
    started = []

    async def fake_start(sender, room_id, message):
        started.append(message)
        return True

    async def no_typing(*a, **kw):
        pass

    originals = (mh._start_order_flow, mh.matrix_client.room_typing)
    mh._start_order_flow = fake_start
    mh.matrix_client.room_typing = no_typing

    class _Event:
        body = "yes"
        event_id = "$evt_yes"
        server_timestamp = 9_999_999_999_999
        sender = user
        source = {"content": {}}

    class _Room:
        room_id = ROOM

    try:
        awaiting_calculator_order[user] = "I want to order: Americano x20"
        await mh._handle_message(_Room(), _Event())
        assert started == ["I want to order: Americano x20"], \
            f"'yes' did not run the deterministic order flow: {started}"
        assert user not in awaiting_calculator_order, "the offer was not cleared"
    finally:
        mh._start_order_flow, mh.matrix_client.room_typing = originals
        awaiting_calculator_order.pop(user, None)
        order_flows.pop(user, None)
    print("[ok] 'yes' runs the ordinary order flow with the pre-built item line")


def main():
    test_formula_rejects_everything_but_arithmetic()
    test_field_list_bounds()
    test_card_validates()
    asyncio.run(test_sender_blocks_unsafe_and_omits_empty_optionals())
    asyncio.run(test_result_offers_the_order_and_yes_runs_it())
    asyncio.run(test_inbound_result_cannot_derail_a_checkout())
    asyncio.run(test_dispatch_never_writes_a_label_into_history())
    asyncio.run(test_options_come_from_the_menu_not_the_model())
    asyncio.run(test_menu_prices_groups_by_category())
    asyncio.run(test_yes_after_the_offer_runs_the_deterministic_order_flow())
    print("\nAll calculator checks passed (10).")


if __name__ == "__main__":
    main()
