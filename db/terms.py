import logging
from db.connection import _connect

logger = logging.getLogger(__name__)

# A signature PNG arrives base64-encoded inside the Matrix event. The Flutter
# card caps its own encode at 48KB, but inbound DSL is never schema-validated
# (see CLAUDE.md) and a hand-rolled client isn't bound by that cap — so bound
# it again here, at the trust boundary.
MAX_SIGNATURE_CHARS = 64 * 1024


def init_agreements_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agreements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        room_id TEXT,
        terms_id TEXT NOT NULL,
        version TEXT NOT NULL,
        agreed INTEGER NOT NULL,
        method TEXT NOT NULL,
        signature TEXT,
        signed_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, terms_id, version)
    )
    """)
    # `body` snapshots the exact terms text the customer was shown, so a copy
    # can be produced later (JNO-97). Deliberately duplicated per agreement
    # rather than joined from a versions table: the row is then immune to any
    # later edit of the constant, which is the whole point of a consent record.
    cur.execute("PRAGMA table_info(agreements)")
    existing_cols = [row[1] for row in cur.fetchall()]
    if "body" not in existing_cols:
        cur.execute("ALTER TABLE agreements ADD COLUMN body TEXT")


def has_agreed(user_id: str, terms_id: str, version: str) -> bool:
    """True if this customer already agreed to this exact version.

    Keyed on version as well as id: republishing the terms is a new thing to
    agree to, so a bumped TERMS_VERSION re-prompts everyone automatically.
    A recorded *decline* is not an agreement — agreed = 1 only.
    """
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM agreements WHERE user_id = ? AND terms_id = ? AND version = ? AND agreed = 1",
        (user_id, terms_id, version),
    ).fetchone()
    conn.close()
    return row is not None


def save_agreement(
    user_id: str,
    room_id: str,
    terms_id: str,
    version: str,
    agreed: bool,
    method: str,
    signature: str | None,
    signed_at: str,
    body: str | None = None,
) -> bool:
    """Record the signed agreement (JNO-97/98). Returns False if it was rejected.

    `method` is what the device actually delivered after the fallback chain,
    NOT the signing_level that was requested — storing the request would claim
    a drawn signature we never received.

    Idempotent on (user_id, terms_id, version): Matrix redelivers events on
    re-sync, and a duplicate row would double-count a single act of consent.
    A decline followed by a later agreement upgrades the same row, so changing
    your mind doesn't leave a stale 'declined' record shadowing the consent.
    """
    if signature and len(signature) > MAX_SIGNATURE_CHARS:
        logger.error(
            f"❌ Oversized signature from {user_id} "
            f"({len(signature)} chars > {MAX_SIGNATURE_CHARS}) — rejecting"
        )
        return False

    conn = _connect()
    conn.execute(
        "INSERT INTO agreements "
        "(user_id, room_id, terms_id, version, agreed, method, signature, signed_at, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, terms_id, version) DO UPDATE SET "
        "  agreed    = excluded.agreed, "
        "  method    = excluded.method, "
        "  signature = excluded.signature, "
        "  signed_at = excluded.signed_at, "
        "  room_id   = excluded.room_id, "
        "  body      = excluded.body "
        "WHERE agreements.agreed = 0",
        (user_id, room_id, terms_id, version, 1 if agreed else 0, method, signature, signed_at, body),
    )
    conn.commit()
    conn.close()
    logger.info(f"📝 Agreement recorded | {user_id} | {terms_id}@{version} | {method} | agreed={agreed}")
    return True


def get_agreements(user_id: str, limit: int = 20) -> list[dict]:
    """This customer's agreement history (JNO-98) — newest first.

    `body` is the text that customer actually saw, read straight off the row.
    Never substitute today's TERMS_BODY: rendering current wording under an old
    version number looks authoritative and is wrong. Rows written before the
    column existed return None, and the card drops its "View copy" affordance
    rather than opening a blank page.

    Signature blobs are left out; this is the list view, and a base64 PNG per
    row would dwarf everything else in it.

    ponytail: 20-row cap because every body rides along in one Matrix event,
    which dies past ~64KB. Paginate if anyone ever signs more than that.
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT terms_id, version, agreed, method, signed_at, created_at, body "
        "FROM agreements WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "terms_id": r["terms_id"],
            "version": r["version"],
            "agreed": bool(r["agreed"]),
            "method": r["method"],
            "signed_at": r["signed_at"],
            "created_at": r["created_at"],
            "body": r["body"],
        }
        for r in rows
    ]
