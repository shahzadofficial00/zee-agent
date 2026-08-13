import logging
from db.connection import _connect, fuzzy_match_key

logger = logging.getLogger(__name__)


# The menu, seeded only into an empty table (see init_menu_schema). This is the
# ONLY tracked copy: restaurant.db is gitignored and Supabase's menu_items was
# retired, so a fresh clone gets its menu from here. Ids are the old Supabase
# ids, kept so the DSL cards' `id` field stays stable.
#   (id, name, price, image, category, description)
# Edit the SQLite rows for a live change; edit this list for a change that
# survives a wiped database.
_SEED_MENU = [
    (1, 'Espresso', 10,
     'https://plus.unsplash.com/premium_photo-1675435644687-562e8042b9db?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8MXx8ZXNwcmVzc28lMjBjb2ZmZWV8ZW58MHx8MHx8fDA%3D',
     'Hot Classics',
     'A concentrated single shot with a rich crema and a bold, roasted finish.'),
    (2, 'Cappuccino', 20,
     'https://images.unsplash.com/photo-1533776992670-a72f4c28235e?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8N3x8Q2FwcHVjY2lubyUyMGNvZmZlZXxlbnwwfHwwfHx8MA%3D%3D',
     'Hot Classics',
     'Equal parts espresso, steamed milk and airy foam, dusted with cocoa.'),
    (3, 'Latte', 30,
     'https://images.unsplash.com/photo-1593443320739-77f74939d0da?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8OHx8TGF0dGUlMjBjb2ZmZWV8ZW58MHx8MHx8fDA%3D',
     'Hot Classics',
     'Smooth espresso under a layer of velvety steamed milk and light foam.'),
    (4, 'Americano', 40,
     'https://plus.unsplash.com/premium_photo-1723559972702-2659e41dbb5b?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8MTd8fEFtZXJpY2FubyUyMGNvZmZlZXxlbnwwfHwwfHx8MA%3D%3D',
     'Hot Classics',
     'Espresso loosened with hot water for a clean, full-bodied cup.'),
    (5, 'Mocha', 50,
     'https://images.unsplash.com/photo-1600056781444-55f3b64235e3?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8MjB8fE1vY2hhJTIwY29mZmVlfGVufDB8fDB8fHww',
     'Hot Classics',
     'Espresso and steamed milk folded through dark chocolate, topped with cream.'),
    (6, 'Velvet Coconut Latte', 999,
     'https://plus.unsplash.com/premium_photo-1723759448747-1d174225e61f?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8MXx8VmVsdmV0JTIwQ29jb251dCUyMExhdHRlfGVufDB8fDB8fHww',
     'Specialty Lattes',
     'Espresso with silky coconut milk and a soft vanilla sweetness.'),
    (7, 'Bono Latte', 999,
     '',
     'Specialty Lattes',
     'Our house latte — double shot, caramelised sugar and toasted milk.'),
    (8, 'Choco Hazelnut Latte', 999,
     'https://images.unsplash.com/photo-1554893898-52600b2d7fb4?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8M3x8Q2hvY28lMjBIYXplbG51dCUyMExhdHRlfGVufDB8fDB8fHww',
     'Specialty Lattes',
     'Roasted hazelnut and chocolate stirred through a creamy espresso latte.'),
    (9, 'Spanish Latte Premium', 1999,
     'https://media.istockphoto.com/id/1160340837/photo/morning-cappuccino.webp?a=1&b=1&s=612x612&w=0&k=20&c=nPgStGB0TUwISWgXoZ8HZAGUshKH9pJD91pcNu6EE5E=',
     'Premium Brews',
     'Espresso with sweetened condensed milk for a rich, dessert-like cup.'),
    (10, 'Orange Mocha Coffee', 1777,
     'https://media.istockphoto.com/id/2254685529/photo/flat-white-coffee-served-in-a-green-mug.webp?a=1&b=1&s=612x612&w=0&k=20&c=3uw-SEgRdOeAnP8sO9XcEf-FvMk0Rjc8TcFGi7vf5IY=',
     'Premium Brews',
     'A dark chocolate mocha lifted with bright orange zest.'),
    (11, 'Belgian Cortado', 1666,
     'https://media.istockphoto.com/id/2150826301/photo/glass-with-vegan-grain-coffee-and-plant-milk-natural-biscuits-wooden-table-blurred-background.webp?a=1&b=1&s=612x612&w=0&k=20&c=_HYdPbIJXcIg96bjIf-lNNecQgHoaiscGPJpg_P-Lps=',
     'Premium Brews',
     'Equal parts espresso and warm milk, finished with Belgian chocolate.'),
    (12, 'Matcha Classic', 888,
     'https://images.unsplash.com/photo-1690993515142-f73920b53cac?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8MjB8fE1hdGNoYSUyMENsYXNzaWN8ZW58MHx8MHx8fDA%3D',
     'Matcha & Frappes',
     'Stone-ground ceremonial matcha whisked smooth with chilled milk.'),
    (13, 'Strawberry Matcha', 1111,
     'https://images.unsplash.com/photo-1749104028327-a33087ea4f47?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8M3x8U3RyYXdiZXJyeSUyME1hdGNoYXxlbnwwfHwwfHx8MA%3D%3D',
     'Matcha & Frappes',
     'Layered matcha and fresh strawberry purée poured over ice.'),
    (14, 'Caramel Frappe', 999,
     'https://images.unsplash.com/photo-1579888071069-c107a6f79d82?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8M3x8Q2FyYW1lbCUyMEZyYXBwZXxlbnwwfHwwfHx8MA%3D%3D',
     'Matcha & Frappes',
     'Blended iced coffee with caramel and a swirl of whipped cream.'),
    (15, 'Cold Latte', 666,
     'https://images.unsplash.com/photo-1641659736749-8bbae305e475?w=500&auto=format&fit=crop&q=60&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxzZWFyY2h8MTJ8fENvbGQlMjBMYXR0ZXxlbnwwfHwwfHx8MA%3D%3D',
     'Cold Drinks',
     'Slow-steeped cold brew poured over milk and ice.'),
    (16, 'Berry Mojito', 666,
     '',
     'Cold Drinks',
     'Mixed berries, lime and mint over crushed ice. Caffeine-free.'),
    (17, 'Mango Smoothie', 999,
     '',
     'Cold Drinks',
     'Ripe mango blended with yoghurt and ice. Caffeine-free.'),
]


def init_menu_schema(cur):
    # The pre-JNO-menu-local table was a 5-row stale cache nothing read (the menu
    # came from Supabase) with price REAL and no category/description. Drop it
    # rather than ALTER: REAL would render "10.0" on the card, and the rows are junk.
    cols = [r[1] for r in cur.execute("PRAGMA table_info(menu_items)")]
    if cols and "category" not in cols:
        cur.execute("DROP TABLE menu_items")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        image TEXT,
        category TEXT,
        description TEXT,
        available INTEGER DEFAULT 1
    )
    """)
    # Seed only when empty, so edits and deletions survive a restart — same rule
    # as init_faqs_schema. A full wipe reads as a fresh install and gets the
    # menu back.
    if cur.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO menu_items (id, name, price, image, category, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            _SEED_MENU,
        )

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
    """Every available menu item, from local SQLite.

    Still `async` purely so the ~8 existing call sites keep their `await` — the
    read itself is synchronous now. Was Supabase; moved local so the menu (and
    therefore ordering, which prices off it) doesn't need the network. The table
    seeds itself from `_SEED_MENU` when empty; edit the rows for a live change.
    """
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, name, price, image, category, description "
            "FROM menu_items WHERE available = 1 ORDER BY id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
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
