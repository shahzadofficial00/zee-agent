import logging
from db.connection import _connect

logger = logging.getLogger(__name__)

# Draft copy, seeded only into an empty table (see init_faqs_schema). Every
# answer restates something the code actually does — cash-only, the 15-minute
# cancel window, the DHA Phase 4 delivery area — so it drifts exactly the way
# TERMS_BODY can. Once the bot is live, edit the rows, not this list.
_SEED_FAQS = [
    ("How do I order?",
     "Just tell me what you'd like, or say \"menu\" to browse. I'll ask about sizes "
     "and any special instructions as we go."),
    ("How can I pay?",
     "We're cash only right now. You pay when you collect your order, or when it's "
     "handed to you."),
    ("Do you deliver?",
     "Yes, within DHA Phase 4, and delivery is free. If you're outside that area "
     "I'll let you know and you're welcome to collect instead."),
    # Deliberately "cancellation policy", not "Can I cancel my order?" — the
    # \bcancel\b fast path in message_handler.py fires before the agent ever
    # runs, so that phrasing could never reach an FAQ, and mid-checkout it would
    # drop the customer's in-flight order instead of answering them. \b doesn't
    # match inside "cancellation", which is why that wording is safe.
    ("What is your cancellation policy?",
     "Reply \"cancel\" within 15 minutes of placing an order and I'll take care of "
     "it. After that we may already have started, so please give us a call instead."),
    ("Do you cater for allergies?",
     "Our kitchen also handles milk, nuts, soy, gluten and eggs, so we can't "
     "guarantee any item is free from traces. Tell me before you order and we'll "
     "do what we can."),
    ("Where are you?",
     "Dot Cafe, DHA Phase 4, Lahore."),
]


def init_faqs_schema(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS faqs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Seed only when empty, so edits and deletions survive a restart. A full
    # wipe reads as a fresh install and gets the drafts back — delete the
    # constant instead if that's ever unwanted.
    if cur.execute("SELECT COUNT(*) FROM faqs").fetchone()[0] == 0:
        cur.executemany("INSERT INTO faqs (question, answer) VALUES (?, ?)", _SEED_FAQS)


def get_faqs() -> list[dict]:
    """Every FAQ in insertion order — which is the accordion's order (JNO-56).

    No sort_order column: `ORDER BY id` is a sane default and nothing can
    reorder them yet. Add one when there's a surface that needs to.
    """
    conn = _connect()
    rows = conn.execute("SELECT question, answer FROM faqs ORDER BY id").fetchall()
    conn.close()
    return [{"question": r["question"], "answer": r["answer"]} for r in rows]


def get_faq(question: str) -> dict | None:
    """Best single match for what the customer asked (JNO-55), or None.

    Exact, then substring either direction. Deliberately NOT fuzzy_match_key():
    on real questions difflib only ever fires where substring already failed,
    and measured against this seed set every such case was wrong — "do you have
    parking for a minibus" scores 0.60 against "Do you cater for allergies?",
    while a genuine miss like "what payment methods do you take" reaches only
    0.35 against "How can I pay?". No cutoff separates those, so the tier can
    only add confident wrong answers. Typo tolerance isn't needed either: the
    LLM writes this string, not the customer.

    Returning None is the safe outcome — show_faq reports FAQ_NO_MATCH and the
    model answers normally or offers the full list.

    ponytail: substring matching, so re-worded questions miss. Embeddings are
    the upgrade if the logs show real questions going unanswered.
    """
    q = question.strip().lower()
    if not q:
        return None

    by_q = {f["question"].strip().lower(): f for f in get_faqs()}
    if q in by_q:
        return by_q[q]
    for stored, faq in by_q.items():
        if q in stored or stored in q:
            return faq
    return None
