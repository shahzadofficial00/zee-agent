import os
from dotenv import load_dotenv
load_dotenv()

MATRIX_SERVER = os.getenv("MATRIX_SERVER", "")
BOT_USER_ID = os.getenv("BOT_USER_ID", "")
BOT_PASSWORD = os.getenv("BOT_PASSWORD", "")
ROOM_ID = os.getenv("ROOM_ID", "")
REVIEW_CARD_ENABLED = os.getenv("REVIEW_CARD_ENABLED", "true").lower() == "true"

# JNO-239 / JNO-241 — cash-only transition. Both default OFF: this gates real
# money movement, so an unset env var must not silently re-enable it.
ONLINE_PAYMENTS_ENABLED = os.getenv("ONLINE_PAYMENTS_ENABLED", "false").lower() == "true"
TIPS_ENABLED = os.getenv("TIPS_ENABLED", "false").lower() == "true"