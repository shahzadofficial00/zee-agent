import logging
from datetime import datetime
from db.connection import _get_supabase

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# PAYMENTS — STAYS IN SUPABASE
# ─────────────────────────────────────────────
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
