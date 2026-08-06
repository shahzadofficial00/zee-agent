import sqlite3
import logging
import json
from db.connection import _connect, _get_supabase

logger = logging.getLogger(__name__)


def init_orders_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        phone TEXT,
        items TEXT,
        total_amount REAL,
        status TEXT DEFAULT 'pending',
        room_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("PRAGMA table_info(orders)")
    existing_cols = [row[1] for row in cur.fetchall()]
    if "stable_order_id" not in existing_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN stable_order_id TEXT")
    if "line_items" not in existing_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN line_items TEXT")
    if "sender" not in existing_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN sender TEXT")
    if "fulfillment_method" not in existing_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN fulfillment_method TEXT")
    if "fulfillment_summary" not in existing_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN fulfillment_summary TEXT")
    # The Matrix event_id of the cancel-window countdown (JNO-85), so cancelling
    # can redact it. On the order row rather than in a dict because a restart
    # would otherwise orphan the card — the countdown outlives one process.
    if "countdown_event_id" not in existing_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN countdown_event_id TEXT")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        phone TEXT,
        reservation_time TEXT,
        guests INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)


# ─────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────
async def save_order(name, phone, items, total, line_items=None, sender=None):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO orders (customer_name, phone, items, total_amount, status, line_items, sender)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (name, phone, str(items), total, json.dumps(line_items) if line_items else None, sender))
        conn.commit()
        order_id = cur.lastrowid
        conn.close()
        return {"data": [{"id": order_id}]}
    except Exception as e:
        logger.error(f"save_order failed: {e}")
        raise


# ─────────────────────────────────────────────
# RESERVATIONS
# ─────────────────────────────────────────────
async def save_reservation(name, phone, time, guests=1):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reservations (customer_name, phone, reservation_time, guests)
            VALUES (?, ?, ?, ?)
        """, (name, phone, time, guests))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save_reservation failed: {e}")
        raise


def order_id_exists(order_id: str) -> bool:
    """Check if this stable_order_id is already taken.

    JNO-239 — this used to query Supabase payment_intents, which is write-frozen
    while cash-only (create_payment_intent short-circuits), so it could never see
    a cash order's id and the uniqueness check silently became a no-op plus a
    blocking network round-trip mid-checkout. SQLite orders.stable_order_id is
    the actual source of truth for these ids, and it's local + synchronous.
    TIP-/AUC- ids still only live in payment_intents, so they get no collision
    check here — acceptable at 36^6, and they'd need their own lookup anyway.
    """
    conn = _connect()
    hit = conn.execute(
        "SELECT 1 FROM orders WHERE stable_order_id = ? LIMIT 1", (order_id,)
    ).fetchone()
    conn.close()
    return hit is not None

# How recently an order must have been placed to still be self-cancellable.
# ponytail: a fixed window, because while cash-only NOTHING moves an order off
# 'pending' except staff running `update_order_status.py ... paid` — so a
# delivered-but-unmarked order from last week looks identical to one placed a
# minute ago, and an unbounded cancel would silently void it. Replace this with
# a real lifecycle column (or a check against fulfillment state) if orders ever
# record "preparing"/"delivered" durably.
CANCEL_WINDOW_MINUTES = 15


def cancel_last_pending_order(sender: str, room_id: str) -> str | None:
    """Cancel this customer's most recent *recent* pending order in SQLite (JNO-239).

    A cash order has NO payment_intents row, so the old Supabase-only cancel path
    couldn't see it — and worse, would match a stale pre-cash-only pending row for
    a DIFFERENT order and report "cancelled!" while the real order stood. Ordered
    by id (autoincrement, strictly monotonic) rather than created_at, which ties.
    Returns the cancelled stable_order_id, or None if there was nothing to cancel.

    Only orders placed within CANCEL_WINDOW_MINUTES qualify — see the constant.
    `created_at` is CURRENT_TIMESTAMP, which SQLite writes in UTC in the exact
    same 'YYYY-MM-DD HH:MM:SS' format datetime('now') returns, so this is a plain
    string comparison and needs no timezone handling.
    """
    conn = _connect()
    row = conn.execute(
        "SELECT stable_order_id FROM orders "
        "WHERE sender = ? AND room_id = ? AND status = 'pending' "
        f"AND created_at > datetime('now', '-{CANCEL_WINDOW_MINUTES} minutes') "
        "ORDER BY id DESC LIMIT 1",
        (sender, room_id),
    ).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute(
        "UPDATE orders SET status = 'cancelled' WHERE stable_order_id = ?", (row[0],)
    )
    conn.commit()
    conn.close()
    return row[0]


def get_orders_by_room(room_id: str, sender: str = None) -> list:
    """Orders for a room, optionally scoped to one customer's Matrix sender id."""
    import sqlite3
    conn = sqlite3.connect('restaurant.db')
    query = "SELECT id, customer_name, items, total_amount, status, created_at, stable_order_id FROM orders WHERE room_id = ?"
    params = [room_id]
    if sender:
        query += " AND sender = ?"
        params.append(sender)
    query += " ORDER BY created_at DESC"
    cursor = conn.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "customer_name": r[1],
            "items": r[2].strip("[]").replace("'", "").replace('"', ""),
            "total_amount": int(r[3]),
            "status": r[4],
            "created_at": r[5],
            "stable_order_id": r[6],
        }
        for r in rows
    ]


def update_order_room_id(order_id: str, room_id: str):
    conn = _connect()
    conn.execute("UPDATE orders SET room_id = ? WHERE id = ?", (room_id, order_id))
    conn.commit()
    conn.close()


def update_order_stable_id(order_id: str, stable_order_id: str):
    conn = _connect()
    conn.execute("UPDATE orders SET stable_order_id = ? WHERE id = ?", (stable_order_id, order_id))
    conn.commit()
    conn.close()


def mark_order_paid(stable_order_id: str) -> bool:
    """Staff confirming cash was collected (JNO-240). Keyed on stable_order_id
    because that's the only id staff ever see (ORD-XXXXXX), same as
    get_order_by_stable_id. Returns False if no such order.

    Only the PAYMENT axis is persisted here — the order_status lifecycle
    (preparing/ready/delivered) deliberately stays fire-and-forget, because
    orders.status is a single column and persisting both would have 'delivered'
    overwrite 'paid', breaking the history card's paid/pending/cancelled filter.
    """
    conn = _connect()
    cur = conn.execute(
        "UPDATE orders SET status = 'paid' WHERE stable_order_id = ?",
        (stable_order_id,),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def update_order_fulfillment(order_id: str, method: str, summary: str):
    conn = _connect()
    conn.execute(
        "UPDATE orders SET fulfillment_method = ?, fulfillment_summary = ? WHERE id = ?",
        (method, summary, order_id),
    )
    conn.commit()
    conn.close()


def get_order_by_stable_id(stable_order_id: str) -> dict | None:
    """Resolve a human-readable order_id (ORD-XXXXXX) back to its room —
    needed by the staff order_status trigger, which only ever knows the
    order_id printed on the customer's receipt, not the room it came from.

    `created_at` rides along so the cancel-window countdown (JNO-85) can be
    anchored to the row's own timestamp — the same value
    cancel_last_pending_order() compares against. Deriving the deadline from
    "now" instead would drift a second or two later than the SQL cutoff, and a
    countdown still showing time left after cancelling stopped working is
    exactly the kind of small lie worth avoiding."""
    conn = _connect()
    cur = conn.execute(
        "SELECT id, customer_name, room_id, status, created_at, countdown_event_id "
        "FROM orders WHERE stable_order_id = ?",
        (stable_order_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "customer_name": row[1], "room_id": row[2],
            "status": row[3], "created_at": row[4], "countdown_event_id": row[5]}


def set_order_countdown_event(stable_order_id: str, event_id: str) -> None:
    """Remember which Matrix event carries this order's cancel-window countdown,
    so cancelling can redact it (JNO-85)."""
    conn = _connect()
    conn.execute(
        "UPDATE orders SET countdown_event_id = ? WHERE stable_order_id = ?",
        (event_id, stable_order_id),
    )
    conn.commit()
    conn.close()
