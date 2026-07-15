"""
Staff CLI — push an order_status card into the customer's room (JNO-186..189).
No admin UI exists yet; this is the trigger until one does, same "manually
invoked, no chat exposure" pattern as create_and_send_auction() (see test.py).

Usage:
    python update_order_status.py <order_id> <status> [message]

Examples:
    python update_order_status.py ORD-AB12CD preparing
    python update_order_status.py ORD-AB12CD ready "Ready for pickup at the counter!"
    python update_order_status.py ORD-AB12CD delivered
"""
import sys
import asyncio
from dotenv import load_dotenv
load_dotenv()

from bot.matrix_client import matrix_client
from bot.orders.fulfillment_service import send_order_status_update
from config import BOT_PASSWORD


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    order_id = sys.argv[1]
    status = sys.argv[2]
    message = sys.argv[3] if len(sys.argv) > 3 else ""

    await matrix_client.login(BOT_PASSWORD)
    ok = await send_order_status_update(order_id, status, message)
    if ok:
        print(f"✅ Sent '{status}' status for {order_id}")
    else:
        print(f"❌ Could not find a room for order '{order_id}' — check the order_id is correct")
    await matrix_client.close()


if __name__ == "__main__":
    asyncio.run(main())
