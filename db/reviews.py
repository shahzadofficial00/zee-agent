import logging
from db.connection import _get_supabase

logger = logging.getLogger(__name__)


def init_reviews_schema(cur):
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
