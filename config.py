import os
from dotenv import load_dotenv
load_dotenv()

MATRIX_SERVER = os.getenv("MATRIX_SERVER", "")
BOT_USER_ID = os.getenv("BOT_USER_ID", "")
BOT_PASSWORD = os.getenv("BOT_PASSWORD", "")
ROOM_ID = os.getenv("ROOM_ID", "")
REVIEW_CARD_ENABLED = os.getenv("REVIEW_CARD_ENABLED", "true").lower() == "true"