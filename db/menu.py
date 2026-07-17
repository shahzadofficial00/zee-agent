import logging
from db.connection import _connect, _get_supabase, fuzzy_match_key

logger = logging.getLogger(__name__)


def init_menu_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price REAL,
        image TEXT,
        available INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cur.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('ordering_enabled', '1')
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_ordering (
        item_name TEXT PRIMARY KEY,
        orderable INTEGER DEFAULT 1
    )
    """)


# ─────────────────────────────────────────────
# MENU
# ─────────────────────────────────────────────
async def get_menu_items():
    try:
        db = await _get_supabase()
        result = (
            await db.table("menu_items")
            .select("id, name, price, image, category, description")
            .eq("available", True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_menu_items failed: {e}")
        return []


# ─────────────────────────────────────────────
# ORDERING SWITCHES (JNO-133 / JNO-134)
# ─────────────────────────────────────────────
def get_ordering_enabled() -> bool:
    from agent.ordering_config import ORDERING_ENABLED
    return ORDERING_ENABLED


def get_item_orderable(item_name: str) -> bool:
    from agent.ordering_config import ITEM_ORDERABLE_OVERRIDES
    overrides = {k.lower().strip(): v for k, v in ITEM_ORDERABLE_OVERRIDES.items()}
    match = fuzzy_match_key(item_name, list(overrides.keys()))
    return overrides[match] if match else True


def get_all_item_orderable() -> dict:
    """Returns {item_name_lower: bool} for every item with an explicit override."""
    from agent.ordering_config import ITEM_ORDERABLE_OVERRIDES
    return {k.lower().strip(): v for k, v in ITEM_ORDERABLE_OVERRIDES.items()}
