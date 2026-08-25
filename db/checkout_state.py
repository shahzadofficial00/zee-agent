import json
from db.connection import _connect

# A checkout parked longer than this is stale — the customer walked away.
# Resuming a two-day-old "what size would you like?" after a restart is more
# confusing than starting fresh, and the order_id inside it was minted against
# a 15-minute cancel window that lapsed long ago.
CHECKOUT_TTL_MINUTES = 180


def init_checkout_state_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS checkout_state (
        user_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        updated_at TEXT
    )
    """)


def load_checkout_state(user_id: str, max_age_minutes: int = CHECKOUT_TTL_MINUTES) -> dict:
    """The user's parked checkout, or {} if they have none or it went stale.
    Both sides of the age comparison use SQLite's own datetime('now') format, so
    there's no ISO-string mismatch of the kind get_open_auctions_past_end() hits."""
    conn = _connect()
    row = conn.execute(
        "SELECT state FROM checkout_state WHERE user_id = ? AND updated_at > datetime('now', ?)",
        (user_id, f"-{int(max_age_minutes)} minutes"),
    ).fetchone()
    conn.close()
    return json.loads(row["state"]) if row else {}


def save_checkout_state(user_id: str, state: dict):
    conn = _connect()
    conn.execute(
        "INSERT INTO checkout_state (user_id, state, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
        (user_id, json.dumps(state)),
    )
    conn.commit()
    conn.close()


def delete_checkout_state(user_id: str):
    conn = _connect()
    conn.execute("DELETE FROM checkout_state WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
