import hmac
import hashlib
import os
import uuid
import logging
import httpx
from urllib.parse import quote
from dotenv import load_dotenv
from supabase_auth import datetime
from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl
from datetime import datetime


load_dotenv()
logger = logging.getLogger(__name__)

SWICH_CLIENT_ID  = os.getenv("SWICH_CLIENT_ID")
SWICH_SECRET_KEY = os.getenv("SWICH_SECRET_KEY")
BUSINESS_NAME    = os.getenv("BUSINESS_NAME", "Unknown Business")
SWICH_BASE_URL   = "https://payin-pwa.swichnow.com/"
# SWICH_BASE_URL = "https://sandbox-payin-pwa.swichnow.com/"

EDGE_FUNCTION_URL = os.getenv("SUPABASE_URL") + "/functions/v1/smooth-processor"
EDGE_FUNCTION_KEY = os.getenv("SUPABASE_ANON_KEY")


def generate_checksum(transaction_id: str, item: str, amount: str) -> str:
    if not SWICH_SECRET_KEY:
        raise ValueError("SWICH_SECRET_KEY is not set in .env!")
    plain = f"Swich:{transaction_id}:{item}:{amount}"
    checksum = hmac.new(
        SWICH_SECRET_KEY.encode(),
        plain.encode(),
        hashlib.sha256
    ).hexdigest()
    return checksum


async def create_transaction_in_supabase(
    user_id: str,
    room_id: str,
    amount: int,
    customer_name: str,
    phone: str,
    order_id: str,
    payment_url: str = "",
) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            EDGE_FUNCTION_URL,
            json={
                "user_id": user_id,
                "room_id": room_id,
                "amount": amount,
                "customer_name": customer_name,
                "phone": phone,
                "order_id": order_id,
                "payment_url": payment_url,
                "business_name": BUSINESS_NAME,
            },
            headers={
                "Authorization": f"Bearer {EDGE_FUNCTION_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"✅ Supabase transaction created: {data['customer_transaction_id']}")
        return data


def build_payment_url(
    customer_name: str,
    phone: str,
    amount: int,
    transaction_id: str,
    order_id: str,
) -> str:
    if amount < 10:
        raise ValueError(f"Amount {amount} is below Swich minimum of 10 PKR")

    item       = "DotCafeOrder"
    amount_str = str(amount)
    email      = f"{phone}@dotcafe.com"
    checksum   = generate_checksum(transaction_id, item, amount_str)
    redirect   = quote(f"jaeno://payment/success?order_id={order_id}", safe="")

    params = (
        f"?clientId={SWICH_CLIENT_ID}"
        f"&customerTransactionId={transaction_id}"
        f"&item={item}"
        f"&amount={amount_str}"
        f"&channel=0"
        f"&billReferenceNo={transaction_id}"
        f"&description=DotCafeOrder"
        f"&PayeeName={quote(customer_name)}"
        f"&Email={quote(email)}"
        f"&MSISDN={phone}"
        f"&currency=PKR"
        f"&checksum={checksum}"
        f"&successRedirectUrl={redirect}"
    )
    url = f"{SWICH_BASE_URL}{params}"
    logger.info(f"💳 Payment URL built for tx: {transaction_id}")
    return url


async def update_payment_url_in_supabase(transaction_id: str, payment_url: str):
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{os.getenv('SUPABASE_URL')}/rest/v1/payment_intents"
            f"?customer_transaction_id=eq.{transaction_id}",
            json={"payment_url": payment_url},
            headers={
                "apikey": os.getenv("SUPABASE_ANON_KEY"),
                "Authorization": f"Bearer {os.getenv('SUPABASE_ANON_KEY')}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"✅ Payment URL updated in DB for tx: {transaction_id}")

async def create_payment_intent(room_id, amount, customer_name, phone, order_id, user_id):
    """
    Creates the payment_intents row + payment_url, WITHOUT sending the old
    standalone payment DSL card. Call this so refresh-payment / the
    order_confirmation "Pay Now" button has a row to find.
    Returns (transaction_id, payment_url).
    """
    if amount < 10:
        logger.error(f"❌ Refusing to create payment intent for invalid amount: {amount}")
        return None, None

    tx_data = await create_transaction_in_supabase(
        user_id=user_id,
        room_id=room_id,
        amount=amount,
        customer_name=customer_name,
        phone=phone,
        order_id=order_id,
        payment_url="",
    )
    transaction_id = tx_data["customer_transaction_id"]

    payment_url = build_payment_url(
        customer_name=customer_name,
        phone=phone,
        amount=amount,
        transaction_id=transaction_id,
        order_id=order_id,
    )

    await update_payment_url_in_supabase(transaction_id, payment_url)
    logger.info(f"💳 Payment intent created | order: {order_id} | tx: {transaction_id} | Rs. {amount}")
    return transaction_id, payment_url


async def send_payment_card(room_id, amount, customer_name, phone, order_id, user_id):
    """Kept for the 'pay'-keyword fallback path (send_existing_payment_card uses
    an existing row; this one still creates+sends if ever called directly)."""
    transaction_id, payment_url = await create_payment_intent(
        room_id=room_id,
        amount=amount,
        customer_name=customer_name,
        phone=phone,
        order_id=order_id,
        user_id=user_id,
    )
    if not transaction_id:
        return

    dsl = {
        "v": 1,
        "type": "payment",
        "data": {
            "amount": amount,
            "currency": "PKR",
            "order_id": order_id,
            "transaction_id": transaction_id,
            "payment_url": payment_url,
            "customer_name": customer_name,
            "user_id": user_id,
            "business_name": BUSINESS_NAME,
            "created_at": f"{datetime.now().day} {datetime.now().strftime('%B')}",
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Payment DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": "Payment",
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"💳 Payment card sent | order: {order_id} | tx: {transaction_id} | Rs. {amount}")
    
    
async def send_existing_payment_card(
    room_id: str,
    pending: dict,
):
    """Resend the payment card for an already-existing pending intent.
    Does NOT create a new payment_intents row."""
    dsl = {
        "v": 1,
        "type": "payment",
        "data": {
            "amount": pending["amount"],
            "currency": "PKR",
            "order_id": pending["order_id"],
            "transaction_id": pending["customer_transaction_id"],
            "payment_url": pending["payment_url"],
            "customer_name": pending["customer_name"],
            "user_id": pending["user_id"],
            "business_name": BUSINESS_NAME,
            "created_at": f"{datetime.now().day} {datetime.now().strftime('%B')}",
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ Payment DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": "Payment",
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"💳 Existing payment card resent | order: {pending['order_id']} | tx: {pending['customer_transaction_id']}")    