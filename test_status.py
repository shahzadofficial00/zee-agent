import asyncio
from dotenv import load_dotenv
load_dotenv()

from bot.matrix_client import matrix_client
from bot.orders.fulfillment_service import send_order_status_update
from config import BOT_PASSWORD

ORDER_ID = "ORD-I8Y5PA"

STATUSES = [
    ("preparing", "Your order is being prepared."),
    ("ready", "Your order is ready!"),
    ("on_the_way", "Your order is on the way."),
    ("delivered", "Your order has been delivered. Enjoy!"),
    ("brewing", "Unrecognized status — should fall back to a generic icon + title-cased label."),
]


async def main():
    await matrix_client.login(BOT_PASSWORD)
    for status, message in STATUSES:
        ok = await send_order_status_update(ORDER_ID, status, message)
        print(f"{status}: sent={ok}")
        await asyncio.sleep(2)
    await matrix_client.close()

asyncio.run(main())
