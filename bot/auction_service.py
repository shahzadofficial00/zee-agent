import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


def generate_auction_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(alphabet) for _ in range(6))
    return f"AUC-{suffix}"


def generate_unique_auction_id() -> str:
    from db import auction_id_exists
    for _ in range(5):
        candidate = generate_auction_id()
        if not auction_id_exists(candidate):
            return candidate
    return generate_auction_id() + secrets.choice(string.ascii_uppercase)


async def send_auction_card(room_id: str, auction: dict):
    dsl = {
        "v": 1,
        "type": "auction",
        "data": {
            "auction_id": auction["auction_id"],
            "title": auction["title"],
            "image": auction.get("image", ""),
            "currency": auction.get("currency", "PKR"),
            "starting_price": int(auction["starting_price"]),
            "min_bid": int(auction["min_bid"]),
            "ends_at": auction["ends_at"],
        },
    }
    if not safe_send_dsl(dsl):
        logger.error("❌ Auction DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": f"Auction: {auction['title']}",
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"🔨 Auction card sent | {auction['auction_id']} | {auction['title']}")


async def send_auction_result_card(room_id: str, title: str, currency: str,
                                    winning_amount: float, is_winner: bool,
                                    order_id: str, user_id: str):
    dsl = {
        "v": 1,
        "type": "auction_result",
        "data": {
            "title": title,
            "currency": currency,
            "winning_amount": winning_amount,
            "is_winner": is_winner,
            "order_id": order_id if is_winner else "",
            "user_id": user_id,
        },
    }
    if not safe_send_dsl(dsl):
        logger.error("❌ Auction result DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": "Auction closed",
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"🏁 Auction result sent | {order_id or title} | winner={is_winner} | to={user_id}")


async def create_and_send_auction(room_id: str, title: str, starting_price: float,
                                   min_bid: float, ends_in_seconds: int,
                                   image: str = "", currency: str = "PKR") -> str:
    """Manual entry point for creating an auction — call this from a shell/script
    for now. Swap in a staff chat-command or admin UI later without touching
    the bidding/closing flow at all."""
    from db import create_auction

    auction_id = generate_unique_auction_id()
    ends_at = (datetime.now(timezone.utc) + timedelta(seconds=ends_in_seconds)).isoformat()

    create_auction(
        auction_id=auction_id, title=title, image=image, currency=currency,
        starting_price=starting_price, min_bid=min_bid, ends_at=ends_at, room_id=room_id,
    )
    await send_auction_card(room_id, {
        "auction_id": auction_id, "title": title, "image": image, "currency": currency,
        "starting_price": starting_price, "min_bid": min_bid, "ends_at": ends_at,
    })
    return auction_id
