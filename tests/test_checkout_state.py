"""Checkout state survives a restart (gap #5). Run: python -m tests.test_checkout_state

The four things that actually break this:
  1. a mid-checkout flow round-trips through SQLite (the whole point);
  2. a completed/cancelled checkout drops its row instead of leaving a blob
     that would resurrect a finished order on the next restart;
  3. a stale flow does NOT come back — resuming a two-day-old "what size?" is
     worse than starting fresh;
  4. the hook is actually wired into _run_serialized. The load/persist pair is
     useless if nothing calls it, and that's a silent failure at runtime.
"""
import inspect
import os
import tempfile

import db.connection as conn_mod

_tmp = os.path.join(tempfile.mkdtemp(), "test.db")
conn_mod.DB_PATH = _tmp

from db import init_db, save_checkout_state  # noqa: E402  (must follow the DB_PATH swap)
import bot.router.state as st  # noqa: E402

SENDER = "@check:jaeno.ai"

# A flow parked at the stage where losing it costs the most: name and phone
# collected, fulfillment settled, one "Yes" away from being a real order.
FLOW = {
    "distinct_items": ["Latte", "Espresso"],
    "qtys": {"Latte": 2, "Espresso": 1},
    "stage": "final_confirm",
    "index": 2,
    "sizes": {"Latte": "Large", "Espresso": "Small"},
    "instructions": {"Latte": "extra hot", "Espresso": ""},
    "order_id": "ORD-AB12CD",
    "customer_name": "Ayesha",
    "customer_phone": "03001234567",
    "fulfillment_method": "delivery",
    "fulfillment_summary": "123 Main St, Lahore",
}


def _wipe_memory():
    """Simulate a bot restart: every in-memory dict empty, nothing loaded yet."""
    st.order_flows.clear()
    st.last_order_line.clear()
    st.last_order_state.clear()
    st.last_orders.clear()
    st.pending_orders.clear()
    st.awaiting_calculator_order.clear()
    st.awaiting_reorder_confirmation.clear()
    st._checkout_loaded.clear()
    st._checkout_on_disk.clear()


def main():
    init_db()

    # ── 1. round-trip ────────────────────────────────────────────────────────
    _wipe_memory()
    st.ensure_checkout_loaded(SENDER)
    st.order_flows[SENDER] = dict(FLOW)
    st.pending_orders[SENDER] = "I want to order: Latte x2, Espresso x1"
    st.awaiting_reorder_confirmation.add(SENDER)
    st.persist_checkout(SENDER)

    _wipe_memory()
    st.ensure_checkout_loaded(SENDER)
    restored = st.order_flows.get(SENDER)
    assert restored == FLOW, f"flow did not round-trip: {restored}"
    assert st.pending_orders.get(SENDER) == "I want to order: Latte x2, Espresso x1"
    assert SENDER in st.awaiting_reorder_confirmation, "reorder flag lost"
    print("[1/4] a mid-checkout flow survives a restart")

    # ── 2. completed checkout drops its row ──────────────────────────────────
    # _place_deterministic_order pops order_flows; the next persist must clear
    # the row, or the restart after that re-parks an order already placed.
    st.order_flows.pop(SENDER)
    st.pending_orders.pop(SENDER)
    st.awaiting_reorder_confirmation.discard(SENDER)
    st.persist_checkout(SENDER)

    _wipe_memory()
    st.ensure_checkout_loaded(SENDER)
    assert SENDER not in st.order_flows, "placed order came back from the dead"
    print("[2/4] a finished checkout leaves no row behind")

    # ── 3. stale flows stay dead ─────────────────────────────────────────────
    save_checkout_state(SENDER, {"order_flow": dict(FLOW)})
    conn = conn_mod._connect()
    conn.execute(
        "UPDATE checkout_state SET updated_at = datetime('now', '-2 days') WHERE user_id = ?",
        (SENDER,),
    )
    conn.commit()
    conn.close()

    _wipe_memory()
    st.ensure_checkout_loaded(SENDER)
    assert SENDER not in st.order_flows, "a two-day-old checkout was resumed"
    print("[3/4] a checkout past CHECKOUT_TTL_MINUTES is not restored")

    # ── 4. the hook is wired ─────────────────────────────────────────────────
    # Source-level, like test_calculator's clean_reply check: the pair above can
    # be perfect and still never run. Must be inside _run_serialized, which is
    # the one place holding the per-sender lock.
    import bot.message_handler as mh
    src = inspect.getsource(mh._run_serialized)
    assert "ensure_checkout_loaded" in src, "_run_serialized never loads checkout state"
    assert "persist_checkout" in src, "_run_serialized never saves checkout state"
    assert "finally:" in src, "persist must be in finally — a crashed turn loses the flow"
    print("[4/4] _run_serialized loads and saves under the lock")

    print("\nAll 4 checks passed")


if __name__ == "__main__":
    main()
