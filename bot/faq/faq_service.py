"""FAQ card (JNO-54; stories JNO-55/56).

One DSL type covers both stories — the client renders a single entry expanded
(the direct answer JNO-55 wants) and several as a collapsed accordion (JNO-56).
So the agent sends the entries it selected and never picks a layout.

Read-only: no inbound event, no state, nothing to persist. None of the terms
machinery applies here.
"""
import logging

from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl

logger = logging.getLogger(__name__)


async def send_faq_card(room_id: str, items: list[dict], title: str = "FAQ") -> bool:
    """Send `items` as one faq card. Returns False if nothing was sent.

    `question`/`answer` are cast to str deliberately: the Dart reads each as
    `String?` off a plain map with no generated model, so a non-string throws in
    the cast and the card falls back to "Unsupported" — the same class of bug as
    the menu `price` field (see CLAUDE.md). Answers are plain text; the widget
    library renders no markdown.
    """
    if not items:
        await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": "I don't have any FAQs saved yet — just ask me directly and I'll help.",
            },
        )
        return False

    dsl = {
        "v": 1,
        "type": "faq",
        "data": {
            "title": title,
            "items": [
                {"question": str(i["question"]), "answer": str(i["answer"])}
                for i in items
            ],
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ faq DSL invalid, not sending")
        return False

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": title, "ai.jaeno.dsl": dsl},
    )
    logger.info(f"❓ FAQ card sent — {len(items)} entries")
    return True
