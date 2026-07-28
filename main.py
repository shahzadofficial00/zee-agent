import asyncio
import json
import logging
import os
import sys

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from nio import RoomMessageText, UnknownEvent, InviteMemberEvent, JoinError
from bot.matrix_client import matrix_client, send_text, STORE_PATH
from bot.message_handler import handle_message, handle_custom_event
from config import BOT_PASSWORD, ROOM_ID
from bot.reviews.review_scheduler import run_review_scheduler
from bot.auction.auction_scheduler import run_auction_scheduler
from db import init_db

load_dotenv()
from config import ONLINE_PAYMENTS_ENABLED, TIPS_ENABLED
print(f"🔧 .env check — REVIEW_CARD_ENABLED={os.getenv('REVIEW_CARD_ENABLED')!r} ONLINE_PAYMENTS_ENABLED={ONLINE_PAYMENTS_ENABLED} TIPS_ENABLED={TIPS_ENABLED}")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


_joining = set()          # invite events repeat every sync until the join lands
_greet_tasks = set()      # asyncio only holds weak refs to tasks
GREETING = "👋 Hey! I'm Zee from Dot Cafe. Type *menu* to see what we've got brewing ☕"


async def _greet_when_room_ready(room_id: str):
    """Greet once the room is actually in client.rooms.

    room_send() looks the room up there on the encrypted path, but a joined room
    only appears after the NEXT sync — and this runs from the invite callback,
    mid-sync. So wait for a sync to land instead of sending immediately.
    """
    for _ in range(3):
        if room_id in matrix_client.rooms:
            await send_text(room_id, GREETING)
            return
        try:
            await asyncio.wait_for(matrix_client.synced.wait(), timeout=30)
        except asyncio.TimeoutError:
            break
    logger.warning(f"⚠️ Greeting skipped — {room_id} never showed up in a sync")


async def auto_join(room, event: InviteMemberEvent):
    """Accept DM invites from the app's Discovery → Start chat flow."""
    if event.membership != "invite" or event.state_key != matrix_client.user_id:
        return
    if room.room_id in _joining or room.room_id in matrix_client.rooms:
        return
    _joining.add(room.room_id)
    try:
        for attempt in range(3):
            resp = await matrix_client.join(room.room_id)
            if not isinstance(resp, JoinError):
                logger.info(f"✅ Joined {room.room_id} (invited by {event.sender})")
                task = asyncio.create_task(_greet_when_room_ready(room.room_id))
                _greet_tasks.add(task)
                task.add_done_callback(_greet_tasks.discard)
                return
            logger.warning(f"Join {room.room_id} failed ({attempt + 1}/3): {resp.message}")
            await asyncio.sleep(2)
    except Exception as e:
        # Never let this escape: an exception in a nio callback kills sync_forever.
        logger.error(f"auto_join failed for {room.room_id}: {e}", exc_info=True)
    finally:
        _joining.discard(room.room_id)


async def login():
    """Log in reusing the saved device_id, so the E2EE store stays valid across restarts.

    A fresh login every start would mint a new device — new olm keys, and every
    customer's client would have to re-share room keys with it.
    """
    creds_file = os.path.join(STORE_PATH, "credentials.json")
    if os.path.exists(creds_file):
        with open(creds_file) as f:
            creds = json.load(f)
        matrix_client.restore_login(**creds)
        logger.info(f"✅ Restored session {creds['device_id']}")
    else:
        resp = await matrix_client.login(BOT_PASSWORD, device_name="dot-cafe-bot")
        with open(creds_file, "w") as f:
            json.dump({"user_id": resp.user_id, "device_id": resp.device_id,
                       "access_token": resp.access_token}, f)
        logger.info(f"✅ Logged in as {resp.user_id} ({resp.device_id})")

    if matrix_client.config.encryption_enabled:
        matrix_client.load_store()  # sync_forever uploads/queries the keys itself
    else:
        logger.warning("⚠️  python-olm not installed — encrypted rooms will be unreadable")


async def main():
    init_db()
    logger.info("🤖 Logging into Matrix...")
    await login()

    await matrix_client.sync(timeout=0, full_state=True)

    matrix_client.add_event_callback(auto_join, InviteMemberEvent)
    matrix_client.add_event_callback(handle_message, RoomMessageText)
    matrix_client.add_event_callback(handle_custom_event, UnknownEvent)
    
    asyncio.create_task(run_review_scheduler())
    asyncio.create_task(run_auction_scheduler())

   

    logger.info("✅ Listening for new messages...")
    await matrix_client.sync_forever(timeout=3000, full_state=False)

if __name__ == "__main__":
    asyncio.run(main())