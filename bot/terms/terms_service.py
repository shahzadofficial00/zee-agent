"""Terms & Conditions card (JNO-90).

Gate: every order. `_handle_order_flow_poll_answer` checks `needs_terms()`
before placing, and only sends this card if the customer hasn't already agreed
to the current version. A repeat customer signs once, not every order.

The terms text lives here as a constant rather than in a table, deliberately:

  * Nothing in JNO-90 authors a terms version — there's no admin surface, so a
    table would be hand-seeded SQL anyway, and git already gives us immutable
    version history for free.
  * Ordering is SQLite-backed precisely so it doesn't depend on the network
    (see CLAUDE.md, Split DB). Reading the agreement text from Supabase would
    put a remote call in the middle of checkout.

  ponytail: constant + hand-bumped version. Move to a `terms_versions` table
  when a business needs to edit its own terms without a deploy.

**Bump TERMS_VERSION whenever TERMS_BODY changes.** Agreements are keyed
(terms_id, version), so a new version re-prompts every customer automatically —
and leaving it stale silently records new consent against the old text.
"""
import logging

from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)

TERMS_ID = "order-terms"

# Bump whenever TERMS_BODY changes — re-prompts every customer.
TERMS_VERSION = "2026-08-05"

TERMS_TITLE = "Order Terms"

# Options: "tap" | "typed" | "drawn"
#   "tap"   → "I Agree" button        → signature stored as NULL
#   "typed" → name text field         → signature stored as the typed name
#   "drawn" → signature canvas        → signature stored as a base64 PNG
# The client falls back drawn → typed → tap if the device can't manage the
# level asked for, so what actually happened is stored as `method`, not this.
TERMS_SIGNING_LEVEL = "typed"

# Options: True | False
#   True  → card shows a Decline button
#   False → agree-or-abandon, no way to say no
# A decline is recorded but doesn't satisfy the gate, so the customer is
# re-prompted on their next order.
TERMS_ALLOW_DECLINE = True

TERMS_BODY = """These terms apply when you place an order with Dot Cafe \
(DHA Phase 4, Lahore) through this chat.

1. Your order. Prices are those shown on the menu in this chat at the time you \
order, in Pakistani Rupees. Your order is placed only after you confirm it, and \
is accepted once we send you a confirmation. If an item turns out to be \
unavailable we will tell you here and you will not be charged for it.

2. Payment. We are cash only. You pay when you collect your order or when it is \
handed to you. We do not take card or online payment, and we will never ask you \
for card details in this chat.

3. Changing or cancelling. You can cancel by replying "cancel" within 15 minutes \
of placing your order. After that we may already have prepared it, so please call \
us on 0321 1234567 instead and we will help where we can.

4. Collection and delivery. You can choose dine-in, pickup, curbside or delivery. \
Any time we give you is an estimate, not a guarantee. For delivery and curbside, \
please make sure the address, vehicle details and phone number you give us are \
correct and reachable — we may not be able to complete the order otherwise. \
We deliver within DHA Phase 4 only, and delivery is free. If your address is \
outside DHA Phase 4 we will let you know here and you are welcome to collect \
instead.

5. Allergens. Our food and drinks are prepared in a kitchen that also handles \
milk, nuts, soy, gluten and eggs, so we cannot guarantee any item is free from \
traces of these. If you have an allergy or intolerance, please tell us in the \
chat before you order.

6. If something is wrong. If your order is incorrect, incomplete or not of \
acceptable quality, tell us in this chat or call 0321 1234567 the same day and we \
will put it right — normally by replacing the item or refunding it. Nothing in \
these terms affects your rights under Pakistani consumer law.

7. Messages we send you. We will message you in this chat about an order you \
have placed — when it is being prepared, when it is ready or on its way, and \
once afterwards to ask how it was. We do not send marketing messages.

8. Ratings and reviews. If you rate an item or leave a review, we keep it and \
may use it to show ratings on our menu. Ratings are shown as an average across \
customers, not attributed to you by name. Leaving one is always optional.

9. Your information. We keep your name, phone number, any delivery address or \
vehicle details you share, your order history, your ratings and reviews, and \
this conversation, so we can prepare and deliver your orders and make future \
ones quicker. We do not sell your information or share it for marketing. \
Messages in this chat are encrypted. Ask us here if you want your details \
removed.

10. This agreement. We record that you agreed, the version of these terms you saw, \
how you signed, and when. Say "my agreements" at any time to read or download a \
copy of exactly what you agreed to. If we change these terms we will ask you to \
agree again before your next order — earlier orders stay under the terms you \
agreed to then.

Questions about any of this — just ask in the chat."""


def needs_terms(user_id: str) -> bool:
    """True if this customer still has to agree before an order can be placed."""
    from db import has_agreed
    return not has_agreed(user_id, TERMS_ID, TERMS_VERSION)


async def send_terms_history_card(room_id: str, user_id: str) -> bool:
    """This customer's agreement history (JNO-98).

    Declines are included, not filtered out — "you declined v2 on the 5th" is
    exactly the kind of thing an agreement history exists to show, and the card
    renders that state deliberately.

    `agreed` must go out as a real JSON bool: SQLite stores it as INTEGER and
    the Dart side reads `agreement['agreed'] as bool?`, which yields null (then
    silently defaults to true) on a 0/1. `db.get_agreements()` already casts.
    """
    from db import get_agreements
    rows = get_agreements(user_id)

    dsl = safe_send_dsl({
        "v": 1,
        "type": "terms_history",
        "data": {
            "agreements": [
                {
                    # Only one document exists today, so the title is resolved
                    # from the constant; an unrecognised terms_id falls back to
                    # the slug rather than mislabelling it as the current terms.
                    "title": TERMS_TITLE if r["terms_id"] == TERMS_ID else r["terms_id"],
                    "version": r["version"],
                    "agreed": bool(r["agreed"]),
                    "method": r["method"],
                    "signed_at": r["signed_at"] or r["created_at"] or "",
                    # Optional by contract: rows signed before the body column
                    # existed have none, and the card drops its "View copy"
                    # affordance rather than opening a blank page. Straight off
                    # the row — never TERMS_BODY, which is today's wording and
                    # would render as authoritative under an old version number.
                    **({"body": r["body"]} if r["body"] else {}),
                }
                for r in rows
            ],
        },
    })
    if not dsl:
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": f"Agreement history — {len(rows)} on record",
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"📜 Terms history sent | {user_id} | {len(rows)} agreement(s)")
    return True


async def send_terms_card(room_id: str) -> bool:
    """Send the agreement card. Returns False if the payload was blocked by
    schema validation, so the caller doesn't wait on a response that can't come."""
    dsl = safe_send_dsl({
        "v": 1,
        "type": "terms",
        "data": {
            "terms_id": TERMS_ID,
            "version": TERMS_VERSION,
            "title": TERMS_TITLE,
            "body": TERMS_BODY,
            "signing_level": TERMS_SIGNING_LEVEL,
            "allow_decline": TERMS_ALLOW_DECLINE,
        },
    })
    if not dsl:
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": f"{TERMS_TITLE} — please review before we place your order",
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"📄 Terms card sent | {TERMS_ID}@{TERMS_VERSION} | {room_id}")
    return True
