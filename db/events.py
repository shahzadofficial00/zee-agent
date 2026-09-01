"""Events + ticket sales.

Same shape as db/auctions.py: the counts the customer sees are cosmetic, and
one locked transaction — reserve_tickets_if_available() — is the only thing
that decides whether a ticket exists. Exactly like place_bid_if_higher() being
the real min-bid check while the app's is decoration.
"""
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from db.connection import _connect, fuzzy_match_key

logger = logging.getLogger(__name__)


def _iso(days: int, hour: int, minutes: int = 0) -> str:
    """Seed timestamp, relative to first run rather than a fixed date.

    ponytail: relative seeding so a fresh clone gets events that haven't
    already happened. Real events want an admin surface (same gap as auction
    creation, #8) — replace the rows, not this helper.
    """
    base = datetime.now(timezone.utc).replace(microsecond=0, second=0, minute=minutes, hour=hour)
    return (base + timedelta(days=days)).isoformat()


# Seeded only into an empty table, same rule as _SEED_MENU/_SEED_FAQS — and the
# only git-tracked copy, since restaurant.db is gitignored.
#   (id, title, description, days_out, hour, location_type, location, online_url)
_SEED_EVENTS = [
    (1, 'Latte Art Throwdown',
     'Our monthly latte art competition — compete or just come and watch. '
     'Free filter coffee for everyone who turns up.',
     14, 18, 'in_person', 'Dot Cafe, DHA Phase 4, Lahore', ''),
    (2, 'Home Brewing Masterclass',
     'A live online session on getting cafe-quality coffee out of a V60 and '
     'an AeroPress at home. Recording shared with everyone who books.',
     21, 20, 'online', '', 'https://meet.jaeno.ai/dot-cafe-brewing'),
]

#   (event_id, name, price, remaining)
_SEED_TIERS = [
    (1, 'Spectator', 500, 40),
    (1, 'Competitor', 1500, 12),
    (2, 'Standard', 1000, 100),
]


def init_events_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        location_type TEXT NOT NULL CHECK (location_type IN ('in_person', 'online')),
        location TEXT,
        online_url TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS event_tiers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        remaining INTEGER NOT NULL,
        UNIQUE(event_id, name)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        reference TEXT PRIMARY KEY,
        event_id INTEGER NOT NULL,
        tier_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    if cur.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO events (id, title, description, starts_at, ends_at, "
            "location_type, location, online_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(eid, title, desc, _iso(days, hour), _iso(days, hour + 2), ltype, loc, url)
             for eid, title, desc, days, hour, ltype, loc, url in _SEED_EVENTS],
        )
        cur.executemany(
            "INSERT INTO event_tiers (event_id, name, price, remaining) VALUES (?, ?, ?, ?)",
            _SEED_TIERS,
        )


def get_events() -> list[dict]:
    """Events that haven't finished yet, soonest first.

    Filtered in Python, not SQL — same reason get_open_auctions_past_end() does:
    SQLite's datetime('now') and an offset-aware ISO string don't compare
    reliably.
    """
    conn = _connect()
    rows = conn.execute("SELECT * FROM events ORDER BY starts_at").fetchall()
    conn.close()
    now = datetime.now(timezone.utc).isoformat()
    return [dict(r) for r in rows if r["ends_at"] >= now]


def get_event_tiers(event_id: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT name, price, remaining FROM event_tiers WHERE event_id = ? ORDER BY price",
        (event_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_event(identifier) -> dict | None:
    """One event by id or by title, with its `tiers` attached, or None.

    Same ladder as show_item's: exact id, then title — exact, substring either
    direction ("latte art" → "Latte Art Throwdown"), then fuzzy_match_key for
    the typos. Worth having here, unlike FAQ matching, because the candidates
    are short names rather than whole sentences, so a partial is unambiguous.
    """
    key = str(identifier).strip().lower()
    if not key:
        return None

    events = get_events()
    match = None
    if key.isdigit():
        match = next((e for e in events if e["id"] == int(key)), None)
    if not match:
        by_title = {e["title"].lower(): e for e in events}
        match = by_title.get(key)
    if not match:
        match = next((e for e in events
                      if key in e["title"].lower() or e["title"].lower() in key), None)
    if not match:
        hit = fuzzy_match_key(key, list(by_title))
        match = by_title.get(hit) if hit else None
    if not match:
        return None
    return {**match, "tiers": get_event_tiers(match["id"])}


def _generate_reference() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "TKT-" + ''.join(secrets.choice(alphabet) for _ in range(6))


def reserve_tickets_if_available(event_id: int, tier_name: str,
                                 quantity: int, user_id: str) -> str | None:
    """Sell `quantity` tickets, or nothing. Returns the reference code, or None.

    THE only thing that decides whether a ticket is sold. The `remaining` count
    on the card is a snapshot that goes stale the instant it's rendered, so two
    customers tapping the last seat at the same moment must both be resolved
    here — one BEGIN IMMEDIATE transaction, exactly like place_bid_if_higher().
    Read-then-write outside a locked transaction is how you sell the same seat
    twice.
    """
    try:
        event_id, quantity = int(event_id), int(quantity)
    except (TypeError, ValueError):
        return None
    if quantity < 1:
        return None

    conn = _connect()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        event = conn.execute("SELECT ends_at FROM events WHERE id = ?", (event_id,)).fetchone()
        if not event or event["ends_at"] < datetime.now(timezone.utc).isoformat():
            conn.execute("ROLLBACK")
            return None

        tier = conn.execute(
            "SELECT name, remaining FROM event_tiers WHERE event_id = ? AND name = ? COLLATE NOCASE",
            (event_id, tier_name),
        ).fetchone()
        if not tier or tier["remaining"] < quantity:
            conn.execute("ROLLBACK")
            return None

        conn.execute(
            "UPDATE event_tiers SET remaining = remaining - ? WHERE event_id = ? AND name = ?",
            (quantity, event_id, tier["name"]),
        )
        for _ in range(5):
            reference = _generate_reference()
            if not conn.execute(
                "SELECT 1 FROM tickets WHERE reference = ?", (reference,)
            ).fetchone():
                break
        conn.execute(
            "INSERT INTO tickets (reference, event_id, tier_name, quantity, user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (reference, event_id, tier["name"], quantity, user_id),
        )
        conn.execute("COMMIT")
        logger.info(f"🎟️ Reserved {quantity}x {tier['name']} for event {event_id} | {reference}")
        return reference
    finally:
        conn.close()
