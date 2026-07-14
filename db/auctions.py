from datetime import datetime, timezone
from db.connection import _connect


def init_auctions_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS auctions (
        auction_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        image TEXT,
        currency TEXT DEFAULT 'PKR',
        starting_price REAL NOT NULL,
        min_bid REAL NOT NULL,
        ends_at TEXT NOT NULL,
        room_id TEXT NOT NULL,
        closed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auction_bids (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        auction_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        room_id TEXT NOT NULL,
        amount REAL NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(auction_id, user_id)
    )
    """)


def auction_id_exists(auction_id: str) -> bool:
    conn = _connect()
    row = conn.execute("SELECT 1 FROM auctions WHERE auction_id = ?", (auction_id,)).fetchone()
    conn.close()
    return row is not None


def create_auction(auction_id: str, title: str, image: str, currency: str,
                    starting_price: float, min_bid: float, ends_at: str, room_id: str):
    conn = _connect()
    conn.execute(
        "INSERT INTO auctions (auction_id, title, image, currency, starting_price, min_bid, ends_at, room_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (auction_id, title, image, currency, starting_price, min_bid, ends_at, room_id),
    )
    conn.commit()
    conn.close()


def get_auction(auction_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM auctions WHERE auction_id = ?", (auction_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_open_auctions_past_end() -> list:
    """Auctions whose deadline has passed and haven't been closed out yet.
    Filtered in Python (not SQL) to avoid ISO-string vs SQLite datetime() format mismatches."""
    conn = _connect()
    rows = conn.execute("SELECT * FROM auctions WHERE closed = 0").fetchall()
    conn.close()
    now = datetime.now(timezone.utc).isoformat()
    return [dict(r) for r in rows if r["ends_at"] <= now]


def mark_auction_closed(auction_id: str):
    conn = _connect()
    conn.execute("UPDATE auctions SET closed = 1 WHERE auction_id = ?", (auction_id,))
    conn.commit()
    conn.close()


def place_bid_if_higher(auction_id: str, user_id: str, room_id: str, amount: float) -> dict:
    """Atomically validate + record a bid inside one locked transaction, so two
    near-simultaneous bids can't both read the same 'current highest' and both win.
    Returns {"accepted": bool, "reason": str|None, "highest": float|None}."""
    conn = _connect()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        auction = conn.execute(
            "SELECT min_bid, closed, ends_at FROM auctions WHERE auction_id = ?", (auction_id,)
        ).fetchone()
        if not auction:
            conn.execute("ROLLBACK")
            return {"accepted": False, "reason": "Auction not found.", "highest": None}

        now = datetime.now(timezone.utc).isoformat()
        if auction["closed"] or auction["ends_at"] <= now:
            conn.execute("ROLLBACK")
            return {"accepted": False, "reason": "This auction has already closed.", "highest": None}

        row = conn.execute(
            "SELECT MAX(amount) AS highest FROM auction_bids WHERE auction_id = ?", (auction_id,)
        ).fetchone()
        current_highest = row["highest"]
        floor = current_highest if current_highest is not None else (auction["min_bid"] - 1)

        if amount <= floor:
            conn.execute("ROLLBACK")
            return {"accepted": False, "reason": f"Bid must be higher than Rs {floor}.", "highest": floor}

        conn.execute(
            "INSERT INTO auction_bids (auction_id, user_id, room_id, amount, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(auction_id, user_id) DO UPDATE SET "
            "amount = excluded.amount, room_id = excluded.room_id, created_at = excluded.created_at",
            (auction_id, user_id, room_id, amount),
        )
        conn.execute("COMMIT")
        return {"accepted": True, "reason": None, "highest": amount}
    finally:
        conn.close()


def get_highest_bid(auction_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT user_id, room_id, amount FROM auction_bids WHERE auction_id = ? ORDER BY amount DESC LIMIT 1",
        (auction_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_auction_bidders(auction_id: str) -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT user_id, room_id, amount FROM auction_bids WHERE auction_id = ?", (auction_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
