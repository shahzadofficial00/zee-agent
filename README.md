# Dot Cafe Bot — Zee

A Matrix chat bot named **Zee** for **Dot Cafe** (specialty coffee shop, DHA Phase 4, Lahore). Customers chat with the bot over Matrix; an AI agent understands their requests and replies with rich UI cards sent via a custom DSL protocol.

- **Bot user:** `@dot_cafe:jaeno.ai`
- **Matrix server:** `https://chat.jaeno.ai`
- **LLM:** Gemini 2.5 Flash via LangChain/LangGraph

## Features

- Menu browsing (full menu, single item, category) as rich Matrix cards
- Order placement with a payment card (Swich gateway) and receipt
- Order history lookup
- Table reservations
- Single-choice polls (e.g. size/flavor picks) via native Matrix polls
- Post-order review prompts on a delay
- Per-customer name/phone memory across conversations

## Project Structure

```
Restaurant Agent/
├── main.py                     # Entry point
├── config.py                   # Matrix credentials, feature flags
├── db.py                       # Database layer (SQLite + Supabase)
├── agent/
│   ├── agent.py                # LangGraph agent assembly
│   ├── llm.py                  # Gemini 2.5 Flash config + rate limiter
│   ├── tools.py                # Agent tools (menu, order, reservation, poll, history)
│   ├── prompt.py                # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, retry, summarization, PII
│   ├── memory_tools.py         # Customer name/phone persistence
│   ├── state.py                # Shared MENU_PRICES / CACHED_MENU cache
│   ├── context.py              # Context dataclass (currently unused)
│   └── ordering_config.py      # Master ordering switch + per-item overrides
└── bot/
    ├── matrix_client.py        # AsyncClient wrapper + send_text helper
    ├── message_handler.py      # Main message router + agent orchestration
    ├── menu_service.py         # Sends menu/item/category DSL cards
    ├── payment_service.py      # Swich gateway integration
    ├── order_confirmation_service.py  # Order receipt card
    ├── order_history_service.py       # Order history card
    ├── poll_service.py         # Native Matrix single-choice polls
    ├── review_service.py       # Review card
    ├── review_scheduler.py     # Async scheduler for post-order reviews
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
5. `sync_forever()` — main event loop

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

**SQLite** (`restaurant.db`) — local, synchronous: orders, reservations, menu cache, settings, per-item ordering overrides, polls.

**Supabase** — remote, async: payment intents, reviews, review queue, authoritative menu source.

## Ordering Control

Edit `agent/ordering_config.py` and restart the bot — no database, no live toggle:

```python
ORDERING_ENABLED = True          # False = entire menu is browse-only

ITEM_ORDERABLE_OVERRIDES = {
    "espresso": False,           # blocks ordering for this item only
}
```

## Documentation

See [`CLAUDE.md`](./CLAUDE.md) for a deeper dive into the architecture, the DSL protocol, the payment flow (which spans this bot, the Flutter app, and Supabase Edge Functions), the agent's middleware stack, and known fragility/gaps.
