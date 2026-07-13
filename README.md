# Dot Cafe Bot — Zee

A Matrix chat bot named **Zee** for **Dot Cafe** (specialty coffee shop, DHA Phase 4, Lahore). Customers chat with the bot over Matrix; an AI agent understands their requests and replies with rich UI cards sent via a custom DSL protocol.

- **Bot user:** `@dot_cafe:jaeno.ai`
- **Matrix server:** `https://chat.jaeno.ai`
- **LLM:** Gemini 2.5 Flash via LangChain/LangGraph

## Features

- Menu browsing (full menu, single item, category) as rich Matrix cards
- Deterministic checkout flow — per-item size and special-instructions polls, saved-customer-info reuse, and final confirmation, all as real poll cards (not left to the LLM to decide)
- Order placement with a payment card (Swich gateway) and receipt
- Post-order tip prompt with preset/custom amounts, paid through the same Swich flow as orders
- Order history lookup
- Table reservations
- Poll ecosystem: single-choice (size, Yes/No), multi-select flavor preference, drag-to-rank, free-text special instructions, post-order star ratings, and poll history/results cards
- Post-order review prompts on a delay
- Per-customer name/phone memory across conversations
- Live auctions — server-validated bidding (highest bid wins, no LLM involved), automatic close on deadline, winner gets a real Swich payment card

## Project Structure

```
Restaurant Agent/
├── main.py                     # Entry point
├── config.py                   # Matrix credentials, feature flags
├── db.py                       # Database layer (SQLite + Supabase)
├── agent/
│   ├── agent.py                # LangGraph agent assembly
│   ├── llm.py                  # Gemini 2.5 Flash config + rate limiter
│   ├── tools/                  # One file per agent tool (menu, order, reservation, polls, history, banner)
│   ├── prompt.py                # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, summarization, retry, PII, call limits
│   ├── memory_tools.py         # Customer name/phone persistence
│   ├── state.py                # Shared MENU_PRICES cache
│   ├── context.py              # Context dataclass (user_id), passed to create_agent
│   └── ordering_config.py      # Master ordering switch + per-item overrides
└── bot/
    ├── matrix_client.py        # AsyncClient wrapper + send_text helper
    ├── message_handler.py      # Main message router, deterministic order/tip flow, agent orchestration
    ├── menu_service.py         # Sends menu/item/category DSL cards
    ├── payment_service.py      # Swich gateway integration (orders and tips)
    ├── tip_service.py          # Post-order tip request card
    ├── order_confirmation_service.py  # Order receipt card
    ├── order_history_service.py       # Order history card
    ├── poll_service.py         # Single-choice poll cards
    ├── flavor_poll_service.py  # Multi-select flavor poll
    ├── ranking_poll_service.py # Drag-to-reorder poll
    ├── rating_poll_service.py  # Post-order star rating poll
    ├── special_instructions_poll_service.py  # Free-text special instructions poll
    ├── poll_history_service.py # Poll history card
    ├── poll_results_service.py # Aggregate poll results card
    ├── review_service.py       # Review card
    ├── review_scheduler.py     # Async scheduler for post-order reviews
    ├── auction_service.py      # Auction + auction-result DSL cards, auction creation helper
    ├── auction_scheduler.py    # Async scheduler that closes due auctions and pays out the winner
    └── dsl_validator.py        # JSON schema validation for DSL payloads
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt   # if requirements.txt exists
```

Copy `.env.example` to `.env` and fill in the required values (see below), then run:

```bash
python main.py
```

Startup sequence:
1. `init_db()` — creates SQLite tables if missing
2. Matrix login + full sync
3. Event callbacks registered
4. Review scheduler started as a background task
5. Auction scheduler started as a background task
6. `sync_forever()` — main event loop

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
| `PAYMENT_CARD_ENABLED` | Feature flag for payment card |
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
from bot.auction_service import create_and_send_auction

await create_and_send_auction(
    room_id=ROOM_ID,
    title="Signature Blend",
    starting_price=1000,
    min_bid=1000,
    ends_in_seconds=300,
)
```

Bids come in from the client as a `bid_confirmation` DSL event and are re-validated server-side (the app's own min-bid check is cosmetic only) — see `db.place_bid_if_higher()`. The `auction_scheduler.py` background task closes auctions past their deadline every 30s, picks the highest bidder, creates a real Swich payment intent for them, and sends every bidder a personalized result card. Winning amounts under Swich's 10 PKR minimum won't get a payment card.

## Documentation

See [`CLAUDE.md`](./CLAUDE.md) for a deeper dive into the architecture, the deterministic order/tip flow, the DSL protocol, the payment flow (which spans this bot, the Flutter app, and Supabase Edge Functions), the agent's middleware stack, and known fragility/gaps.
