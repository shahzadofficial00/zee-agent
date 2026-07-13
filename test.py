import asyncio
from dotenv import load_dotenv
load_dotenv()

from bot.matrix_client import matrix_client
from bot.auction_service import create_and_send_auction
from config import BOT_PASSWORD, ROOM_ID


async def main():
    await matrix_client.login(BOT_PASSWORD)
    auction_id = await create_and_send_auction(
        room_id=ROOM_ID,
        title="Signature Blend",
        starting_price=2,
        min_bid=2,
        ends_in_seconds=300,  # 5 minutes
    )
    print(f"Auction created: {auction_id}")
    await matrix_client.close()

asyncio.run(main())