import asyncio
import logging

from db import (
    get_open_auctions_past_end,
    mark_auction_closed,
    get_auction_bidders,
    get_highest_bid,
    get_customer,
)
from bot.auction_service import send_auction_result_card
from bot.payment_service import create_payment_intent

logger = logging.getLogger(__name__)


async def run_auction_scheduler():
    logger.info("🔁 Auction scheduler started")
    while True:
        try:
            for auction in get_open_auctions_past_end():
                auction_id = auction["auction_id"]
                mark_auction_closed(auction_id)

                winner = get_highest_bid(auction_id)
                winning_amount = int(winner["amount"] if winner else auction["starting_price"])

                for bidder in get_auction_bidders(auction_id):
                    is_winner = winner is not None and bidder["user_id"] == winner["user_id"]

                    if is_winner:
                        customer = get_customer(bidder["user_id"])
                        if not customer:
                            logger.error(f"❌ No saved name/phone for auction winner {bidder['user_id']}, can't create payment")
                        else:
                            tx_id, _ = await create_payment_intent(
                                room_id=bidder["room_id"],
                                amount=int(winning_amount),
                                customer_name=customer["name"],
                                phone=customer["phone"],
                                order_id=auction_id,
                                user_id=bidder["user_id"],
                            )
                            if not tx_id:
                                logger.error(f"❌ Payment intent creation failed for auction winner {bidder['user_id']}")

                    await send_auction_result_card(
                        room_id=bidder["room_id"],
                        title=auction["title"],
                        currency=auction.get("currency", "PKR"),
                        winning_amount=winning_amount,
                        is_winner=is_winner,
                        order_id=auction_id,
                        user_id=bidder["user_id"],
                    )

                logger.info(f"🏁 Auction closed: {auction_id} | winner={winner['user_id'] if winner else 'none'}")
        except Exception as e:
            logger.error(f"auction_scheduler error: {e}")
        await asyncio.sleep(30)
