import json
from db.connection import _connect


def init_conversation_history_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS conversation_history (
        user_id TEXT PRIMARY KEY,
        history TEXT NOT NULL,
        updated_at TEXT
    )
    """)


def load_history(user_id: str) -> list:
    """Load a user's saved conversation history. Returns [] if never saved."""
    conn = _connect()
    row = conn.execute(
        "SELECT history FROM conversation_history WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return []
    return json.loads(row["history"])


def save_history(user_id: str, history: list):
    """Persist a user's full conversation history, keyed by their Matrix user_id."""
    conn = _connect()
    conn.execute(
        "INSERT INTO conversation_history (user_id, history, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(user_id) DO UPDATE SET history = excluded.history, updated_at = excluded.updated_at",
        (user_id, json.dumps(history))
    )
    conn.commit()
    conn.close()
