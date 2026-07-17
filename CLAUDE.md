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
├── db/                         # Database layer (SQLite + Supabase), one file per domain
│   ├── __init__.py             # Re-exports every function so `from db import X` keeps working; assembles init_db()
│   ├── connection.py           # Shared _connect() / _get_supabase() / fuzzy_match_key()
│   ├── orders.py               # Orders + reservations
│   ├── customers.py            # Saved name/phone lookup
│   ├── menu.py                 # Menu items + ordering on/off switches
│   ├── payments.py             # Supabase payment_intents CRUD
│   ├── reviews.py               # Supabase reviews + review queue
│   ├── polls.py                 # Polls, poll answers, item ratings
│   ├── auctions.py              # Auctions, bids, the locked-transaction bid logic
│   └── conversation_history.py  # load_history/save_history — per-user chat history as a JSON blob
├── agent/
│   ├── agent.py                # LangGraph agent assembly
│   ├── llm.py                  # Gemini 2.5 Flash config + rate limiter
│   ├── prompt.py                # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, summarization, retry, PII, call limits
│   ├── memory_tools.py         # Customer name/phone persistence (get/save_customer_info tools)
│   ├── state.py                # Shared MENU_PRICES cache, populated by confirm_order.py at order time
│   ├── context.py              # Context dataclass (user_id) — passed as context_schema to create_agent
│   ├── ordering_config.py      # Master ordering switch + per-item overrides
│   └── tools/                  # One file per agent tool, grouped by domain
│       ├── menu/                # show_menu.py, show_item.py, show_category.py
│       ├── orders/              # confirm_order.py, confirm_reservation.py, show_order_history.py
│       ├── polls/               # send_single_choice_poll.py, send_flavor_preference_poll.py,
│       │                        # send_ranking_poll.py, send_rating_poll.py,
│       │                        # send_special_instructions_poll.py, show_poll_history.py
│       └── show_banner.py       # Single tool, stays at root (no domain group needed)
└── bot/
    ├── matrix_client.py        # AsyncClient wrapper + send_text helper
    ├── dsl_validator.py        # JSON schema validation for DSL payloads
    ├── banner_service.py       # Banner DSL card
    ├── message_handler.py      # Thin entrypoint: handle_message() + handle_custom_event(), routes to bot/router/
    ├── router/                 # Extracted message_handler internals — state machine, agent glue, event routing
    │   ├── state.py             # The shared in-memory dicts (conversation_histories, order_flows, pending_orders, etc.)
    │   ├── ids.py                # Order/tip ID generation (ORD-XXXXXX, TIP-XXXXXX)
    │   ├── order_flow.py         # The deterministic per-item size/instructions/confirm checkout state machine
    │   ├── agent_invoke.py       # Agent retry wrapper (handles blank-generation retries)
    │   ├── agent_dispatch.py     # Parses tool signal strings out of agent output, dispatches the matching card
    │   ├── dsl_text_events.py    # Inbound DSL-over-text handling: tip_selected/declined, order_summary,
    │   │                          # bid_confirmation, review_submit, fulfillment_selection/name_response/
    │   │                          # car_response/address_response
    │   └── custom_events.py      # handle_custom_event: poll responses, review submit, legacy native polls
    ├── menu/
    │   └── menu_service.py      # Sends menu/item/category DSL cards
    ├── orders/
    │   ├── order_confirmation_service.py  # Order receipt card (v1 + v2 with fulfillment summary)
    │   ├── order_history_service.py       # Order history card
    │   └── fulfillment_service.py         # fulfillment_method/name_request/car_request/address_request/
    │                                       # order_status DSL senders + send_order_status_update() (staff trigger)
    ├── payment/
    │   ├── payment_service.py   # Swich gateway integration (orders AND tips)
    │   └── tip_service.py       # Sends the post-order tip_request card
    ├── polls/
    │   ├── poll_service.py      # Single-choice poll cards (size, Yes/No confirm, etc.)
    │   ├── flavor_poll_service.py  # Multi-select flavor preference poll
    │   ├── ranking_poll_service.py # Drag-to-reorder poll
    │   ├── rating_poll_service.py  # Post-order star rating poll (per item)
    │   ├── special_instructions_poll_service.py  # Free-text "special instructions" poll
    │   ├── poll_history_service.py # "Show my poll history" card
    │   └── poll_results_service.py # Aggregate results follow-up card (currently rating only)
    ├── reviews/
    │   ├── review_service.py    # Review card
    │   └── review_scheduler.py  # Async scheduler for post-order reviews
    └── auction/
        ├── auction_service.py    # auction/auction_result DSL cards + create_and_send_auction() creation helper
        └── auction_scheduler.py  # Async scheduler: closes due auctions, pays out the winner, notifies every bidder
```

`bot/`, `agent/tools/`, and `db.py` were all reorganized into these domain-grouped packages for clean architecture — matrix_client.py/dsl_validator.py/banner_service.py/message_handler.py stayed at `bot/` root as shared infra rather than being folded into a domain, since they're used across every domain rather than owned by one. `message_handler.py` itself dropped from ~1,380 lines to ~220 by extracting the checkout state machine, agent dispatch, and DSL event routing into `bot/router/` — every extraction was a pure move (function bodies copied verbatim, only imports rewired), no logic changed.

---

## Architecture & Data Flow

```
Matrix Room
    │
    ▼
message_handler.py
    │
    ├── Fast-path patterns (bypass agent)
    │   ├── "menu"                → send_menu()
    │   ├── "order history"       → send_order_history_card()
    │   ├── "pay/payment"         → send_existing_payment_card()
    │   ├── "cancel"              → cancel pending payment intent
    │   └── DSL events            → review_submit / order_summary / tip_selected / tip_declined
    │
    ├── Deterministic order flow (order_flows state machine, NO LLM)
    │   "I want to order: ..." →  size poll (per item)
    │                          →  special-instructions poll (per item)
    │                          →  "reuse saved name/phone?" poll
    │                          →  (fresh name/phone via plain text, if declined/missing)
    │                          →  fulfillment_method card (Dine-in/Pickup/Car/Delivery)
    │                          →  method detail card (name_request, or car/address_request
    │                          │   → client-side saved-vehicle/address picker)
    │                          →  final "Shall I proceed?" poll   [now the true last step]
    │                          →  confirm_order.invoke() called directly
    │                          →  ORDER_SAVED → order_confirmation v2 card (with fulfillment
    │                                            summary) + payment intent
    │                                         → rating poll(s)
    │                                         → tip_request card
    │
    ├── Order status (staff-triggered, NO LLM, NO chat exposure)
    │   update_order_status.py <order_id> <status> → send_order_status_update()
    │                          →  looks up the order's room (db.get_order_by_stable_id)
    │                          →  order_status card (preparing/ready/on_the_way/delivered)
    │
    └── AI agent (free-form chat, reservations, menu Q&A, item ordering when the
        item list can't be parsed deterministically)
            │
            ▼
        LangGraph Agent (agent.py)
            ├── Middleware stack
            └── Tools → signal strings in response
                    │
                    ├── MENU_TRIGGERED            → send_menu()
                    ├── ITEM_TRIGGERED|name       → send_item_card()
                    ├── CATEGORY_TRIGGERED|name   → send_category_card()
                    ├── PAYMENT_TRIGGERED|...     → send_order_confirmation_card()
                    │                               + create_payment_intent() + schedule_review()
                    ├── ORDER_HISTORY_TRIGGERED   → send_order_history_card()
                    ├── POLL_TRIGGERED|...        → send_single_choice_poll_to_room()
                    ├── MULTI_POLL_TRIGGERED|...  → send_flavor_preference_poll_to_room()
                    ├── RANKING_POLL_TRIGGERED|.. → send_ranking_poll_to_room()
                    ├── OPEN_POLL_TRIGGERED|...   → send_special_instructions_poll_to_room()
                    ├── RATING_POLL_TRIGGERED|... → send_rating_poll_to_room() (one card per item)
                    └── POLL_HISTORY_TRIGGERED    → send_poll_history_card()
```

**Why a deterministic order flow exists at all:** the agent has no checkpointer — every poll answer is a fresh `agent.ainvoke()` with no real memory across turns. On repeat orders in the same conversation, the LLM would start imitating its own flattened text history and silently skip a tool call (e.g. reply with the size question as plain text instead of calling `send_single_choice_poll`) instead of progressing the flow. Since "which item still needs a size/instructions/confirmation answer" is fully mechanical, that entire sequence — including calling `confirm_order` directly — is owned by code (`bot/router/order_flow.py`, driven by `bot/message_handler.py`), and only hands off to the LLM for parts that genuinely need judgment (free-form chat, reservations, or an order whose item list couldn't be parsed).

---

## Database

The `db/` package re-exports every function through `db/__init__.py`, so every call site elsewhere in the repo still does `from db import save_order, get_customer, ...` unchanged — only the internal file layout is split by domain (see Project Structure above). `init_db()` assembles the schema by calling each domain module's `init_*_schema(cur)` against one shared connection, in place of the single monolithic function it used to be.

**SQLite** (`restaurant.db`) — local, synchronous:
| Table | Purpose |
|---|---|
| `orders` | Customer orders (name, phone, items, total, status, room_id, stable_order_id, fulfillment_method, fulfillment_summary) |
| `reservations` | Table bookings |
| `menu_items` | Menu (local cache) |
| `settings` | Key/value store (ordering_enabled flag) |
| `item_ordering` | Per-item ordering overrides |
| `customers` | Per-user saved name/phone (`get_customer`/`save_customer`) |
| `polls` | Native Matrix poll metadata (question, options, poll_type) |
| `poll_answers` | Every DSL/native poll answer, for poll history + results |
| `item_ratings` | Post-order star ratings per item |
| `auctions` | Auction metadata (title, starting_price, min_bid, ends_at, room_id, closed flag) |
| `auction_bids` | One row per (auction_id, user_id) — a rebid updates the row in place rather than inserting a new one |
| `conversation_history` | One row per user_id — their chat history as a JSON blob, so it survives a restart |

**Supabase** — remote, async:
| Table | Purpose |
|---|---|
| `payment_intents` | Payment records for orders, tips, AND auction winners (created by Supabase Edge Function), keyed by `order_id` (`ORD-XXXXXX`, `TIP-XXXXXX`, or `AUC-XXXXXX`) |
| `reviews` | Customer reviews |
| `review_queue` | Scheduled review card sends |
| `menu_items` | Authoritative menu source (fetched by agent at order time) |

Orders are saved to SQLite first, then a `stable_order_id` (e.g. `ORD-AB1C2D`) is generated and linked to the Supabase payment intent. Tips reuse the exact same `payment_intents` mechanism with a `TIP-XXXXXX` id instead — no separate tips table. Auction winners reuse it again with the auction's own `AUC-XXXXXX` id as the `order_id` — the auction_id IS the payment order_id, no separate ID generation needed.

---

## DSL Protocol

All rich UI cards are sent as Matrix `m.room.message` events with an `ai.jaeno.dsl` field. Payloads are validated against a JSON schema at `../dsl-spec/schemas/v1/schema.json` before sending — this only covers **outbound** sends (`safe_send_dsl()`); inbound DSL events from the client are not schema-validated.

| DSL type | Version | Description |
|---|---|---|
| `menu` | v2 | Full menu grouped by category |
| `menu_item` | v1 | Single item with image and price |
| `menu_category` | v1 | All items in one category |
| `order_confirmation` | v1 | Receipt card (before payment) — no fulfillment info |
| `order_confirmation` | v2 | Same receipt, plus an optional `fulfillment: {method, summary}` block. Additive-only; v1 stays registered/unmodified for in-flight messages that predate the fulfillment flow |
| `payment` | v1 | Swich payment link card (orders and tips) |
| `order_history` | v1 | List of past orders with payment status |
| `review` | v1 | Post-order review prompt |
| `poll` | v1 | Single-choice poll card (size, Yes/No, etc.) |
| `poll_response` | v1 | Inbound-only: customer's answer to a `poll` card |
| `poll_history` | v1 | "Show my poll history" card |
| `poll_results` | v1 | Aggregate results follow-up (rating average; `results` breakdown array supported but currently unused for other poll types) |
| `tip_request` | v1 | Post-order tip prompt (presets, custom amount, decline) |
| `auction` | v1 | Live auction card (image, starting price, min bid, countdown) |
| `auction_result` | v1 | Inbound trigger is client-side (`bid_confirmation`); this outbound card is sent per-bidder when an auction closes — `is_winner` + a `Pay Now` button (via `order_id`) for the winner only |
| `fulfillment_method` | v1 | 4-button picker (Dine-in/Pickup/Car/Delivery), sent right after customer info is settled |
| `fulfillment_selection` | v1 | Inbound-only: customer's method choice |
| `name_request` | v1 | Dine-in/Pickup name prompt |
| `name_response` | v1 | Inbound-only: the name entered |
| `car_request` | v1 | Opens the client's saved-vehicle picker/add sheet (reads/writes Supabase `vehicles`, client-owned — the agent never touches that table) |
| `car_response` | v1 | Inbound-only: make/model/color/plate_number (+ optional label) shared |
| `address_request` | v1 | Opens the client's saved-address picker/add sheet (reads/writes Supabase `addresses`, same client-owned pattern as `vehicles`) |
| `address_response` | v1 | Inbound-only: line1/city (+ optional line2/notes/label) shared |
| `order_status` | v1 | Staff-pushed order lifecycle card — `preparing`/`ready`/`on_the_way`/`delivered` get a dedicated icon+label client-side, any other string falls back to a generic icon + title-cased label |

Incoming DSL events from the client are routed by `dsl.type`:
- `review_submit` — saves customer review
- `order_summary` — pre-built order from the menu card UI, feeds into the deterministic order flow
- `tip_selected` — customer picked a preset/custom tip amount → creates a real payment intent + sends a `payment` card (no LLM)
- `tip_declined` — customer skipped the tip → plain text ack (no LLM)
- `bid_confirmation` — customer placed/updated a bid on a live auction → re-validated server-side against the current highest bid (the Flutter client's own min-bid check is cosmetic only) via `db.place_bid_if_higher()`, then upserted into `auction_bids` (no LLM)
- `fulfillment_selection` / `name_response` / `car_response` / `address_response` — the fulfillment flow's inbound half, see Order Fulfillment section below (no LLM)
- `payment_success` — **dead code**, kept for reference only (see Payment section)

**Item descriptions:** `menu` v2 / `menu_category` nested items and the `menu_item` card all carry a `description` string, sourced from the Supabase `menu_items.description` column. The Flutter grid card and item-detail screen read `item['description']` straight off the map (no generated model for nested items), so the schema tells you nothing about what the client renders there — check the Dart. Known gap: `_HighlightedItemCard` (the `menu_item` v1 card) takes `description` as a constructor param and never renders it, so that one card still shows nothing.

**Gotcha already hit and fixed:** `menu`/`menu_category`'s nested item `price` field must be a **string** (`str(i["price"])`), matching `menu_item_card_data`'s convention and what the Dart-generated model (`MenuV2Item.price: String`) expects — `send_menu()`/`send_category_card()` were sending the raw SQLite/Supabase numeric value, which passed Python-side schema validation (the nested `menu_item` def had no type constraint on `price`) but threw on the client's stricter generated parser, silently falling back to an "Unsupported" card. `schema.json` now explicitly types it `string` too, so `safe_send_dsl()` catches this class of bug server-side going forward.

---

## Agent

### LLM
- **Model:** `gemini-2.5-flash`
- **Temperature:** 0.4
- **Rate limit:** 0.5 req/s (max bucket 5)
- **Reasoning:** disabled (`thinking_budget=0`)

### Tools

| Tool | Trigger | Returns |
|---|---|---|
| `show_menu` | Customer asks for menu | `"MENU_TRIGGERED"` |
| `show_item(item_name)` | Customer asks about one item | `"ITEM_TRIGGERED\|{name}"` |
| `show_category(category_name)` | Customer asks about a category | `"CATEGORY_TRIGGERED\|{name}"` |
| `confirm_order(items, customer_name, phone)` | After customer confirms order (LLM-driven fallback path only — the normal path calls this directly, see Architecture) | `"ORDER_SAVED..."` + `"PAYMENT_TRIGGERED\|..."` |
| `confirm_reservation(date, time, guests, customer_name, phone)` | After customer confirms reservation | `"RESERVATION_SAVED..."` |
| `show_order_history()` | Customer asks for past orders | `"ORDER_HISTORY_TRIGGERED"` |
| `get_customer_info(user_id)` | At start of every order/reservation (LLM-driven path); the deterministic order flow calls `db.get_customer` directly instead | Saved name + phone |
| `save_customer_info(user_id, name, phone)` | After collecting name + phone | Confirmation string |
| `send_single_choice_poll(question, options)` | Size, Yes/No, or any single-pick question | `"POLL_TRIGGERED\|..."` |
| `send_flavor_preference_poll()` | Customer unsure what to order | `"MULTI_POLL_TRIGGERED\|..."` |
| `send_ranking_poll(question, options)` | Flavor tie-break, or "can't decide between X and Y" | `"RANKING_POLL_TRIGGERED\|..."` |
| `send_special_instructions_poll(item_name, placeholder)` | Per-item special instructions (LLM-driven fallback path) | `"OPEN_POLL_TRIGGERED\|..."` |
| `send_rating_poll(item_name)` | Immediately after `ORDER_SAVED` | `"RATING_POLL_TRIGGERED\|..."` |
| `show_poll_history()` | Customer asks to see their past poll answers | `"POLL_HISTORY_TRIGGERED"` |
| `show_banner(variant, title, message, meta)` | Visual callout (outage, warning, success, info) | `"BANNER_TRIGGERED\|..."` |

### Middleware Stack (in order)
1. **`RestaurantGuardrail`** — blocks banned keywords (prompt-injection phrases) before the LLM; checks the customer's **latest** human message
2. **`SummarizationMiddleware`** — summarizes conversation when it exceeds ~8000 tokens, keeps last 6 (rarely triggers in practice since `message_handler.py` already trims to the last 20 messages before invoking)
3. **`ToolRetryMiddleware`** — retries `confirm_order` / `confirm_reservation` up to 3 times
4. **`PIIMiddleware`** — phone-number detector configured but currently a no-op (`strategy="none"`, `apply_to_input=False`, `apply_to_output=False`)
5. **`ModelCallLimitMiddleware`** — caps a single `agent.ainvoke()` run at 10 model calls (`exit_behavior="end"`)
6. **`ToolCallLimitMiddleware`** — caps a single run at 10 tool calls (`exit_behavior="continue"`)

There is intentionally **no separate model-retry middleware** — the Gemini SDK's own `max_retries=4` (in `agent/llm.py`) already covers 429/5xx/network errors with its own backoff; a `ModelRetryMiddleware` layer was removed because its up-to-120s backoff schedule was getting killed mid-retry by the outer 30s timeout in `_invoke_agent_with_retry` (`message_handler.py`), causing the whole call to restart from scratch instead of completing one clean retry.

No checkpointer is configured — every turn is a fresh `agent.ainvoke()` call with `context=Context(user_id=sender)`; there is no cross-turn agent memory beyond what `message_handler.py` manually re-injects as message history.

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

Flow (orders):
1. `confirm_order` is invoked (directly by `bot/router/order_flow.py::_place_deterministic_order`, or by the LLM in the fallback path) and returns `PAYMENT_TRIGGERED` with amount, name, phone, order_id, line_items
2. `bot/router/agent_dispatch.py` generates a unique `stable_order_id` (e.g. `ORD-AB1C2D`) via `bot/router/ids.py`
3. `create_payment_intent()` calls Supabase Edge Function (`smooth-processor`) to create a `payment_intents` row, status `pending`
4. `build_payment_url()` constructs a signed Swich checkout URL (HMAC-SHA256)
5. `send_order_confirmation_card()` sends receipt DSL card (with "Pay Now" button) — Python's job ends here
6. Customer taps "Pay Now" → Flutter calls the `refresh-payment` Edge Function for a fresh checkout URL, opens it in an in-app WebView
7. **Real confirmation:** Swich's backend calls the `swich-callback` Edge Function directly (server-to-server, HMAC verified). This function flips `payment_intents.status` to `paid` in Supabase AND sends the `payment_confirmation` DSL card straight into the Matrix room via raw HTTP with `MATRIX_BOT_TOKEN` — bypassing the Python bot entirely.
8. Flutter's WebView-close redirect and Realtime listener on `payment_intents` are secondary UI sync only — not the source of truth.

**Dead code, do not rely on it:** Flutter's `_sendPaymentSuccessEvent()` sends a custom `com.jaino.payment_success` event, and `bot/router/dsl_text_events.py` has a `dsl_type == 'payment_success'` branch — but the event Flutter sends isn't nested under `ai.jaeno.dsl`, so the branch never matches. This path has never fired in production. Safe to remove, or wire up properly if a fallback is ever wanted.

Cancellation: any message containing "cancel" cancels the pending payment intent (Python-side only, doesn't touch the edge functions).

### Tips

After a successful order, `bot/payment/tip_service.py` automatically sends a `tip_request` card (presets `[50, 100, 200]` PKR + custom amount + decline option) — no LLM tool call, fired directly from `_place_deterministic_order` on `ORDER_SAVED`. When the customer responds:
- **Preset/custom amount** (`tip_selected` DSL event) → `bot/router/dsl_text_events.py` looks up the customer's saved name/phone (`db.get_customer`), generates a `TIP-XXXXXX` id, and calls `payment_service.send_payment_card()` — the exact same function used for order payments — so the tip gets a real Swich checkout card. Rejects amounts under 10 PKR (Swich's minimum).
- **Decline** (`tip_declined`) → plain text acknowledgment, nothing else happens.

No dedicated `tips` table exists yet — tip payments are just `payment_intents` rows distinguished by the `TIP-` prefix on `order_id`. If tip-specific reporting/analytics is ever needed, that's the natural next addition.

---

## Order Fulfillment

After the customer confirms their name/phone, the deterministic order flow asks how they want to receive the order — **before** the final "Shall I proceed?" confirmation, not after, so the receipt shows the whole picture in one go and "Yes" is the true last step:

1. `bot/router/order_flow.py::_start_fulfillment_stage()` generates the order's stable `order_id` **early** (not later in `agent_dispatch.py`, unlike the plain LLM-fallback path) and sends a `fulfillment_method` card
2. Client responds `fulfillment_selection` (dine_in/pickup/car/delivery) → `bot/router/dsl_text_events.py` sends the matching detail card — `name_request` (dine-in/pickup), or `car_request`/`address_request` (car/delivery — these open the client's own saved-vehicle/address picker)
3. Client responds `name_response`/`car_response`/`address_response` → the one-line `fulfillment_summary` is built server-side (e.g. `"White Toyota Corolla (ABC-123)"`, `"123 Main St, Apt 4, Lahore"`) and stashed on `order_flows[sender]`, then the **final** confirm poll fires
4. "Yes" → `_place_deterministic_order()` → `agent_dispatch.py` reuses the pre-generated `order_id` (instead of minting a fresh one) and sends `order_confirmation` **v2** with the fulfillment block, plus `db.update_order_fulfillment()` persisting `fulfillment_method`/`fulfillment_summary` on the `orders` row

**Saved addresses/vehicles are genuinely client-owned data** — Supabase `addresses`/`vehicles` tables, RLS-scoped to `auth.uid()`, written directly by the Flutter client's own "Add new" form. The agent never reads or writes those tables; it only ever receives the *result* via `car_response`/`address_response`. This is a deliberate, explicit exception to "the agent owns order data."

### Order Status

No admin UI exists yet — `update_order_status.py` is the trigger, run manually from a terminal, same "no chat exposure, no LLM" pattern as auction creation (see `test.py`):

```bash
python update_order_status.py ORD-AB12CD preparing
python update_order_status.py ORD-AB12CD ready
python update_order_status.py ORD-AB12CD delivered
```

Under the hood: `send_order_status_update(order_id, status, message)` (`bot/orders/fulfillment_service.py`) resolves `order_id → room_id` via `db.get_order_by_stable_id()` and sends the `order_status` card — `preparing`/`ready`/`on_the_way`/`delivered` get a dedicated icon+label client-side, anything else falls back to a generic icon + title-cased label instead of an "unsupported DSL" card.

---

## Review Scheduler

After each paid order (if `REVIEW_CARD_ENABLED=true`):
1. `schedule_review()` inserts a row in Supabase `review_queue` with `send_at = now + 120s`
2. `run_review_scheduler()` (background asyncio task, polls every 60s) checks for due reviews and sends the review DSL card
3. Customer submits a rating via `review_submit` DSL event, saved to Supabase `reviews`

This is separate from the per-item star **rating polls** (`send_rating_poll` / `RATING_POLL_TRIGGERED`), which fire immediately after `ORDER_SAVED` and save to SQLite `item_ratings`, not Supabase `reviews`.

---

## Auctions

**Creation:** no chat command or admin UI exists yet — `bot/auction/auction_service.py::create_and_send_auction()` is called directly (see `test.py` for a working example) to insert a SQLite `auctions` row and send the `auction` DSL card. Swap in a real trigger (staff chat command, Supabase-polling like the menu, or an in-app staff screen) later without touching bidding/closing at all — this was a deliberate scope cut, see Known Fragility/Gaps.

**Bidding (deterministic, no LLM):**
1. Customer taps "Place Bid" in the app → client sends a `bid_confirmation` DSL event (the app's own min-bid check is client-side only, not trustworthy)
2. `bot/router/dsl_text_events.py` re-validates via `db.place_bid_if_higher()` — one `BEGIN IMMEDIATE` SQLite transaction that reads the current highest bid and writes the new one atomically, so two near-simultaneous bids can't both read the same stale "highest" and both get accepted
3. A rebid from the same customer updates their existing `auction_bids` row (`UNIQUE(auction_id, user_id)` + `ON CONFLICT ... DO UPDATE`) rather than inserting a new one
4. Bids at or before the auction's `ends_at`, or after it's been marked `closed`, are rejected with a reason string sent back as plain text

**Closing (`bot/auction/auction_scheduler.py::run_auction_scheduler()`, background asyncio task, polls every 30s — same shape as the review scheduler):**
1. Finds auctions where `closed = 0` and `ends_at` has passed (filtered in Python, not SQL, to dodge SQLite `datetime('now')` vs. ISO-string format mismatches — see `get_open_auctions_past_end()`)
2. Marks the auction closed, computes the highest bidder
3. For the winner: looks up their saved name/phone (`db.get_customer` — skipped with a logged error if never saved, same rule tips already follow) and calls `payment_service.create_payment_intent()`, reusing the exact same Swich mechanism as orders/tips. The auction_id itself doubles as the payment `order_id` — no separate ID generation.
4. Sends every bidder (not just the winner) a personalized `auction_result` card — `is_winner` and the "Pay Now" button are per-recipient, so this is one `room_send` per bidder, not a single room broadcast

**Known gotcha already hit and fixed:** amounts pulled from SQLite (`REAL` column) come back as Python floats — Matrix's canonical JSON forbids raw floats in event content (same reason `poll_results_data.average_x10`/`percent` are ints, see DSL Protocol). `winning_amount`, `starting_price`, and `min_bid` are explicitly cast to `int` right before they go into the DSL payload in `bot/auction/auction_service.py`/`bot/auction/auction_scheduler.py`. If you add a new call site that sends these fields, cast there too.

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
5. Auction scheduler started as background task
6. `sync_forever()` — main event loop

---

## Conversation History

Per-user conversation history lives in the `conversation_histories` dict, write-through cached to the SQLite `conversation_history` table (`db/conversation_history.py`):
- Last 20 messages injected into each agent call
- Capped at 20 messages per user — the cap lives in `persist_history()` (`bot/router/state.py`), **not** at each call site: the poll paths in `custom_events.py`/`order_flow.py` append without capping, and persistence would turn that into an unbounded row on disk
- **Survives a bot restart.** `ensure_history_loaded(sender)` reloads from SQLite on first touch per sender; `persist_history(sender)` must be called after every mutation
- Writes are synchronous SQLite on the asyncio loop — one small blob per message, fine at this scale, revisit if it ever shows up in latency

The deterministic order-flow state (`order_flows`, `pending_orders`, `last_orders`, `last_order_line`/`last_order_state`, `awaiting_reorder_confirmation`) is still **in-memory only**, living alongside the history dict in `bot/router/state.py`. A bot restart mid-checkout loses that state — a known gap, not yet fixed. Conversation history's `ensure_history_loaded`/`persist_history` pair is the working template for the planned fix (a write-through SQLite table for `order_flows` keyed by `user_id`), which is why history got done first.

---

## Key Design Decisions

- **Signal strings** — tools return sentinel strings (e.g. `PAYMENT_TRIGGERED|...`) instead of side effects; `bot/router/agent_dispatch.py` parses them and dispatches. This keeps tools pure and testable.
- **Deterministic order flow over LLM tool-calling** — every step of checkout (size, instructions, customer-info reuse, final confirmation, placing the order) is driven by explicit code (`bot/router/order_flow.py`), not the LLM deciding to call a tool each turn. This was a deliberate fix: with no checkpointer, the LLM would silently drop tool calls (replying with plain text instead of sending a poll card) on repeat orders in the same conversation. The LLM is only in the loop for genuinely open-ended parts (menu Q&A, reservations, free-form chat, or an order whose item list can't be parsed).
- **Split DB** — orders/reservations in SQLite (always available, no network), payments/reviews in Supabase (need real-time access from mobile clients).
- **Domain-grouped packages over flat directories** — `bot/`, `agent/tools/`, and `db.py` were reorganized (this session) into subfolders/files per domain (polls, menu, orders, payment, reviews, auction) instead of one flat pile of same-level files. Every move was a pure relocation — function bodies copied verbatim, cross-references rewired, public import surface (`from db import X`, `from bot.message_handler import handle_message`, etc.) kept unchanged — verified by actually importing every module afterward, not just checking syntax.
- **stable_order_id** — a human-readable ID (`ORD-XXXXXX`, or `TIP-XXXXXX` for tips) separate from the SQLite auto-increment, used as the Supabase payment intent key.
- **Fast-path bypass** — common intents (menu, pay, order history, cancel) are intercepted before the agent to reduce latency and LLM cost.
- **DSL validation** — all outbound DSL payloads are validated against a JSON schema before sending to prevent malformed UI cards reaching clients. Inbound DSL (customer responses) is not schema-validated.
- **Fulfillment before final confirm, not after** — the fulfillment method/detail cards fire right after customer info is settled, and the "Shall I proceed?" poll is deliberately the *last* step, once fulfillment details are already known, so the receipt reflects everything at once instead of the customer confirming before fulfillment even entered the picture.
- **Manual CLI triggers over premature admin UI** — both `create_and_send_auction()` and `send_order_status_update()` are called directly (via `test.py`/`update_order_status.py`), not exposed to the LLM or gated behind a permission system that doesn't exist yet. A real staff surface can call the same functions later without touching the underlying logic.

---

## Known Fragility / Gaps

1. **Name-matching between Supabase and SQLite** for per-item ordering — plain text match on lowercased item name, no foreign keys. Typo or rename in Supabase silently defaults the item back to orderable.
2. **No admin/staff permission check** anywhere — `ordering_config.py` mitigates this for ordering toggles (hand-edited, restart-required, no chat exposure), but is worth keeping in mind for any future admin-style feature. `HumanInTheLoopMiddleware` would be the right LangChain primitive if this becomes a priority.
3. **`orders` table (SQLite) vs. `stable_order_id`** — confirm whether `update_order_room_id` / `update_order_stable_id` fully reconcile these now, or if older rows predate the random-ID scheme and still carry the old DB-generated ID.
4. **Schema/DSL version drift** — any new DSL type or field needs simultaneous updates to the sender, `schema.json` (Python-validated outbound path only), and the Dart handler. DSL sent from Edge Functions bypasses Python's `safe_send_dsl()` entirely — no schema enforcement on that path.
5. **In-memory checkout state doesn't survive a restart** — `order_flows` and friends only; conversation history itself is now persisted (see Conversation History section above). Not yet fixed for checkout.
6. **No dedicated `tips` table** — tip payments live in `payment_intents` distinguished only by the `TIP-` prefix; fine for the payment mechanics, not great for tip-specific reporting.
7. **`poll_results` DSL's `results` breakdown array is only ever populated for rating polls** — the Flutter side already renders a generic percentage-bar breakdown for any poll type, but no Python service computes/sends that data for size/flavor/confirmation polls yet.
8. **No auction creation trigger** — `create_and_send_auction()` has to be called manually (see Auctions section); there's no chat command, Supabase-polling, or staff UI wired up yet. Deliberate scope cut, not an oversight.
9. **No permission check on who can create an auction or place a bid** — same class of gap as #2, just unmitigated here (ordering_config's "hand-edited, restart-required" approach doesn't apply to auctions since creation is a runtime function call). Anyone who can call `create_and_send_auction()` or send a `bid_confirmation` DSL event can act.
10. **Auction `payment_intents` reuse the winner's Matrix `user_id`/saved customer info** — same "no `tips` table"-style tradeoff as #6: fine for payment mechanics, no dedicated auction-analytics table if that's ever needed.
11. **No order_status trigger UI** — `update_order_status.py` is a manually-run CLI script (same deliberate scope cut as auction creation, gap #8); a real staff dashboard is the natural next step if this goes into an actual restaurant, calling the same `send_order_status_update()` function underneath.
12. **In-memory checkout state doesn't distinguish "mid-fulfillment" from any other in-progress stage** — a restart during `fulfillment_method`/`awaiting_name`/`awaiting_car`/`awaiting_address` loses that state exactly like every other `order_flows` stage (see gap #5); not a new gap, just confirming the fulfillment stages inherit the existing one.
