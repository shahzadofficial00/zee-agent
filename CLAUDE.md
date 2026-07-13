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
│   ├── prompt.py                # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, summarization, retry, PII, call limits
│   ├── memory_tools.py         # Customer name/phone persistence (get/save_customer_info tools)
│   ├── state.py                # Shared MENU_PRICES cache, populated by confirm_order.py at order time
│   ├── context.py              # Context dataclass (user_id) — passed as context_schema to create_agent
│   ├── ordering_config.py      # Master ordering switch + per-item overrides
│   └── tools/                  # One file per agent tool
│       ├── show_menu.py / show_item.py / show_category.py
│       ├── confirm_order.py / confirm_reservation.py
│       ├── show_order_history.py / show_poll_history.py
│       ├── send_single_choice_poll.py / send_flavor_preference_poll.py
│       ├── send_ranking_poll.py / send_rating_poll.py / send_special_instructions_poll.py
│       └── show_banner.py
└── bot/
    ├── matrix_client.py        # AsyncClient wrapper + send_text helper
    ├── message_handler.py      # Main message router, deterministic order/tip flow, agent orchestration
    ├── menu_service.py         # Sends menu/item/category DSL cards
    ├── payment_service.py      # Swich gateway integration (orders AND tips)
    ├── order_confirmation_service.py  # Order receipt card
    ├── order_history_service.py       # Order history card
    ├── tip_service.py          # Sends the post-order tip_request card
    ├── poll_service.py         # Single-choice poll cards (size, Yes/No confirm, etc.)
    ├── flavor_poll_service.py  # Multi-select flavor preference poll
    ├── ranking_poll_service.py # Drag-to-reorder poll
    ├── rating_poll_service.py  # Post-order star rating poll (per item)
    ├── special_instructions_poll_service.py  # Free-text "special instructions" poll
    ├── poll_history_service.py # "Show my poll history" card
    ├── poll_results_service.py # Aggregate results follow-up card (currently rating only)
    ├── review_service.py       # Review card
    ├── review_scheduler.py     # Async scheduler for post-order reviews
    ├── auction_service.py      # auction/auction_result DSL cards + create_and_send_auction() creation helper
    ├── auction_scheduler.py    # Async scheduler: closes due auctions, pays out the winner, notifies every bidder
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
    │                          →  final "Shall I proceed?" poll
    │                          →  confirm_order.invoke() called directly
    │                          →  ORDER_SAVED → order_confirmation card + payment intent
    │                                         → rating poll(s)
    │                                         → tip_request card
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

**Why a deterministic order flow exists at all:** the agent has no checkpointer — every poll answer is a fresh `agent.ainvoke()` with no real memory across turns. On repeat orders in the same conversation, the LLM would start imitating its own flattened text history and silently skip a tool call (e.g. reply with the size question as plain text instead of calling `send_single_choice_poll`) instead of progressing the flow. Since "which item still needs a size/instructions/confirmation answer" is fully mechanical, `message_handler.py` now owns that entire sequence — including calling `confirm_order` directly — and only hands off to the LLM for parts that genuinely need judgment (free-form chat, reservations, or an order whose item list couldn't be parsed).

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
| `customers` | Per-user saved name/phone (`get_customer`/`save_customer`) |
| `polls` | Native Matrix poll metadata (question, options, poll_type) |
| `poll_answers` | Every DSL/native poll answer, for poll history + results |
| `item_ratings` | Post-order star ratings per item |
| `auctions` | Auction metadata (title, starting_price, min_bid, ends_at, room_id, closed flag) |
| `auction_bids` | One row per (auction_id, user_id) — a rebid updates the row in place rather than inserting a new one |

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
| `order_confirmation` | v1 | Receipt card (before payment) |
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

Incoming DSL events from the client are routed by `dsl.type`:
- `review_submit` — saves customer review
- `order_summary` — pre-built order from the menu card UI, feeds into the deterministic order flow
- `tip_selected` — customer picked a preset/custom tip amount → creates a real payment intent + sends a `payment` card (no LLM)
- `tip_declined` — customer skipped the tip → plain text ack (no LLM)
- `bid_confirmation` — customer placed/updated a bid on a live auction → re-validated server-side against the current highest bid (the Flutter client's own min-bid check is cosmetic only) via `db.place_bid_if_higher()`, then upserted into `auction_bids` (no LLM)
- `payment_success` — **dead code**, kept for reference only (see Payment section)

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
2. **`SummarizationMiddleware`** — summarizes conversation when it exceeds ~8000 tokens, keeps last 6 (rarely triggers in practice since `message_handler.py` already trims to the last 12 messages before invoking)
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
1. `confirm_order` is invoked (directly by `message_handler._place_deterministic_order`, or by the LLM in the fallback path) and returns `PAYMENT_TRIGGERED` with amount, name, phone, order_id, line_items
2. `message_handler` generates a unique `stable_order_id` (e.g. `ORD-AB1C2D`)
3. `create_payment_intent()` calls Supabase Edge Function (`smooth-processor`) to create a `payment_intents` row, status `pending`
4. `build_payment_url()` constructs a signed Swich checkout URL (HMAC-SHA256)
5. `send_order_confirmation_card()` sends receipt DSL card (with "Pay Now" button) — Python's job ends here
6. Customer taps "Pay Now" → Flutter calls the `refresh-payment` Edge Function for a fresh checkout URL, opens it in an in-app WebView
7. **Real confirmation:** Swich's backend calls the `swich-callback` Edge Function directly (server-to-server, HMAC verified). This function flips `payment_intents.status` to `paid` in Supabase AND sends the `payment_confirmation` DSL card straight into the Matrix room via raw HTTP with `MATRIX_BOT_TOKEN` — bypassing the Python bot entirely.
8. Flutter's WebView-close redirect and Realtime listener on `payment_intents` are secondary UI sync only — not the source of truth.

**Dead code, do not rely on it:** Flutter's `_sendPaymentSuccessEvent()` sends a custom `com.jaino.payment_success` event, and `message_handler.py` has a `dsl_type == 'payment_success'` branch — but the event Flutter sends isn't nested under `ai.jaeno.dsl`, so the branch never matches. This path has never fired in production. Safe to remove, or wire up properly if a fallback is ever wanted.

Cancellation: any message containing "cancel" cancels the pending payment intent (Python-side only, doesn't touch the edge functions).

### Tips

After a successful order, `bot/tip_service.py` automatically sends a `tip_request` card (presets `[50, 100, 200]` PKR + custom amount + decline option) — no LLM tool call, fired directly from `_place_deterministic_order` on `ORDER_SAVED`. When the customer responds:
- **Preset/custom amount** (`tip_selected` DSL event) → `message_handler.py` looks up the customer's saved name/phone (`db.get_customer`), generates a `TIP-XXXXXX` id, and calls `payment_service.send_payment_card()` — the exact same function used for order payments — so the tip gets a real Swich checkout card. Rejects amounts under 10 PKR (Swich's minimum).
- **Decline** (`tip_declined`) → plain text acknowledgment, nothing else happens.

No dedicated `tips` table exists yet — tip payments are just `payment_intents` rows distinguished by the `TIP-` prefix on `order_id`. If tip-specific reporting/analytics is ever needed, that's the natural next addition.

---

## Review Scheduler

After each paid order (if `REVIEW_CARD_ENABLED=true`):
1. `schedule_review()` inserts a row in Supabase `review_queue` with `send_at = now + 120s`
2. `run_review_scheduler()` (background asyncio task, polls every 60s) checks for due reviews and sends the review DSL card
3. Customer submits a rating via `review_submit` DSL event, saved to Supabase `reviews`

This is separate from the per-item star **rating polls** (`send_rating_poll` / `RATING_POLL_TRIGGERED`), which fire immediately after `ORDER_SAVED` and save to SQLite `item_ratings`, not Supabase `reviews`.

---

## Auctions

**Creation:** no chat command or admin UI exists yet — `bot/auction_service.py::create_and_send_auction()` is called directly (see `test.py` for a working example) to insert a SQLite `auctions` row and send the `auction` DSL card. Swap in a real trigger (staff chat command, Supabase-polling like the menu, or an in-app staff screen) later without touching bidding/closing at all — this was a deliberate scope cut, see Known Fragility/Gaps.

**Bidding (deterministic, no LLM):**
1. Customer taps "Place Bid" in the app → client sends a `bid_confirmation` DSL event (the app's own min-bid check is client-side only, not trustworthy)
2. `message_handler.py` re-validates via `db.place_bid_if_higher()` — one `BEGIN IMMEDIATE` SQLite transaction that reads the current highest bid and writes the new one atomically, so two near-simultaneous bids can't both read the same stale "highest" and both get accepted
3. A rebid from the same customer updates their existing `auction_bids` row (`UNIQUE(auction_id, user_id)` + `ON CONFLICT ... DO UPDATE`) rather than inserting a new one
4. Bids at or before the auction's `ends_at`, or after it's been marked `closed`, are rejected with a reason string sent back as plain text

**Closing (`bot/auction_scheduler.py::run_auction_scheduler()`, background asyncio task, polls every 30s — same shape as the review scheduler):**
1. Finds auctions where `closed = 0` and `ends_at` has passed (filtered in Python, not SQL, to dodge SQLite `datetime('now')` vs. ISO-string format mismatches — see `get_open_auctions_past_end()`)
2. Marks the auction closed, computes the highest bidder
3. For the winner: looks up their saved name/phone (`db.get_customer` — skipped with a logged error if never saved, same rule tips already follow) and calls `payment_service.create_payment_intent()`, reusing the exact same Swich mechanism as orders/tips. The auction_id itself doubles as the payment `order_id` — no separate ID generation.
4. Sends every bidder (not just the winner) a personalized `auction_result` card — `is_winner` and the "Pay Now" button are per-recipient, so this is one `room_send` per bidder, not a single room broadcast

**Known gotcha already hit and fixed:** amounts pulled from SQLite (`REAL` column) come back as Python floats — Matrix's canonical JSON forbids raw floats in event content (same reason `poll_results_data.average_x10`/`percent` are ints, see DSL Protocol). `winning_amount`, `starting_price`, and `min_bid` are explicitly cast to `int` right before they go into the DSL payload in `auction_service.py`/`auction_scheduler.py`. If you add a new call site that sends these fields, cast there too.

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

Per-user conversation history is kept in memory (`conversation_histories` dict):
- Last 12 messages injected into each agent call
- Capped at 20 messages per user
- Lost on bot restart (no persistence)

The deterministic order-flow state (`order_flows`, `pending_orders`, `last_orders`, `last_order_line`/`last_order_state`, `awaiting_reorder_confirmation`) is **also** in-memory only, in `message_handler.py`. A bot restart mid-checkout currently loses that state — this is a known gap, not yet fixed (a write-through SQLite table for `order_flows` keyed by `user_id` is the planned fix, not yet implemented).

---

## Key Design Decisions

- **Signal strings** — tools return sentinel strings (e.g. `PAYMENT_TRIGGERED|...`) instead of side effects; `message_handler` parses them and dispatches. This keeps tools pure and testable.
- **Deterministic order flow over LLM tool-calling** — every step of checkout (size, instructions, customer-info reuse, final confirmation, placing the order) is driven by explicit code in `message_handler.py`, not the LLM deciding to call a tool each turn. This was a deliberate fix: with no checkpointer, the LLM would silently drop tool calls (replying with plain text instead of sending a poll card) on repeat orders in the same conversation. The LLM is only in the loop for genuinely open-ended parts (menu Q&A, reservations, free-form chat, or an order whose item list can't be parsed).
- **Split DB** — orders/reservations in SQLite (always available, no network), payments/reviews in Supabase (need real-time access from mobile clients).
- **stable_order_id** — a human-readable ID (`ORD-XXXXXX`, or `TIP-XXXXXX` for tips) separate from the SQLite auto-increment, used as the Supabase payment intent key.
- **Fast-path bypass** — common intents (menu, pay, order history, cancel) are intercepted before the agent to reduce latency and LLM cost.
- **DSL validation** — all outbound DSL payloads are validated against a JSON schema before sending to prevent malformed UI cards reaching clients. Inbound DSL (customer responses) is not schema-validated.

---

## Known Fragility / Gaps

1. **Name-matching between Supabase and SQLite** for per-item ordering — plain text match on lowercased item name, no foreign keys. Typo or rename in Supabase silently defaults the item back to orderable.
2. **No admin/staff permission check** anywhere — `ordering_config.py` mitigates this for ordering toggles (hand-edited, restart-required, no chat exposure), but is worth keeping in mind for any future admin-style feature. `HumanInTheLoopMiddleware` would be the right LangChain primitive if this becomes a priority.
3. **`orders` table (SQLite) vs. `stable_order_id`** — confirm whether `update_order_room_id` / `update_order_stable_id` fully reconcile these now, or if older rows predate the random-ID scheme and still carry the old DB-generated ID.
4. **Schema/DSL version drift** — any new DSL type or field needs simultaneous updates to the sender, `schema.json` (Python-validated outbound path only), and the Dart handler. DSL sent from Edge Functions bypasses Python's `safe_send_dsl()` entirely — no schema enforcement on that path.
5. **In-memory checkout state doesn't survive a restart** — see Conversation History section above. Not yet fixed.
6. **No dedicated `tips` table** — tip payments live in `payment_intents` distinguished only by the `TIP-` prefix; fine for the payment mechanics, not great for tip-specific reporting.
7. **`poll_results` DSL's `results` breakdown array is only ever populated for rating polls** — the Flutter side already renders a generic percentage-bar breakdown for any poll type, but no Python service computes/sends that data for size/flavor/confirmation polls yet.
8. **No auction creation trigger** — `create_and_send_auction()` has to be called manually (see Auctions section); there's no chat command, Supabase-polling, or staff UI wired up yet. Deliberate scope cut, not an oversight.
9. **No permission check on who can create an auction or place a bid** — same class of gap as #2, just unmitigated here (ordering_config's "hand-edited, restart-required" approach doesn't apply to auctions since creation is a runtime function call). Anyone who can call `create_and_send_auction()` or send a `bid_confirmation` DSL event can act.
10. **Auction `payment_intents` reuse the winner's Matrix `user_id`/saved customer info** — same "no `tips` table"-style tradeoff as #6: fine for payment mechanics, no dedicated auction-analytics table if that's ever needed.
