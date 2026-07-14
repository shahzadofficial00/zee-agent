from db.connection import _connect


def init_customers_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        user_id TEXT PRIMARY KEY,
        name TEXT,
        phone TEXT,
        updated_at TEXT
    )
    """)


def get_customer(user_id: str):
    """Look up a customer's saved name/phone. Returns None if never saved."""
    conn = _connect()
    row = conn.execute(
        "SELECT name, phone FROM customers WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"name": row["name"], "phone": row["phone"]}


def save_customer(user_id: str, name: str, phone: str):
    """Save/update a customer's name and phone, keyed by their Matrix user_id."""
    conn = _connect()
    conn.execute(
        "INSERT INTO customers (user_id, name, phone, updated_at) VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, phone = excluded.phone, updated_at = excluded.updated_at",
        (user_id, name, phone)
    )
    conn.commit()
    conn.close()
