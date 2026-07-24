import json
import logging
from bot.matrix_client import matrix_client
from bot.dsl_validator import safe_send_dsl
from db import get_menu_items, get_all_item_rating_summaries
from agent.state import update_menu_cache

logger = logging.getLogger(__name__)


def _rating_fields(item_name: str, summaries: dict) -> dict:
    """Aggregate stars for a menu card item. Empty dict when nobody's rated it
    yet, so both fields are omitted (client shows no stars).

    Takes the whole summaries dict rather than looking the item up itself — this
    runs once per item in a card, and a per-item DB call meant one SQLite
    connection per menu item on the asyncio loop. Callers fetch it once.
    """
    summary = summaries.get(item_name)
    if not summary:
        return {}
    return {"rating": str(summary["average"]), "reviews": summary["total_votes"]}


async def send_menu(room_id: str):
    items = await get_menu_items()

    if not items:
        logger.warning("⚠️ No menu items found in database")
        await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": "Sorry, menu is not available right now!"},
        )
        return

    update_menu_cache(items)

    from db import get_ordering_enabled, get_all_item_orderable
    ordering_enabled = get_ordering_enabled()
    item_flags = get_all_item_orderable()  # {item_name_lower: bool} — only disabled/explicit rows
    ratings = get_all_item_rating_summaries()  # {item_name: {average, total_votes}}

    categories_map = {}
    for item in items:
        cat = item.get("category") or "Other"
        categories_map.setdefault(cat, []).append(item)

    dsl = {
        "v": 2,
        "type": "menu",
        "data": {
            "title": "Our Menu",
            "orderable": ordering_enabled,
            "categories": [
                {
                    "name": cat_name,
                    "items": [
                        {
                            "id": str(i["id"]),
                            "name": i["name"],
                            "price": str(i["price"]),
                            "image": i.get("image") or "",
                            "description": i.get("description") or "",
                            "orderable": item_flags.get(i["name"].lower().strip(), True),
                            **_rating_fields(i["name"], ratings),
                        }
                        for i in cat_items
                    ],
                }
                for cat_name, cat_items in categories_map.items()
            ],
        },
    }

    logger.info(f"📤 DSL payload: {json.dumps(dsl)}")

    if not safe_send_dsl(dsl):
        logger.error("❌ Menu DSL invalid, not sending")
        return

    if not ordering_enabled:
        from bot.banner_service import send_banner_card
        await send_banner_card(
            room_id=room_id,
            variant="outage",
            title="Ordering Temporarily Paused",
            message="We're not able to take new orders right now — you can still browse the menu.",
        )

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": "Menu", "ai.jaeno.dsl": dsl},
    )
    logger.info(f"📋 Menu sent — {len(items)} items across {len(categories_map)} categories")
    
    
    
async def send_item_card(room_id: str, item_name: str):
    items = await get_menu_items()

    target = next(
        (i for i in items if i["name"].lower() == item_name.strip().lower()),
        None,
    )
    if not target:
        target = next(
            (i for i in items if item_name.strip().lower() in i["name"].lower()),
            None,
        )

    if not target:
        await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": f"Sorry, couldn't find '{item_name}' on the menu!"},
        )
        return

    dsl = {
        "v": 1,
        "type": "menu_item",
        "data": {
            "name": target["name"],
            "price": str(target["price"]),
            "image": target.get("image") or "",
            "description": target.get("description") or "",
            # One query for the whole table even though this card shows a single
            # item — same single round-trip either way, and one code path.
            **_rating_fields(target["name"], get_all_item_rating_summaries()),
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ menu_item DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": target["name"],
            "ai.jaeno.dsl": dsl,
        },
    )
    logger.info(f"📋 Item card sent: {target['name']}")
    
    
    
    

    
async def send_category_card(room_id: str, category_name: str):
    items = await get_menu_items()

    matched = [
        i for i in items
        if (i.get("category") or "").lower() == category_name.strip().lower()
    ]
    if not matched:
        matched = [
            i for i in items
            if category_name.strip().lower() in (i.get("category") or "").lower()
        ]

    if not matched:
        await matrix_client.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": f"Sorry, couldn't find a '{category_name}' category!"},
        )
        return

    ratings = get_all_item_rating_summaries()

    dsl = {
        "v": 1,
        "type": "menu_category",
        "data": {
            "name": matched[0]["category"],
            "items": [
                {
                    "id": str(i["id"]),
                    "name": i["name"],
                    "price": str(i["price"]),
                    "image": i.get("image") or "",
                    "description": i.get("description") or "",
                    **_rating_fields(i["name"], ratings),
                }
                for i in matched
            ],
        },
    }

    if not safe_send_dsl(dsl):
        logger.error("❌ menu_category DSL invalid, not sending")
        return

    await matrix_client.room_send(
        room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": matched[0]["category"], "ai.jaeno.dsl": dsl},
    )
    logger.info(f"📋 Category card sent: {matched[0]['category']} ({len(matched)} items)")    