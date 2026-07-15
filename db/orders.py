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


async def order_id_exists(order_id: str) -> bool:
    """Check if this order_id is already used in payment_intents (Supabase)."""
    try:
        db = await _get_supabase()
        result = (
            await db.table("payment_intents")
            .select("order_id")
            .eq("order_id", order_id)
            .limit(1)
            .execute()
        )
        return len(result.data) > 0
    except Exception as e:
        logger.error(f"order_id_exists failed: {e}")
        return False

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
    order_id printed on the customer's receipt, not the room it came from."""
    conn = _connect()
    cur = conn.execute(
        "SELECT id, customer_name, room_id, status FROM orders WHERE stable_order_id = ?",
        (stable_order_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "customer_name": row[1], "room_id": row[2], "status": row[3]}
