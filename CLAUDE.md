# Restaurant Agent — Dot Cafe Bot

## Overview

A Matrix chat bot named **Zee** for **Dot Cafe** (specialty coffee shop, DHA Phase 4, Lahore). Customers interact via Matrix chat; the bot uses an AI agent to understand requests and sends rich UI cards via a custom DSL protocol.

- **Bot user:** `@dot_cafe:jaeno.ai`
- **Matrix server:** `https://chat.jaeno.ai`
- **Room:** `!NyMqNOqWDDsZLJsVAA:jaeno.ai`
- **LLM:** Gemini 2.5 Flash via LangChain/LangGraph

---

## Project Structure

```
Restaurant Agent/
├── main.py                     # Entry point
├── config.py                   # Matrix credentials, feature flags
├── db.py                       # Database layer (SQLite + Supabase)
├── agent/
│   ├── agent.py                # LangGraph agent assembly
│   ├── llm.py                  # Gemini 2.5 Flash config + rate limiter
│   ├── tools.py                # Agent tools (menu, order, reservation, history)
│   ├── prompt.py               # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, retry, summarization, PII
│   ├── memory_tools.py         # Customer name/phone persistence
│   ├── state.py                # Shared MENU_PRICES / CACHED_MENU (currently unpopulated — no code writes to it yet)
│   ├── context.py              # Context dataclass (user_id) — currently unused; context_schema is never passed to create_agent
│   └── ordering_config.py      # Master ordering switch + per-item overrides
└── bot/
    ├── matrix_client.py        # AsyncClient wrapper + send_text helper
    ├── message_handler.py      # Main message router + agent orchestration
    ├── menu_service.py         # Sends menu/item/category DSL cards
    ├── payment_service.py      # Swich gateway integration
    ├── order_confirmation_service.py  # Order receipt card
    ├── order_history_service.py       # Order history card
    ├── review_service.py       # Review card
    ├── review_scheduler.py     # Async scheduler for post-order reviews
    └── dsl_validator.py        # JSON schema validation for DSL payloads
```

---

## Architecture & Data Flow

```
Matrix Room
    │
    ▼
message_handler.py
    │
    ├── Fast-path patterns (bypass agent)
    │   ├── "menu"           → send_menu()
    │   ├── "order history"  → send_order_history_card()
    │   ├── "pay/payment"    → send_existing_payment_card()
    │   ├── "cancel"         → cancel pending payment intent
    │   └── DSL events       → payment_success / review_submit / order_summary
    │
    └── AI agent (everything else)
            │
            ▼
        LangGraph Agent (agent.py)
            ├── Middleware stack
            └── Tools → signal strings in response
                    │
                    ├── MENU_TRIGGERED       → send_menu()
                    ├── ITEM_TRIGGERED|name  → send_item_card()
                    ├── CATEGORY_TRIGGERED|name → send_category_card()
                    ├── PAYMENT_TRIGGERED|...   → send_order_confirmation_card()
                    │                            + create_payment_intent()
                    │                            + schedule_review()
                    └── ORDER_HISTORY_TRIGGERED → send_order_history_card()
```

---

## Database

**SQLite** (`restaurant.db`) — local, synchronous:
| Table | Purpose |
|---|---|
| `orders` | Customer orders (name, phone, items, total, status, room_id, stable_order_id) |
| `reservations` | Table bookings |
| `menu_items` | Menu (local cache) |
| `settings` | Key/value store (ordering_enabled flag) |
| `item_ordering` | Per-item ordering overrides |

**Supabase** — remote, async:
| Table | Purpose |
|---|---|
| `payment_intents` | Payment records (created by Supabase Edge Function) |
| `reviews` | Customer reviews |
| `review_queue` | Scheduled review card sends |
| `menu_items` | Authoritative menu source (fetched by agent at order time) |

Orders are saved to SQLite first, then a `stable_order_id` (e.g. `ORD-AB1C2D`) is generated and linked to the Supabase payment intent.

---

## DSL Protocol

All rich UI cards are sent as Matrix `m.room.message` events with an `ai.jaeno.dsl` field. Payloads are validated against a JSON schema at `../dsl-spec/schemas/v1/schema.json` before sending.

| DSL type | Version | Description |
|---|---|---|
| `menu` | v2 | Full menu grouped by category |
| `menu_item` | v1 | Single item with image and price |
| `menu_category` | v1 | All items in one category |
| `order_confirmation` | v1 | Receipt card (before payment) |
| `payment` | v1 | Swich payment link card |
| `order_history` | v1 | List of past orders with payment status |
| `review` | v1 | Post-order review prompt |

Incoming DSL events from the client are routed by `dsl.type`:
- `payment_success` — marks order paid in Supabase
- `review_submit` — saves customer review
- `order_summary` — pre-built order from the menu card UI

---

## Agent

### LLM
- **Model:** `gemini-2.5-flash`
- **Temperature:** 0.4
- **Rate limit:** 0.5 req/s (max bucket 5)

### Tools

| Tool | Trigger | Returns |
|---|---|---|
| `show_menu` | Customer asks for menu | `"MENU_TRIGGERED"` |
| `show_item(item_name)` | Customer asks about one item | `"ITEM_TRIGGERED\|{name}"` |
| `show_category(category_name)` | Customer asks about a category | `"CATEGORY_TRIGGERED\|{name}"` |
| `confirm_order(items, customer_name, phone)` | After customer confirms order | `"ORDER_SAVED..."` + `"PAYMENT_TRIGGERED\|..."` |
| `confirm_reservation(date, time, guests, customer_name, phone)` | After customer confirms reservation | `"RESERVATION_SAVED..."` |
| `show_order_history()` | Customer asks for past orders | `"ORDER_HISTORY_TRIGGERED"` |
| `get_customer_info(user_id)` | At start of every order/reservation | Saved name + phone |
| `save_customer_info(user_id, name, phone)` | After collecting name + phone | Confirmation string |

### Middleware Stack (in order)
1. **`RestaurantGuardrail`** — blocks prompt injection keywords before the LLM
2. **`ModelRetryMiddleware`** — retries on 429/RESOURCE_EXHAUSTED with exponential backoff (max 4 retries, max 120s delay)
3. **`SummarizationMiddleware`** — summarizes conversation when >12 messages, keeps 6
4. **`ToolRetryMiddleware`** — retries `confirm_order` / `confirm_reservation` up to 3 times
5. **`PIIMiddleware`** — phone number PII handling (no masking applied to input/output)

---

## Ordering Control

Edit `agent/ordering_config.py` and restart the bot — no database, no live toggle:

```python
ORDERING_ENABLED = True          # False = entire menu is browse-only

ITEM_ORDERABLE_OVERRIDES = {
    "espresso": False,           # blocks ordering for this item only
}
```

---

## Payment (Swich Gateway)

**⚠️ This flow spans three codebases — Python bot, Flutter app, AND Deno/Supabase Edge Functions (separate repo). The Python bot has zero involvement past step 5.**

Flow:
1. `confirm_order` tool signals `PAYMENT_TRIGGERED` with amount, name, phone, order_id, line_items
2. `message_handler` generates a unique `stable_order_id` (e.g. `ORD-AB1C2D`)
3. `create_payment_intent()` calls Supabase Edge Function (`smooth-processor`) to create a `payment_intents` row, status `pending`
4. `build_payment_url()` constructs a signed Swich checkout URL (HMAC-SHA256)
5. `send_order_confirmation_card()` sends receipt DSL card (with "Pay Now" button) — Python's job ends here
6. Customer taps "Pay Now" → Flutter calls the `refresh-payment` Edge Function for a fresh checkout URL, opens it in an in-app WebView
7. **Real confirmation:** Swich's backend calls the `swich-callback` Edge Function directly (server-to-server, HMAC verified). This function flips `payment_intents.status` to `paid` in Supabase AND sends the `payment_confirmation` DSL card straight into the Matrix room via raw HTTP with `MATRIX_BOT_TOKEN` — bypassing the Python bot entirely.
8. Flutter's WebView-close redirect and Realtime listener on `payment_intents` are secondary UI sync only — not the source of truth.

**Dead code, do not rely on it:** Flutter's `_sendPaymentSuccessEvent()` sends a custom `com.jaino.payment_success` event, and `message_handler.py` has a `dsl_type == 'payment_success'` branch — but the event Flutter sends isn't nested under `ai.jaeno.dsl`, so the branch never matches. This path has never fired in production. Safe to remove, or wire up properly if a fallback is ever wanted.

Cancellation: any message containing "cancel" cancels the pending payment intent (Python-side only, doesn't touch the edge functions).

---

## Review Scheduler

After each paid order (if `REVIEW_CARD_ENABLED=true`):
1. `schedule_review()` inserts a row in Supabase `review_queue` with `send_at = now + 120s`
2. `run_review_scheduler()` (background asyncio task, polls every 60s) checks for due reviews and sends the review DSL card
3. Customer submits a rating via `review_submit` DSL event, saved to Supabase `reviews`

---

## Environment Variables (`.env`)

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service key |
| `SUPABASE_ANON_KEY` | Supabase anon key (for REST calls) |
| `SWICH_CLIENT_ID` | Swich payment gateway client ID |
| `SWICH_SECRET_KEY` | Swich HMAC signing secret |
| `BUSINESS_NAME` | Displayed on payment cards — ⚠️ there's a SEPARATE `BUSINESS_NAME` env var on the `swich-callback` Edge Function (different repo/secrets store). Not shared; can silently drift out of sync. |
| `REVIEW_CARD_ENABLED` | `"true"` / `"false"` (default `"true"`) |
| `PAYMENT_CARD_ENABLED` | Feature flag for payment card |
| `LANGCHAIN_API_KEY` | LangSmith tracing key |

---

## Running

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt   # (if requirements.txt exists)
python main.py
```

Startup sequence:
1. `init_db()` — creates SQLite tables if missing
2. Matrix login + full sync
3. Event callbacks registered
4. Review scheduler started as background task
5. `sync_forever()` — main event loop

---

## Conversation History

Per-user conversation history is kept in memory (`conversation_histories` dict):
- Last 6 messages injected into each agent call
- Capped at 20 messages per user
- Lost on bot restart (no persistence)

---

## Key Design Decisions

- **Signal strings** — tools return sentinel strings (e.g. `PAYMENT_TRIGGERED|...`) instead of side effects; `message_handler` parses them and dispatches. This keeps tools pure and testable.
- **Split DB** — orders/reservations in SQLite (always available, no network), payments/reviews in Supabase (need real-time access from mobile clients).
- **stable_order_id** — a human-readable ID (`ORD-XXXXXX`) separate from the SQLite auto-increment, used as the Supabase payment intent key.
- **Fast-path bypass** — common intents (menu, pay, order history, cancel) are intercepted before the agent to reduce latency and LLM cost.
- **DSL validation** — all outbound DSL payloads are validated against a JSON schema before sending to prevent malformed UI cards reaching clients.



---

## Known Fragility / Gaps

1. **Name-matching between Supabase and SQLite** for per-item ordering — plain text match on lowercased item name, no foreign keys. Typo or rename in Supabase silently defaults the item back to orderable.
2. **No admin/staff permission check** anywhere — `ordering_config.py` mitigates this for ordering toggles (hand-edited, restart-required, no chat exposure), but is worth keeping in mind for any future admin-style feature.
3. **`orders` table (SQLite) vs. `stable_order_id`** — confirm whether `update_order_room_id` / `update_order_stable_id` fully reconcile these now, or if older rows predate the random-ID scheme and still carry the old DB-generated ID.
4. **Schema/DSL version drift** — any new DSL type or field needs simultaneous updates to the sender, `schema.json` (Python-validated path only), and the Dart handler. DSL sent from Edge Functions bypasses Python's `safe_send_dsl()` entirely — no schema enforcement on that path.
