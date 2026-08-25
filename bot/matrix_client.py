import os
import time
from nio import AsyncClient, AsyncClientConfig
from nio.store import SqliteStore
from config import MATRIX_SERVER, BOT_USER_ID

# E2EE store (olm keys + sync token). nio turns encryption on by itself when
# python-olm is importable, so this file stays working without it installed.
STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store")
os.makedirs(STORE_PATH, exist_ok=True)

matrix_client = AsyncClient(
    MATRIX_SERVER,
    BOT_USER_ID,
    store_path=STORE_PATH,
    # SqliteStore, not the default: DefaultStore keeps device-trust state in files
    # named "<mxid>_<device>.blacklisted_devices", and the ':' in an mxid is an
    # illegal Windows filename char (WinError 123 on every encrypted send).
    # store_name for the same reason: the default is "<mxid>_<device>.db", and on
    # NTFS that colon silently puts the whole crypto DB in an alternate data
    # stream that disappears when the folder is zipped or copied to Linux.
    config=AsyncClientConfig(store_sync_tokens=True, store=SqliteStore, store_name="nio.db"),
)
BOT_START_TIME = int(time.time() * 1000)

# ponytail: blanket-trust every device instead of a verification flow — customers
# never verify a shop bot, and without this every send into an encrypted room
# raises OlmUnverifiedDeviceError. Patched here because every DSL sender in the
# repo goes through matrix_client.room_send. Swap for real verification only if
# someone actually needs it.
_room_send = matrix_client.room_send


async def _room_send_trusting(*args, **kwargs):
    kwargs.setdefault("ignore_unverified_devices", True)
    # JNO-292: no usage counting here — metering moved to the jaeno-metering
    # homeserver appservice, which bills every operator, not just this one.
    return await _room_send(*args, **kwargs)


matrix_client.room_send = _room_send_trusting


async def send_text(room_id: str, text: str):
    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": text},
    )