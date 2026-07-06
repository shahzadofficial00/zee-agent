import sqlite3
import logging
import difflib
from datetime import datetime

logger = logging.getLogger(__name__)


def fuzzy_match_key(name: str, candidates: list[str], cutoff: float = 0.75) -> str | None:
    """Match a (possibly misspelled) name against known keys. Exact match wins;
    otherwise falls back to the closest candidate within the similarity cutoff."""
    name = name.lower().strip()
    if name in candidates:
        return name
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None

DB_PATH = "restaurant.db"


# ─────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
# INIT
# ─────────────────────────────────────────────
def init_db():
    conn = _connect()
    cur = conn.cursor()

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        room_id TEXT,
        menu_item TEXT,
        order_id TEXT,
        rating INTEGER,
        comment TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS review_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id TEXT,
        order_id TEXT,
        menu_item TEXT,
        send_at TEXT,
        sent INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price REAL,
        image TEXT,
        available INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cur.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('ordering_enabled', '1')
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_ordering (
        item_name TEXT PRIMARY KEY,
        orderable INTEGER DEFAULT 1
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        multi_select BOOLEAN DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────
async def save_order(name, phone, items, total):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO orders (customer_name, phone, items, total_amount, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (name, phone, str(items), total))
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


# ─────────────────────────────────────────────
# MENU
# ─────────────────────────────────────────────
async def get_menu_items():
    try:
        db = await _get_supabase()
        result = (
            await db.table("menu_items")
            .select("id, name, price, image, category")
            .eq("available", True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_menu_items failed: {e}")
        return []


# ─────────────────────────────────────────────
# REVIEWS
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# REVIEWS — SUPABASE
# ─────────────────────────────────────────────
async def save_review(user_id, room_id, menu_item, order_id, rating, comment):
    try:
        db = await _get_supabase()
        result = (
            await db.table("reviews")
            .insert({
                "user_id": user_id,
                "room_id": room_id,
                "menu_item": menu_item,
                "order_id": order_id,
                "rating": rating,
                "comment": comment,
            })
            .execute()
        )
        return result
    except Exception as e:
        logger.error(f"save_review failed: {e}")
        raise


# ─────────────────────────────────────────────
# REVIEW QUEUE
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# REVIEW QUEUE — SUPABASE
# ─────────────────────────────────────────────
async def insert_review_queue(room_id: str, order_id: str, menu_item: str, send_at):
    try:
        db = await _get_supabase()
        existing = (
            await db.table("review_queue")
            .select("id")
            .eq("order_id", order_id)
            .eq("sent", False)
            .execute()
        )
        if existing.data:
            logger.info(f"Review already queued for {order_id}, skipping")
            return

        await db.table("review_queue").insert({
            "room_id": room_id,
            "order_id": order_id,
            "menu_item": menu_item,
            "send_at": send_at.isoformat() if send_at else None,
            "sent": False,
        }).execute()
    except Exception as e:
        logger.error(f"insert_review_queue failed: {e}")
        raise


async def get_pending_review_queue():
    try:
        db = await _get_supabase()
        result = await db.table("review_queue").select("*").eq("sent", False).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"get_pending_review_queue failed: {e}")
        return []


async def mark_review_sent(review_id: str):
    try:
        db = await _get_supabase()
        await db.table("review_queue").update({"sent": True}).eq("id", review_id).execute()
    except Exception as e:
        logger.error(f"mark_review_sent failed: {e}")
        raise


# ─────────────────────────────────────────────
# PAYMENTS — STAYS IN SUPABASE
# ─────────────────────────────────────────────
from supabase import acreate_client
import os
from dotenv import load_dotenv
load_dotenv()

async def _get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL or SUPABASE_KEY missing")
    return await acreate_client(url, key)


async def save_payment(order_id, sender, room_id, amount, customer_name, phone):
    logger.info(f"save_payment called for {order_id} — row already exists via Edge Function, skipping.")


async def get_payment(sender: str, order_id: str):
    try:
        db = await _get_supabase()
        result = (
            await db.table("payment_intents")
            .select("*")
            .eq("user_id", sender)
            .eq("order_id", order_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        row = result.data[0] if result.data else None
        if row:
            row["sender"] = row.get("user_id")
        return row
    except Exception as e:
        logger.error(f"get_payment failed: {e}")
        return None


async def update_payment_status(order_id: str, status: str, sender: str | None = None):
    try:
        db = await _get_supabase()
        query = (
            db.table("payment_intents")
            .update({
                "status": status,
                "updated_at": datetime.utcnow().isoformat(),
            })
            .eq("order_id", order_id)
        )
        if sender:
            query = query.eq("user_id", sender)
        await query.execute()
    except Exception as e:
        logger.error(f"update_payment_status failed: {e}")
        raise


async def get_pending_payment_by_user(sender: str, room_id: str):
    try:
        db = await _get_supabase()
        result = (
            await db.table("payment_intents")
            .select("*")
            .eq("user_id", sender)
            .eq("room_id", room_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"get_pending_payment_by_user failed: {e}")
        return None
    
    



async def get_payment_statuses(stable_order_ids: list[str]) -> dict:
    if not stable_order_ids:
        return {}
    try:
        db = await _get_supabase()
        result = (
            await db.table("payment_intents")
            .select("order_id, status")
            .in_("order_id", stable_order_ids)
            .execute()
        )
        return {row["order_id"]: row["status"] for row in (result.data or [])}
    except Exception as e:
        logger.error(f"get_payment_statuses failed: {e}")
        return {}
    

    
    
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
        
def get_orders_by_room(room_id: str) -> list:
    import sqlite3
    conn = sqlite3.connect('restaurant.db')
    cursor = conn.execute(
        "SELECT id, customer_name, items, total_amount, status, created_at, stable_order_id FROM orders WHERE room_id = ? ORDER BY created_at DESC",
        (room_id,)
    )
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
    
    
    
    
    
    
    
    # ─────────────────────────────────────────────
# ORDERING SWITCHES (JNO-133 / JNO-134)
# ─────────────────────────────────────────────
def get_ordering_enabled() -> bool:
    from agent.ordering_config import ORDERING_ENABLED
    return ORDERING_ENABLED


def get_item_orderable(item_name: str) -> bool:
    from agent.ordering_config import ITEM_ORDERABLE_OVERRIDES
    overrides = {k.lower().strip(): v for k, v in ITEM_ORDERABLE_OVERRIDES.items()}
    match = fuzzy_match_key(item_name, list(overrides.keys()))
    return overrides[match] if match else True


def get_all_item_orderable() -> dict:
    """Returns {item_name_lower: bool} for every item with an explicit override."""
    from agent.ordering_config import ITEM_ORDERABLE_OVERRIDES
    return {k.lower().strip(): v for k, v in ITEM_ORDERABLE_OVERRIDES.items()}



import json

def save_poll(room_id: str, event_id: str, question: str, options: list[str], multi_select: bool = False):
    conn = _connect()
    conn.execute(
        "INSERT INTO polls (room_id, event_id, question, options, multi_select, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (room_id, event_id, question, json.dumps(options), int(multi_select)),
    )
    conn.commit()
    conn.close()


def get_poll_by_event_id(event_id: str):
    conn = _connect()
    row = conn.execute(
        "SELECT room_id, question, options, multi_select FROM polls WHERE event_id = ?",
        (event_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "room_id": row["room_id"],
        "question": row["question"],
        "options": json.loads(row["options"]),
        "multi_select": bool(row["multi_select"]),
    }