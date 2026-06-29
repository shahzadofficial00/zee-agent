import asyncio
import logging
from datetime import datetime, timezone, timedelta
from db import get_pending_review_queue, mark_review_sent, insert_review_queue
from bot.review_service import send_review_card

logger = logging.getLogger(__name__)

async def schedule_review(room_id: str, order_id: str, menu_item: str, delay_seconds: int = 120):
    send_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    await insert_review_queue(room_id=room_id, order_id=order_id, menu_item=menu_item, send_at=send_at)
    logger.info(f"📅 Review scheduled for order {order_id} at {send_at}")

async def run_review_scheduler():
    logger.info("🔁 Review scheduler started")
    while True:
        try:
            from config import REVIEW_CARD_ENABLED
            if not REVIEW_CARD_ENABLED:
                await asyncio.sleep(60)
                continue
            pending = await get_pending_review_queue()
            now = datetime.now(timezone.utc)
            for row in pending:
                send_at = row['send_at']
                if isinstance(send_at, str):
                    send_at = datetime.fromisoformat(send_at)
                if send_at.tzinfo is None:
                    send_at = send_at.replace(tzinfo=timezone.utc)
                if now >= send_at:
                    await send_review_card(
                        room_id=row['room_id'],
                        order_id=row['order_id'],
                        menu_item=row['menu_item'],
                    )
                    await mark_review_sent(row['id'])
                    logger.info(f"✅ Review card sent for order {row['order_id']}")
        except Exception as e:
            logger.error(f"review_scheduler error: {e}")
        await asyncio.sleep(60)
        
        
