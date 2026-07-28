import sqlite3
import logging
import difflib
import os
from dotenv import load_dotenv
from supabase import acreate_client

load_dotenv()

logger = logging.getLogger(__name__)


def fuzzy_match_key(name: str, candidates: list[str], cutoff: float = 0.75) -> str | None:
    """Match a (possibly misspelled) name against known keys. Exact match wins;
    otherwise falls back to the closest candidate within the similarity cutoff."""
    name = name.lower().strip()
    if name in candidates:
        return name
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None

DB_PATH = "restaurant.db"


# ─────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────
def _connect():
    # timeout: wait for the writer lock instead of raising "database is locked"
    # the instant another handler is mid-write. WAL: readers don't block on the
    # writer, which matters now that message handling runs concurrently.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


async def _get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL or SUPABASE_KEY missing")
    return await acreate_client(url, key)
