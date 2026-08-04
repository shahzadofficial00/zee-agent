# Dot Cafe Bot — Zee

A Matrix chat bot named **Zee** for **Dot Cafe** (specialty coffee shop, DHA Phase 4, Lahore). Customers chat with the bot over Matrix; an AI agent understands their requests and replies with rich UI cards sent via a custom DSL protocol.

- **Bot user:** `@dot_cafe:jaeno.ai`
- **Matrix server:** `https://chat.jaeno.ai`
- **LLM:** Gemini 2.5 Flash via LangChain/LangGraph

## Features

- Menu browsing (full menu, single item, category) as rich Matrix cards
- Deterministic checkout flow — per-item size and special-instructions polls, saved-customer-info reuse, fulfillment method (Dine-in/Pickup/Car/Delivery) with saved-vehicle/address picker, and a final confirmation, all as real poll/DSL cards (not left to the LLM to decide)
- Order placement with a payment card (Swich gateway) and a receipt that shows the chosen fulfillment details
- Order status updates (Preparing/Ready/On the way/Delivered) pushed to the customer's chat, triggered by a staff CLI script
- Post-order tip prompt with preset/custom amounts, paid through the same Swich flow as orders
- Order history lookup
- Table reservations
- Poll ecosystem: single-choice (size, Yes/No), multi-select flavor preference, drag-to-rank, free-text special instructions, post-order star ratings, and poll history/results cards — related questions share a `chain_id` so a multi-item order asks for its sizes, instructions and ratings in **one** card that advances in place, not a stack of them
- Post-order review prompts on a delay
- Per-customer name/phone memory across conversations
- Live auctions — server-validated bidding (highest bid wins, no LLM involved), automatic close on deadline, winner gets a real Swich payment card
- Works in end-to-end encrypted rooms, and auto-joins DM invites from the app's Discovery → "Start chat" flow with a greeting
- Handles customers concurrently — one slow agent turn no longer blocks every other room

> **Currently cash-only.** `ONLINE_PAYMENTS_ENABLED` and `TIPS_ENABLED` both default to `false`, so no Swich payment intent is created and no tip card is sent. Set both `true` in `.env` and restart to bring online payment back — no code changes. See CLAUDE.md → Cash-Only Mode.

## Project Structure

```
Restaurant Agent/
├── main.py                     # Entry point — login/session restore, auto-join, callbacks, schedulers
├── config.py                   # Matrix credentials, feature flags
├── dsl-spec/schemas/v1/schema.json  # DSL schema used to validate every outbound card
├── store/                      # gitignored — E2EE keys + saved session
├── db/                         # Database layer (SQLite + Supabase), one file per domain
│   ├── connection.py           # Shared _connect() / _get_supabase() / fuzzy_match_key()
│   ├── orders.py / customers.py / menu.py / payments.py
│   └── reviews.py / polls.py / auctions.py
├── agent/
│   ├── agent.py                # LangGraph agent assembly
│   ├── llm.py                  # Gemini 2.5 Flash config + rate limiter
│   ├── tools/                  # One file per agent tool, grouped into menu/, orders/, polls/ (+ show_banner.py at root)
│   ├── prompt.py                # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, summarization, retry, PII, call limits
│   ├── memory_tools.py         # Customer name/phone persistence
│   ├── state.py                # Shared MENU_PRICES cache
│   ├── context.py              # Context dataclass (user_id), passed to create_agent
│   └── ordering_config.py      # Master ordering switch + per-item overrides
└── bot/
    ├── matrix_client.py        # AsyncClient wrapper + send_text helper
    ├── dsl_validator.py        # JSON schema validation for DSL payloads
    ├── banner_service.py       # Banner DSL card
    ├── message_handler.py      # Thin entrypoint — routes to bot/router/
    ├── router/                 # Checkout state machine, agent dispatch, DSL/custom event routing
    │   ├── state.py / ids.py / order_flow.py
    │   └── agent_invoke.py / agent_dispatch.py / dsl_text_events.py / custom_events.py
    ├── menu/menu_service.py               # Sends menu/item/category DSL cards
    ├── payment/payment_service.py         # Swich gateway integration (orders and tips)
    ├── payment/tip_service.py             # Post-order tip request card
    ├── orders/order_confirmation_service.py  # Order receipt card (v1 + v2 with fulfillment summary)
    ├── orders/order_history_service.py       # Order history card
    ├── orders/fulfillment_service.py         # Fulfillment method/detail cards + order_status trigger
    ├── polls/                             # poll_service, flavor_poll_service, ranking_poll_service,
    │                                       # rating_poll_service, special_instructions_poll_service,
    │                                       # poll_history_service, poll_results_service
    ├── reviews/review_service.py          # Review card
    ├── reviews/review_scheduler.py        # Async scheduler for post-order reviews
    ├── auction/auction_service.py         # Auction + auction-result DSL cards, auction creation helper
    └── auction/auction_scheduler.py       # Async scheduler that closes due auctions and pays out the winner
```

See [`CLAUDE.md`](./CLAUDE.md) for the full per-file breakdown.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt   # if requirements.txt exists
```

`python-olm` is required for end-to-end encrypted rooms. Without it the bot still starts and logs a warning, but can't read or send in any encrypted room.

Copy `.env.example` to `.env` and fill in the required values (see below), then run:

```bash
python main.py
```

Startup sequence:
1. `init_db()` — creates SQLite tables if missing
2. `login()` — restores the saved session from `store/credentials.json`, or logs in fresh and writes it, then loads the E2EE key store
3. Full sync
4. Event callbacks registered — invites (auto-join), text messages, DSL/custom events
5. Review scheduler started as a background task
6. Auction scheduler started as a background task
7. `sync_forever()` — main event loop

**Don't delete `store/`.** It holds the bot's device identity and olm keys; wiping it makes the bot log in as a brand-new device and every customer's client has to re-share room keys.

Each incoming event is handled in its own task, serialized per sender (`bot/message_handler.py`) so one slow agent call can't block other customers while two events from the same customer still can't race the checkout state machine.

## Environment Variables (`.env`)

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service key |
| `SUPABASE_ANON_KEY` | Supabase anon key (for REST calls) |
| `SWICH_CLIENT_ID` | Swich payment gateway client ID |
| `SWICH_SECRET_KEY` | Swich HMAC signing secret |
| `BUSINESS_NAME` | Displayed on payment cards |
| `REVIEW_CARD_ENABLED` | `"true"` / `"false"` (default `"true"`) |
| `ONLINE_PAYMENTS_ENABLED` | Master Swich kill switch — default `"false"` (cash-only) |
| `TIPS_ENABLED` | Post-order tip card on/off — default `"false"` |
| `LANGCHAIN_API_KEY` | LangSmith tracing key |

## Database

**SQLite** (`restaurant.db`) — local, synchronous: orders, reservations, menu cache, settings, per-item ordering overrides, customers (name/phone), polls, poll answers, item ratings, auctions, auction bids.

**Supabase** — remote, async: payment intents (orders and tips), reviews, review queue, authoritative menu source.

## Ordering Control

Edit `agent/ordering_config.py` and restart the bot — no database, no live toggle:

```python
ORDERING_ENABLED = True          # False = entire menu is browse-only

ITEM_ORDERABLE_OVERRIDES = {
    "espresso": False,           # blocks ordering for this item only
}
```

## Auctions

There's no chat command or admin UI to start an auction yet — call `create_and_send_auction()` directly (see `test.py` for a working example):

```python
from bot.auction.auction_service import create_and_send_auction

await create_and_send_auction(
    room_id=ROOM_ID,
    title="Signature Blend",
    starting_price=1000,
    min_bid=1000,
    ends_in_seconds=300,
)
```

Bids come in from the client as a `bid_confirmation` DSL event and are re-validated server-side (the app's own min-bid check is cosmetic only) — see `db.place_bid_if_higher()`. The `auction_scheduler.py` background task closes auctions past their deadline every 30s, picks the highest bidder, creates a real Swich payment intent for them, and sends every bidder a personalized result card. Winning amounts under Swich's 10 PKR minimum won't get a payment card.

## Order Fulfillment & Status

After checkout, the customer picks how they want the order — Dine-in/Pickup (name), Car or Delivery (saved-vehicle/address picker, client-owned Supabase data) — before the final confirmation, so the receipt shows the whole picture at once.

There's no admin UI for order status yet either — push an update from a terminal:

```bash
python update_order_status.py ORD-AB12CD preparing
python update_order_status.py ORD-AB12CD ready
python update_order_status.py ORD-AB12CD delivered
```

## Documentation

See [`CLAUDE.md`](./CLAUDE.md) for a deeper dive into the architecture, the deterministic order/tip flow, the DSL protocol, the payment flow (which spans this bot, the Flutter app, and Supabase Edge Functions), the agent's middleware stack, and known fragility/gaps.
