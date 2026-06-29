








import asyncio
import logging
from dotenv import load_dotenv
from nio import RoomMessageText, UnknownEvent
from bot.matrix_client import matrix_client
from bot.message_handler import handle_message, handle_custom_event
from config import BOT_PASSWORD, ROOM_ID
from bot.review_scheduler import run_review_scheduler
from db import init_db

load_dotenv()
import os
print(f"🔧 .env check — REVIEW_CARD_ENABLED={os.getenv('REVIEW_CARD_ENABLED')!r} PAYMENT_CARD_ENABLED={os.getenv('PAYMENT_CARD_ENABLED')!r}")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    init_db()
    logger.info("🤖 Logging into Matrix...")
    await matrix_client.login(BOT_PASSWORD)
    logger.info(f"✅ Logged in as {matrix_client.user_id}")
    
    
    await matrix_client.sync(timeout=0, full_state=True)

    matrix_client.add_event_callback(handle_message, RoomMessageText)
    matrix_client.add_event_callback(handle_custom_event, UnknownEvent)
    
    asyncio.create_task(run_review_scheduler())

   

    logger.info("✅ Listening for new messages...")
    await matrix_client.sync_forever(timeout=3000, full_state=False)

if __name__ == "__main__":
    asyncio.run(main())