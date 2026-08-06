from db.connection import _connect, _get_supabase, fuzzy_match_key, DB_PATH

from db.orders import (
    init_orders_schema, save_order, save_reservation, order_id_exists,
    get_orders_by_room, update_order_room_id, update_order_stable_id,
    update_order_fulfillment, get_order_by_stable_id, mark_order_paid,
    cancel_last_pending_order,
)
from db.customers import init_customers_schema, get_customer, save_customer
from db.menu import (
    init_menu_schema, get_menu_items, get_ordering_enabled,
    get_item_orderable, get_all_item_orderable,
)
from db.payments import (
    save_payment, get_payment, update_payment_status,
    get_pending_payment_by_user, get_payment_statuses,
)
from db.reviews import (
    init_reviews_schema, save_review, insert_review_queue,
    get_pending_review_queue, mark_review_sent,
)
from db.polls import (
    init_polls_schema, save_poll, get_poll_by_event_id, get_poll_by_poll_id,
    save_item_rating, get_item_rating_summary, get_all_item_rating_summaries,
    save_poll_answer, get_poll_answers,
)
from db.auctions import (
    init_auctions_schema, auction_id_exists, create_auction, get_auction,
    get_open_auctions_past_end, mark_auction_closed, place_bid_if_higher,
    get_highest_bid, get_auction_bidders,
)
from db.conversation_history import (
    init_conversation_history_schema, load_history, save_history,
)
from db.terms import (
    init_agreements_schema, has_agreed, save_agreement, get_agreements,
)


# ─────────────────────────────────────────────
# INIT
# ─────────────────────────────────────────────
def init_db():
    conn = _connect()
    cur = conn.cursor()

    init_orders_schema(cur)
    init_customers_schema(cur)
    init_reviews_schema(cur)
    init_menu_schema(cur)
    init_polls_schema(cur)
    init_auctions_schema(cur)
    init_conversation_history_schema(cur)
    init_agreements_schema(cur)

    conn.commit()
    conn.close()
