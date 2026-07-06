import asyncio
import concurrent.futures
import logging
from langchain_core.tools import tool
from db import save_reservation

logger = logging.getLogger(__name__)


@tool
def confirm_reservation(date: str, time: str, guests: int, customer_name: str, phone: str) -> str:
    """
    Save a confirmed reservation to the database.
    Only call this after collecting: date, time, guests, customer full name, and phone.
    """
    try:
        reservation_time = f"{date} {time}"

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    save_reservation(
                        name=customer_name,
                        phone=phone,
                        time=reservation_time,
                        guests=guests,
                    )
                )
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            future.result(timeout=10)

        return (
            f"RESERVATION_SAVED\nDate: {date}\nTime: {time}\n"
            f"Guests: {guests}\nName: {customer_name}\nPhone: {phone}\n"
            f"BANNER_TRIGGERED|success|Reservation Confirmed|"
            f"Table for {guests} booked for {customer_name} on {date} at {time}.|"
        )
    except Exception as e:
        logger.error(f"confirm_reservation error: {e}")
        return "RESERVATION_ERROR: Could not save reservation."
