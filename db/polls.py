import logging
import json
from db.connection import _connect

logger = logging.getLogger(__name__)


def init_polls_schema(cur):
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
    cur.execute("PRAGMA table_info(polls)")
    existing_poll_cols = [row[1] for row in cur.fetchall()]
    if "poll_id" not in existing_poll_cols:
        cur.execute("ALTER TABLE polls ADD COLUMN poll_id TEXT")
    if "poll_type" not in existing_poll_cols:
        cur.execute("ALTER TABLE polls ADD COLUMN poll_type TEXT DEFAULT 'single_choice'")


    cur.execute("""
    CREATE TABLE IF NOT EXISTS poll_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        poll_event_id TEXT NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        poll_type TEXT DEFAULT 'single_choice',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        menu_item TEXT NOT NULL,
        rating INTEGER NOT NULL,
        order_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)


def save_poll(room_id: str, event_id: str, question: str, options: list[str], multi_select: bool = False, poll_id: str = None, poll_type: str = 'single_choice'):
    conn = _connect()
    conn.execute(
        "INSERT INTO polls (room_id, event_id, question, options, multi_select, created_at, poll_id, poll_type) VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)",
        (room_id, event_id, question, json.dumps(options), int(multi_select), poll_id, poll_type),
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


def get_poll_by_poll_id(poll_id: str):
    """Look up a poll by its DSL poll_id (the uuid sent to the client), used to
    resolve the real poll_type when a ai.jaeno.poll_response event comes back."""
    conn = _connect()
    row = conn.execute(
        "SELECT room_id, question, options, multi_select, poll_type FROM polls WHERE poll_id = ?",
        (poll_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "room_id": row["room_id"],
        "question": row["question"],
        "options": json.loads(row["options"]),
        "multi_select": bool(row["multi_select"]),
        "poll_type": row["poll_type"] or "single_choice",
    }


async def save_item_rating(room_id: str, sender: str, menu_item: str, rating: int, order_id: str = None) -> bool:
    """Save a star rating for a specific menu item to SQLite. Returns True on success."""
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO item_ratings (room_id, sender, menu_item, rating, order_id) VALUES (?, ?, ?, ?, ?)",
            (room_id, sender, menu_item, rating, order_id),
        )
        conn.commit()
        conn.close()
        logger.info(f"⭐ Item rating saved: {menu_item} = {rating} stars by {sender}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to save item rating: {e}", exc_info=True)
        return False


def get_item_rating_summary(menu_item: str) -> dict | None:
    """Average rating + total count for a menu item across ALL customers who've
    rated it, regardless of room — used for the "how did others rate this"
    aggregate card shown right after a customer submits their own rating."""
    conn = _connect()
    row = conn.execute(
        "SELECT AVG(rating) AS average, COUNT(*) AS total FROM item_ratings WHERE menu_item = ?",
        (menu_item,),
    ).fetchone()
    conn.close()
    if not row or not row["total"]:
        return None
    return {"average": round(row["average"], 1), "total_votes": row["total"]}


def get_all_item_rating_summaries() -> dict:
    """Every rated item's aggregate, keyed by menu_item — one query for the whole
    table. The menu/category cards need a rating for every item they render, and
    calling get_item_rating_summary() per item meant one SQLite connection per
    menu item, synchronously, on the asyncio loop (so the bot stalled for every
    other customer while a single menu card was being built).

    Items nobody has rated are simply absent from the dict.
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT menu_item, ROUND(AVG(rating), 1) AS average, COUNT(*) AS total "
        "FROM item_ratings GROUP BY menu_item"
    ).fetchall()
    conn.close()
    return {
        r["menu_item"]: {"average": r["average"], "total_votes": r["total"]}
        for r in rows
    }


def save_poll_answer(room_id: str, sender: str, poll_event_id: str, question: str, answer: str, poll_type: str = 'single_choice'):
    """Save a customer's poll answer to SQLite."""
    conn = _connect()
    conn.execute(
        "INSERT INTO poll_answers (room_id, sender, poll_event_id, question, answer, poll_type) VALUES (?, ?, ?, ?, ?, ?)",
        (room_id, sender, poll_event_id, question, answer, poll_type)
    )
    conn.commit()
    conn.close()


def get_poll_answers(room_id: str, sender: str) -> list:
    """Get all poll answers for a customer in a room."""
    conn = _connect()
    rows = conn.execute(
        "SELECT question, answer, poll_type, created_at FROM poll_answers WHERE room_id = ? AND sender = ? ORDER BY created_at DESC",
        (room_id, sender)
    ).fetchall()
    conn.close()
    return [
        {
            "question": row[0],
            "answer": row[1],
            "poll_type": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]
