import time
from nio import AsyncClient
from config import MATRIX_SERVER, BOT_USER_ID

matrix_client = AsyncClient(MATRIX_SERVER, BOT_USER_ID)
BOT_START_TIME = int(time.time() * 1000)

async def send_text(room_id: str, text: str):
    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": text},
    )