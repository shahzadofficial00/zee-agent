# Restaurant Agent — Dot Cafe Bot

## Overview

A Matrix chat bot named **Zee** for **Dot Cafe** (specialty coffee shop, DHA Phase 4, Lahore). Customers interact via Matrix chat; the bot uses an AI agent to understand requests and sends rich UI cards via a custom DSL protocol.

- **Bot user:** `@dot_cafe:jaeno.ai`
- **Matrix server:** `https://chat.jaeno.ai`
- **Room:** `!NyMqNOqWDDsZLJsVAA:jaeno.ai`
- **LLM:** MiniMax M2.7 (free tier) via OpenRouter (OpenAI-compatible client), LangChain/LangGraph

---

## Project Structure

```
Restaurant Agent/
├── main.py                     # Entry point — login/session restore, auto-join, callbacks, schedulers
├── config.py                   # Matrix credentials, feature flags
├── tests/                      # Offline self-checks. Run as modules from the repo root
│   │                           #   (`python -m tests.test_faq`) — a plain path invocation
│   │                           #   puts tests/ on sys.path instead of the root and can't
│   │                           #   import bot/db/agent.
│   ├── test_terms.py           # JNO-90/97/98 — 9 checks
│   ├── test_faq.py             # JNO-54/55/56 — 4 checks
│   ├── test_calculator.py      # JNO-233/234-237 — 10 checks
│   ├── test_menu.py            # Local menu — 4 checks (seed, int price, fields, idempotence)
│   ├── test_countdown.py       # JNO-84/85 — 3 checks (schema, optional omission, deadline rule)
│   └── test_checkout_state.py  # Gap #5 — 4 checks (round-trip, cleanup, TTL, hook wired)
│                               # ⚠️ test.py and test_status.py stay at the ROOT on purpose:
│                               #   despite the names they are manual triggers, not tests —
│                               #   they log into Matrix and fire real events at a live room
│                               #   (auction creation / order-status cards). Same class as
│                               #   update_order_status.py. Never run them as a suite.
├── dsl-spec/schemas/v1/schema.json  # In-repo copy of the DSL schema (see DSL Protocol)
├── store/                      # gitignored — E2EE olm keys (nio.db) + credentials.json
├── db/                         # Database layer (SQLite + Supabase), one file per domain
│   ├── __init__.py             # Re-exports every function so `from db import X` keeps working; assembles init_db()
│   ├── connection.py           # Shared _connect() / _get_supabase() / fuzzy_match_key()
│   ├── orders.py               # Orders + reservations
│   ├── customers.py            # Saved name/phone lookup
│   ├── menu.py                 # Menu items (local, + the _SEED_MENU constant) + ordering switches
│   ├── payments.py             # Supabase payment_intents CRUD
│   ├── reviews.py               # Supabase reviews + review queue
│   ├── polls.py                 # Polls, poll answers, item ratings
│   ├── auctions.py              # Auctions, bids, the locked-transaction bid logic
│   ├── conversation_history.py  # load_history/save_history — per-user chat history as a JSON blob
│   ├── checkout_state.py        # load/save/delete_checkout_state + CHECKOUT_TTL_MINUTES (gap #5)
│   ├── terms.py                 # Signed agreements (has_agreed/save_agreement/get_agreements)
│   └── faqs.py                  # FAQ rows + the match ladder (get_faqs/get_faq) + seed drafts
├── agent/
│   ├── agent.py                # LangGraph agent assembly
│   ├── llm.py                  # OpenRouter model config + rate limiter
│   ├── prompt.py                # System prompt for "Zee"
│   ├── middleware.py           # Guardrails, summarization, retry, PII, call limits
│   ├── memory_tools.py         # Customer name/phone persistence (get/save_customer_info tools)
│   ├── state.py                # Shared MENU_PRICES cache, populated by confirm_order.py at order time
│   ├── context.py              # Context dataclass (user_id) — passed as context_schema to create_agent
│   ├── ordering_config.py      # Master ordering switch + per-item overrides
│   └── tools/                  # One file per agent tool, grouped by domain
│       ├── menu/                # show_menu.py, show_item.py, show_category.py,
│       │                        # get_menu_prices.py (prices AS TEXT to the model)
│       ├── orders/              # confirm_order.py, confirm_reservation.py, show_order_history.py
│       ├── polls/               # send_single_choice_poll.py, send_flavor_preference_poll.py,
│       │                        # send_ranking_poll.py, send_rating_poll.py,
│       │                        # send_special_instructions_poll.py, show_poll_history.py
│       ├── faq/                 # show_faq.py, show_faqs.py
│       ├── calculator/          # send_calculator.py
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
    │   └── tip_service.py       # Sends the post-order tip_request card (gated by TIPS_ENABLED)
    ├── polls/
    │   ├── poll_service.py      # Single-choice poll cards (size, Yes/No confirm, etc.)
    │   ├── flavor_poll_service.py  # Multi-select flavor preference poll
    │   ├── ranking_poll_service.py # Drag-to-reorder poll
    │   ├── rating_poll_service.py  # Post-order star rating poll (per item, chained)
    │   ├── special_instructions_poll_service.py  # Free-text "special instructions" poll
    │   ├── poll_history_service.py # "Show my poll history" card
    │   └── poll_results_service.py # Aggregate results follow-up card (currently rating only)
    ├── reviews/
    │   ├── review_service.py    # Review card
    │   └── review_scheduler.py  # Async scheduler for post-order reviews
    ├── terms/
    │   └── terms_service.py     # terms DSL card + the terms text itself (a constant) +
    │                            # needs_terms() — the per-order consent gate
    ├── faq/
    │   └── faq_service.py       # send_faq_card() — one card type for both FAQ stories
    ├── countdown/
    │   └── countdown_service.py # send_countdown_card() — no table, no inbound event
    ├── calculator/
    │   └── calculator_service.py # send_calculator_card() + the two gates
    │                             # (validate_fields / validate_formula). No table.
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
    │   ├── FAQ question match    → send_faq_card()  (JNO-55, deterministic)
    │   ├── "faq"/"help"          → send_faq_card(all)  (JNO-56)
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
    │                          →  terms card  [only if not already agreed to the
    │                          │   current TERMS_VERSION; parks at awaiting_terms]
    │                          →  final "Shall I proceed?" poll  [the true last step]
    │                          →  confirm_order.invoke() called directly
    │                          →  ORDER_SAVED → order_confirmation card (with fulfillment
    │                                            summary) — v3 while cash-only,
    │                                            v2/v1 when ONLINE_PAYMENTS_ENABLED
    │                                         → payment intent  [skipped while cash-only]
    │                                         → rating poll(s) — N events, one card
    │                                         → countdown card (15-min cancel window)
    │                                         → tip_request card [skipped unless TIPS_ENABLED]
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
                    │                               + create_payment_intent()  [no-op while
                    │                                 cash-only] + schedule_review()
                    ├── ORDER_HISTORY_TRIGGERED   → send_order_history_card()
                    ├── POLL_TRIGGERED|...        → send_single_choice_poll_to_room()
                    ├── MULTI_POLL_TRIGGERED|...  → send_flavor_preference_poll_to_room()
                    ├── RANKING_POLL_TRIGGERED|.. → send_ranking_poll_to_room()
                    ├── OPEN_POLL_TRIGGERED|...   → send_special_instructions_poll_to_room()
                    ├── RATING_POLL_TRIGGERED|... → send_rating_poll_to_room() (one event per
                    │                               item, one shared chain_id → ONE card)
                    ├── POLL_HISTORY_TRIGGERED    → send_poll_history_card()
                    ├── FAQ_TRIGGERED|name        → send_faq_card([one])
                    ├── FAQ_LIST_TRIGGERED        → send_faq_card(all)
                    └── CALC_TRIGGERED|{json}     → send_calculator_card()
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
| `menu_items` | **The** menu — authoritative. Was Supabase; moved local so browsing and pricing don't need the network. Seeded from `_SEED_MENU` in `db/menu.py` only when empty (same rule as `faqs`), which is the only git-tracked copy since `restaurant.db` is gitignored |
| `settings` | Key/value store (ordering_enabled flag) |
| `item_ordering` | Per-item ordering overrides |
| `customers` | Per-user saved name/phone (`get_customer`/`save_customer`) |
| `polls` | Native Matrix poll metadata (question, options, poll_type) |
| `poll_answers` | Every DSL/native poll answer, for poll history + results |
| `item_ratings` | Post-order star ratings per item |
| `auctions` | Auction metadata (title, starting_price, min_bid, ends_at, room_id, closed flag) |
| `auction_bids` | One row per (auction_id, user_id) — a rebid updates the row in place rather than inserting a new one |
| `conversation_history` | One row per user_id — their chat history as a JSON blob, so it survives a restart |
| `checkout_state` | One row per user_id — the whole in-flight checkout as a JSON blob (gap #5). Row exists only while a checkout is live; deleted once the order is placed or cancelled |
| `agreements` | Signed T&C records — `UNIQUE(user_id, terms_id, version)`, so a re-synced event can't duplicate one act of consent |
| `faqs` | Question/answer pairs (JNO-54). Seeded with drafts only when empty, so edits survive a restart. `ORDER BY id` is the accordion's order — no `sort_order`/`category` column |

**Supabase** — remote, async:
| Table | Purpose |
|---|---|
| `payment_intents` | Payment records for orders, tips, AND auction winners (created by Supabase Edge Function), keyed by `order_id` (`ORD-XXXXXX`, `TIP-XXXXXX`, or `AUC-XXXXXX`) |
| `reviews` | Customer reviews |
| `review_queue` | Scheduled review card sends |

Orders are saved to SQLite first, then a `stable_order_id` (e.g. `ORD-AB1C2D`) is generated and linked to the Supabase payment intent. Tips reuse the exact same `payment_intents` mechanism with a `TIP-XXXXXX` id instead — no separate tips table. Auction winners reuse it again with the auction's own `AUC-XXXXXX` id as the `order_id` — the auction_id IS the payment order_id, no separate ID generation needed.

---

## DSL Protocol

All rich UI cards are sent as Matrix `m.room.message` events with an `ai.jaeno.dsl` field. Payloads are validated against a JSON schema at `dsl-spec/schemas/v1/schema.json` (in-repo since JNO-schema-move; was `../dsl-spec/`, an unversioned Desktop folder) before sending — this only covers **outbound** sends (`safe_send_dsl()`); inbound DSL events from the client are not schema-validated.

| DSL type | Version | Description |
|---|---|---|
| `menu` | v2 | Full menu grouped by category |
| `menu_item` | v1 | Single item with image and price |
| `menu_category` | v1 | All items in one category |
| `order_confirmation` | v1 | Receipt card (before payment) — no fulfillment info |
| `order_confirmation` | v2 | Same receipt, plus an optional `fulfillment: {method, summary}` block. Additive-only; v1 stays registered/unmodified for in-flight messages that predate the fulfillment flow |
| `order_confirmation` | v3 | **The card actually sent today** (cash-only mode, JNO-240). Byte-identical payload to v2 — the client swaps the gateway button for a cash line and ignores `customer_name`/`user_id`/`raw_order_text`. One sender emits all three versions (`send_order_confirmation_card_v2(version=…)`); there is no separate v3 function or schema def |
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
| `terms` | v1 | Terms & conditions agreement card — text, plus a `signing_level` of `tap`/`typed`/`drawn` |
| `terms_response` | v1 | Inbound-only: `agreed` + the `method` the device actually delivered (+ signature) |
| `countdown` | v1 | Live countdown to a deadline (JNO-84/85). Outbound only — no inbound event, no table, no id. `ends_at` is ISO 8601 **with a UTC offset** (`datetime.now(timezone.utc).isoformat()`), same format `auction` already uses so the client parses both one way. `subtitle`/`expired_text` are omitted when empty so the client can use its own default |
| `calculator` | v1 | Agent-configured calculator (JNO-233/237). `title`/`fields`/`formula`/`result_label` required; `subtitle`/`result_unit` omitted when empty. `fields` is 1–8 entries of `{key, label, type: number\|choice, min?, max?, required?, options?}`; a `choice` option's `value` is the **number** the formula sees, its `label` is what the customer reads. **Python never evaluates the formula** — the client does, live |
| `calculator_result` | v1 | Inbound-only: the customer's filled-in estimate (JNO-236). Carries display **labels and values**, not field keys, because nothing server-side remembers the card that was sent |
| `faq` | v1 | Q&A card (JNO-54). **One type covers both stories** — the client renders a single `items` entry expanded (the direct answer, JNO-55) and several as a collapsed accordion (JNO-56), so Python never picks a layout. `question`/`answer` must be **strings** — the Dart reads them as `String?` off a plain map, so a non-string throws in the cast (same class as the menu `price` bug). Answers are plain text; the widget library renders no markdown |
| `terms_history` | v1 | Agreement history (JNO-98) — trigger card in the timeline, full list on tap. Declines included. `agreed` must be a real JSON bool, not SQLite's 0/1 |
| `order_status` | v1 | Staff-pushed order lifecycle card — `preparing`/`ready`/`on_the_way`/`delivered` get a dedicated icon+label client-side, any other string falls back to a generic icon + title-cased label |

Incoming DSL events from the client are routed by `dsl.type`:
- `review_submit` — saves customer review
- `order_summary` — pre-built order from the menu card UI, feeds into the deterministic order flow
- `tip_selected` — customer picked a preset/custom tip amount → creates a real payment intent + sends a `payment` card (no LLM)
- `tip_declined` — customer skipped the tip → plain text ack (no LLM)
- `bid_confirmation` — customer placed/updated a bid on a live auction → re-validated server-side against the current highest bid (the Flutter client's own min-bid check is cosmetic only) via `db.place_bid_if_higher()`, then upserted into `auction_bids` (no LLM)
- `fulfillment_selection` / `name_response` / `car_response` / `address_response` — the fulfillment flow's inbound half, see Order Fulfillment section below (no LLM)
- `terms_response` — customer agreed to / declined the order terms → records the agreement, then resumes or cancels the parked order (no LLM). See Terms & Conditions below
- `calculator_result` — customer sent their estimate into the chat (JNO-236) → summarises it, offers the order, and a "yes" runs the deterministic order flow (no LLM). See Calculator below
- `payment_success` — **dead code**, kept for reference only (see Payment section)

**Poll chaining (`chain_id`):** any `poll` card can carry an optional `data.chain_id`. Every poll sharing a value is drawn client-side as **one** card that advances question-to-question in place, instead of N stacked cards; the folded-in events are hidden as tiles. Omit it for a standalone question (a confirmation) so it keeps its own card. Used by:

| Sender | chain_id |
|---|---|
| size / instructions / name-confirm (`order_flow.py`) | the order's `order_id` |
| rating polls (`agent_dispatch.py`) | `rate_{order_id}` — **deliberately not the bare `order_id`** |
| `send_single_choice_poll` / `send_special_instructions_poll` (LLM path) | whatever the model passes; `agent/prompt.py` documents the rule |

**The client no longer requires the previous question to be answered before folding.** That condition was dropped app-side (`poll_chain.dart`) so the bot can fire a whole batch at once and forget it — which is what the rating polls do at `ORDER_SAVED`, rather than sequencing sends against inbound `poll_response` events (in-memory state that wouldn't survive a restart, gap #5). Consequence: **a chain_id collision now silently merges unrelated cards.** Ratings use the `rate_` prefix precisely so they don't land in the finished checkout chain. Any new chained sender needs its own namespace.

**Item descriptions:** `menu` v2 / `menu_category` nested items and the `menu_item` card all carry a `description` string, sourced from the SQLite `menu_items.description` column. The Flutter grid card and item-detail screen read `item['description']` straight off the map (no generated model for nested items), so the schema tells you nothing about what the client renders there — check the Dart. Known gap: `_HighlightedItemCard` (the `menu_item` v1 card) takes `description` as a constructor param and never renders it, so that one card still shows nothing.

**Gotcha already hit and fixed:** `menu`/`menu_category`'s nested item `price` field must be a **string** (`str(i["price"])`), matching `menu_item_card_data`'s convention and what the Dart-generated model (`MenuV2Item.price: String`) expects — `send_menu()`/`send_category_card()` were sending the raw SQLite/Supabase numeric value, which passed Python-side schema validation (the nested `menu_item` def had no type constraint on `price`) but threw on the client's stricter generated parser, silently falling back to an "Unsupported" card. `schema.json` now explicitly types it `string` too, so `safe_send_dsl()` catches this class of bug server-side going forward.

**Second instance of the same class — `poll_results` "unsupported" card:** the Python side validates against the *monolithic* `dsl-spec/schemas/v1/schema.json`, where `poll_results_data.required` is only `["question", "poll_type"]`. The client validates against the *per-type* `poll_results.v1.json` in the app's own dsl-spec checkout, which lists `results` as required — its generated model does `json["results"].map(...)` unguarded, so a missing key throws, `_tryParse` swallows it, and the card renders as "Unsupported". Rating polls have no per-option breakdown, so `send_item_rating_results_to_room()` now sends `"results": []`. **The two schema copies are not the same file and can disagree** — a payload passing `safe_send_dsl()` proves nothing about the client's stricter generated parser. Real fix is upstream: drop `results` from `required` in `poll_results.v1.json` and regenerate (the Dart handler already does `msg.get<List>('results') ?? []`).

**Adding any new DSL version — check the top-level `v` enum first:** `schema.json`'s root `v` property is an `enum`, not just `"type": "integer"`. It was `[1, 2]`, so the first `order_confirmation` v3 send was rejected by `safe_send_dsl()` before it left Python, with no per-type schema involved. Now `[1, 2, 3]`. A v4 of anything hits this again.

---

## Agent

### LLM
- **Provider:** OpenRouter, reached with `ChatOpenAI` (`langchain_openai`) pointed at `https://openrouter.ai/api/v1`. OpenRouter speaks the OpenAI wire format, so it's the same client class with a different `base_url` — no OpenAI account involved. The previous direct `ChatGoogleGenerativeAI` config is kept commented at the top of `agent/llm.py` rather than deleted.
- **Model:** `OPENROUTER_MODEL`, currently `minimax/minimax-m2.7:free` (the code default if unset is still `google/gemini-2.5-flash`). Swapping models is an `.env` edit plus a restart — but a model change is a behavior change, and this agent's tool-calling is what everything downstream depends on. Re-run the checks before trusting a swap.
- **Temperature:** 0.4
- **`max_tokens=2048`** (`OPENROUTER_MAX_TOKENS`) — load-bearing, not a guess. OpenRouter reserves credits against `max_tokens` up front; unset it defaults to the model's full 65535 window, and the reservation alone returns **402** on a low balance even when the actual reply is 200 tokens. Free (`:free`) models aren't billed against credits, so nothing is reserved and 2048 is safe; **drop it to ~1024 if `OPENROUTER_MODEL` is ever pointed back at a paid model on a near-empty balance.**

**The credit balance is a prompt-size ceiling, not just a spend limit (2026-08-25).** On a paid model OpenRouter derives a *max prompt tokens* cap from the remaining balance and returns `402 Prompt tokens limit exceeded: 10404 > 10058` with `limit_source: openrouter_credits`. This is not a `max_tokens` problem and **switching to a different paid model does not help** — the cap follows the account, not the model. The floor here is roughly fixed: ~6.7k tokens for `SYSTEM_PROMPT` (26,978 chars) plus ~1.5–2k of tool schemas for 17 tools, so ~8.5k is spent before the customer says anything, leaving almost no room for history. Trimming the injected history window bought only ~200 tokens (10404 → 10199) and did **not** clear the cap; only moving to a `:free` model did.

Three levers, in the order they're actually worth pulling: **top up credits** (the real fix, reverts everything else), **use a `:free` model** (today's answer), **shrink the prompt** (a rewrite of a 27KB prompt with real behavior risk — not attempted).

**Picking a free model is an empirical question, not a spec question.** Of OpenRouter's ~16 free tool-calling models, all are reasoning models, and availability churns. `z-ai/glm-5.2:free` was the best on paper and is persistently 429'd upstream ("temporarily rate-limited"), so it was ruled out by testing rather than reading. `minimax/minimax-m2.7:free` was verified end-to-end through `_invoke_agent_with_retry`: correct tool calls on menu / item / order-start, a 6,667-token prompt accepted, 3.5–8.5s per turn. `nvidia/nemotron-3.5-lightning:free` also passed and is faster (3.8s) if latency matters more than instruction-following. Note MiniMax **rejects** `reasoning: {enabled: false}` outright (`400 Reasoning is mandatory for this endpoint`) — reasoning stayed on, and completion stayed at ~34 tokens anyway, so it never threatened the `max_tokens` budget.

**Free tier is a stopgap, not a destination** — free models carry daily request caps and upstream rate limits that a customer-facing bot will hit. Topping up and reverting `.env` is the exit.
- **Rate limit:** 2.0 req/s, max bucket 10 (`agent/llm.py`). The limiter counts requests *it* issues, not the client's internal retries, and one customer message costs 2–3 requests (model → tool → model) — so this is ~40–50 messages/min, not 120. Under OpenRouter the real ceiling is your credit balance and the upstream provider's limits, not a per-project RPM quota.
- **Reasoning:** the old `thinking_budget=0` was a `ChatGoogleGenerativeAI` argument and does **not** exist on `ChatOpenAI` — it's gone, not ported. If the routed model reasons by default, that's now unbounded; OpenRouter's `reasoning` field is the knob, passed via `extra_body`.

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
| `show_faq(question)` | A general question the fast path couldn't match — the LLM rephrases it toward a stored question | `"FAQ_TRIGGERED\|{matched question}"` or `"FAQ_NO_MATCH"` |
| `show_faqs()` | Customer wants to browse every FAQ | `"FAQ_LIST_TRIGGERED"` |
| `send_calculator(title, quantity_label, categories, ...)` | An estimate depending on a quantity only the customer has (catering/bulk). **Builds the fields, options, prices and formula itself** from the live menu — the model only names categories | `"CALC_TRIGGERED\|{json}"`, or `CALC_NO_CATEGORY\|{valid names}` / `CALC_MENU_UNAVAILABLE` |
| `get_menu_prices()` | Before `send_calculator` — this is where the exact category spellings come from | `"Hot Classics: Espresso 10, Latte 30\|nCold Drinks: ..."` or `"MENU_PRICES_UNAVAILABLE"` |

### Middleware Stack (in order)
1. **`RestaurantGuardrail`** — blocks banned keywords (prompt-injection phrases) before the LLM; checks the customer's **latest** human message
2. **`SummarizationMiddleware`** — summarizes conversation when it exceeds ~8000 tokens, keeps last 6 (rarely triggers in practice since `message_handler.py` already trims to the last 20 messages before invoking)
3. **`ToolRetryMiddleware`** — retries `confirm_order` / `confirm_reservation` up to 3 times
4. **`PIIMiddleware`** — phone-number detector configured but currently a no-op (`strategy="none"`, `apply_to_input=False`, `apply_to_output=False`)
5. **`ModelCallLimitMiddleware`** — caps a single `agent.ainvoke()` run at 10 model calls (`exit_behavior="end"`)
6. **`ToolCallLimitMiddleware`** — caps a single run at 10 tool calls (`exit_behavior="continue"`)

There is intentionally **no separate model-retry middleware** — the client's own `max_retries=6` (in `agent/llm.py`) already covers 429/5xx/network errors with its own backoff; a `ModelRetryMiddleware` layer was removed because its up-to-120s backoff schedule was getting killed mid-retry by the outer timeout in `_invoke_agent_with_retry` (`bot/router/agent_invoke.py`), causing the whole call to restart from scratch instead of completing one clean retry. That timeout is **50s** (was 30s) — OpenRouter adds a routing hop in front of the upstream provider, so the old budget was cutting off calls that would have completed.

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

> **⚠️ CURRENTLY DISABLED — the bot is cash-only (JNO-239).** Everything in this
> section describes the flow as it works when `ONLINE_PAYMENTS_ENABLED=true`.
> With the flag off (today's default), `create_payment_intent()` returns
> `(None, None)` before any Supabase or Swich call, so **no `payment_intents`
> row is ever created** — for orders, tips, *or* auction winners. See
> Cash-Only Mode below.

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

Cancellation: a message matching `\bcancel\b` cancels the pending payment intent (Python-side only, doesn't touch the edge functions). While cash-only there is no payment intent, so it cancels the SQLite order instead — see Cash-Only Cancellation below.

### Cash-Only Mode (JNO-238/239/240/241)

Two flags in `config.py`, both read from `.env` **at import time — a change needs a bot restart**, and both defaulting to `false` so an unset env var can never silently re-enable real money movement:

| Flag | Effect when `false` (today) |
|---|---|
| `ONLINE_PAYMENTS_ENABLED` | `create_payment_intent()` short-circuits to `(None, None)`; `agent_dispatch.py` sends `order_confirmation` **v3** (cash receipt, no gateway button); `order_history_service.py` reports cash orders as `status: "cash"` |
| `TIPS_ENABLED` | `order_flow.py` skips the `tip_request` card entirely |

**Why the guard lives inside `create_payment_intent()` and not at the call sites:** all three *creating* paths — orders (`agent_dispatch.py`), tips (`send_payment_card()`), and auction winner payouts (`auction_scheduler.py`) — funnel through that one function, and every caller already handled its `(None, None)` return.

**There are TWO chokepoints, not one** — `send_existing_payment_card()` *reads* an existing row instead of creating one, so the `create_payment_intent()` guard does not cover it. This was missed on the first pass and shipped a real leak: `payment_intents` rows created **before** the flag flip are still `status='pending'`, so the "pay" keyword fast-path (`message_handler.py`) happily resent a live Swich card for a pre-cash-only order. Both `send_existing_payment_card()` and that fast path are now guarded — the fast path answers with the cash message instead of querying Supabase at all. **Any new code path that surfaces a payment must be checked against both.** The stale `pending` rows themselves are left alone; they're genuine historical online orders.

The tip card is gated separately even though the guard already blocks tip payments: without it the customer taps a tip button that silently does nothing (`send_payment_card` returns early on the `None` transaction id).

**Cash orders stay `'pending'` in order history until staff marks them paid** — `python update_order_status.py ORD-XXXXXX paid`. A bespoke `"cash"` status was tried first and reverted: the Flutter history page filters strictly on `paid`/`completed`/`delivered` → `pending` → `cancelled`, so any other string falls through **every** tab and the order disappears from the UI entirely (the page opens on "Completed", so the customer just sees "No orders found"). `'pending'` is also simply true — the customer hasn't handed over money yet.

**Cash-Only Cancellation** — with no `payment_intents` row to cancel, "cancel" falls through to `db.cancel_last_pending_order()`, which flips the newest *recent* pending SQLite order to `'cancelled'`. Two bounds, both load-bearing, and **currently uncovered** — `test_cash_cancel.py` is referenced here but does not exist in `tests/`; it was either never written or lost. Both bounds below are the kind that fail silently and void a real order, so this is the most worthwhile test to add:

- **`\bcancel\b`, not `'cancel' in message`** — this branch writes to the `orders` table now, not just a Supabase payment row, so the old substring test would have voided a real order on "what's your cancellation policy?" or "my last order was cancelled".
- **`CANCEL_WINDOW_MINUTES = 15`** (`db/orders.py`) — nothing moves an order off `'pending'` except staff manually running `update_order_status.py ... paid`, so a delivered order whose cash was never marked collected is **indistinguishable in SQL** from one placed a minute ago. Unbounded, "cancel" days later would void a completed, paid-for sale. The window is the cheap stand-in for the lifecycle column that doesn't exist; if `preparing`/`delivered` ever persist durably, check those instead and drop the constant.

Outside the window (or with nothing pending) the customer gets one "give us a call" message rather than a second query to tell the two cases apart — the answer is the same either way.

`paid` is the **only** status persisted to `orders.status`; the lifecycle statuses (`preparing`/`ready`/`delivered`) remain fire-and-forget cards. `orders.status` is a single column holding the *payment* axis, so persisting lifecycle values there would let `delivered` overwrite `paid` and break the history filter. If both axes ever need persisting, that's a separate column, not a second string in this one.

**The system prompt is flag-driven too** — `agent/prompt.py` picks its TASK 6 (PAYMENT QUESTIONS) text off `ONLINE_PAYMENTS_ENABLED`, so "how do I pay?" answers "cash on receipt" instead of describing a "Pay Now" button that isn't on the receipt any more. Hardcoding cash there would have made the flag a half-truth.

**To restore online payments:** set both flags `true` in `.env` and restart. No code changes — v1/v2 senders, the tip handlers, and the Swich integration were all left intact, never removed.

### Tips

**(Disabled while cash-only — see above.)** After a successful order, `bot/payment/tip_service.py` automatically sends a `tip_request` card (presets `[50, 100, 200]` PKR + custom amount + decline option) — no LLM tool call, fired directly from `_place_deterministic_order` on `ORDER_SAVED`. When the customer responds:
- **Preset/custom amount** (`tip_selected` DSL event) → `bot/router/dsl_text_events.py` looks up the customer's saved name/phone (`db.get_customer`), generates a `TIP-XXXXXX` id, and calls `payment_service.send_payment_card()` — the exact same function used for order payments — so the tip gets a real Swich checkout card. Rejects amounts under 10 PKR (Swich's minimum).
- **Decline** (`tip_declined`) → plain text acknowledgment, nothing else happens.

No dedicated `tips` table exists yet — tip payments are just `payment_intents` rows distinguished by the `TIP-` prefix on `order_id`. If tip-specific reporting/analytics is ever needed, that's the natural next addition.

---

## Order Fulfillment

After the customer confirms their name/phone, the deterministic order flow asks how they want to receive the order — **before** the final "Shall I proceed?" confirmation, not after, so the receipt shows the whole picture in one go and "Yes" is the true last step:

1. `bot/router/order_flow.py::_start_fulfillment_stage()` sends a `fulfillment_method` card carrying the order's stable `order_id`. That id is minted **when the flow starts**, not later in `agent_dispatch.py` as the plain LLM-fallback path does — the size/instructions polls already need it as their `chain_id`. `_start_fulfillment_stage()` keeps a defensive backfill for a flow that somehow arrives without one
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

## Terms & Conditions (JNO-90)

Gated on **every order**, but a customer only ever signs **once per terms version**:

1. Fulfillment details settled → `_send_final_confirm_poll()` (`bot/router/order_flow.py`) checks `needs_terms(sender)` **before** sending the poll
2. Already agreed to the current `TERMS_VERSION` → the confirm poll goes out exactly as before, no card
3. Not agreed → `terms` card goes out *instead*, the flow parks at stage `awaiting_terms` (state stays alive in `order_flows`)
4. Client replies `terms_response` → `bot/router/dsl_text_events.py` records it via `db.save_agreement()`, sets stage back to `final_confirm` and calls `_send_final_confirm_poll()` again — which now falls through the gate — or clears the flow on a decline
5. "Shall I proceed?" → **Yes** → order places, unchanged

**Terms come before the final confirm, not after** — same reasoning as fulfillment (see Key Design Decisions): "Yes" has to be the true last step, so the customer isn't confirming an order and only then being asked to sign for it, and a decline cancels nothing they'd already agreed to.

The gate sits **inside** `_send_final_confirm_poll()` rather than at its callers because all three fulfillment responses (`name_response`/`car_response`/`address_response`) route through that one function — one guard covers them, and a fourth fulfillment method would inherit it for free. The re-entry after consent is deliberate and can't loop: `needs_terms()` is False the second time because the agreement was written first.

**`method` is stored, never `signing_level`.** The card *requests* a level; the client's fallback chain (drawn → typed → tap, on device capability) decides what it can actually deliver and reports it back as `method`. Storing the request would claim a drawn signature that was never produced.

**The terms text is a constant in `bot/terms/terms_service.py`, not a table.** Nothing in JNO-90 authors a terms version — there's no admin surface, so a table would be hand-seeded anyway, and git already gives immutable version history. Ordering is also deliberately SQLite-backed so checkout doesn't depend on the network; reading the agreement text remotely would undo that. **Bump `TERMS_VERSION` whenever `TERMS_BODY` changes** — agreements are keyed `(terms_id, version)`, so a bump re-prompts everyone automatically, and leaving it stale silently records new consent against old text.

Inbound `terms_response` is a trust boundary (inbound DSL is never schema-validated), so the handler drops anything whose `terms_id`/`version` doesn't match what was published, or whose `method` is outside the enum — otherwise anyone could fabricate a signature on a document that never existed. `db/terms.py` caps signature size again server-side; the Flutter card's own 48KB cap binds only clients that choose to honour it.

A decline is recorded but does **not** satisfy the gate (`agreed = 1` only), and a later agreement upgrades that same row rather than leaving a stale `declined` record shadowing real consent.

**Agreement history (JNO-98)** — `send_terms_history_card(room_id, user_id)` builds the list from `db.get_agreements()`. Triggered by `_TERMS_HISTORY_PATTERN` in `message_handler.py` ("my agreements", "agreement history", "what did i agree to"), no LLM, same shape as the order-history fast path. Singular forms are matched too (`terms?`, `agreements?`): "term history" missed it, reached the agent, and was answered *"I can't show you a term history"* — a false capability claim that then persisted through the summary (see FAQ → card-only turns). The pattern sits at **module scope**, unlike the other fast paths, so `tests/test_terms.py` asserts against the shipped regex instead of a copy that could drift from it. Declines are included on purpose. Signature blobs are **not** sent — `get_agreements()` omits them, since a base64 PNG per row would dwarf the rest of the payload. Capped at 20 rows: every `body` rides along in one Matrix event, which dies past ~64KB.

**The `body` snapshot (JNO-97)** — `agreements.body` holds the exact text the customer was shown, written at sign time and never joined from a versions table. Duplicating it per agreement is the point: the row is then immune to any later edit of `TERMS_BODY`, which is what makes it a consent record rather than a timestamp.

The one rule: **serve `body` from the row, never from the `TERMS_BODY` constant.** Rendering today's wording under an old version number looks authoritative and is wrong. Snapshotting the constant is safe in exactly one place — `dsl_text_events.py` at write time — because the version guard immediately above it has already rejected anything that isn't the currently published version.

`body` is **optional** in the `terms_history` contract. Agreements written before the column existed have none, and the client drops its "View copy" affordance rather than opening a blank page, so old rows keep rendering. `send_terms_history_card()` omits the key entirely rather than sending null.

Checks: `python -m tests.test_terms` — 9 checks covering both schemas, the storage round-trip (decline / upgrade / redelivery / version bump / size cap), the four inbound-handler branches, and JNO-97's "agreement recorded" reply.

---

## Countdown (JNO-84/85)

The leanest card in the repo: outbound only, no inbound event, **no table**. The deadline is just a timestamp the caller passes, so there's nothing to persist and nothing to clean up when it lapses — the client greys the card out. A bot that *acted* on expiry would be a scheduler, which the epic doesn't ask for.

**Deliberately not an LLM tool.** A countdown makes a promise about time, and every LLM-triggered card here has misfired at least once (see the FAQ section). Triggered deterministically, like auction creation and order status.

**The one real deadline it points at is the 15-minute cancel window.** None of JNO-85's own examples exist at Dot Cafe — no time-gated menu (open 12–12 straight), no flash sales, no held slots, no ETA data. But `CANCEL_WINDOW_MINUTES` is genuinely enforced in SQL and the customer previously had no way to see the clock; it was only in FAQ #4 and clause 3 of the terms.

`_send_cancel_countdown()` (`bot/router/agent_dispatch.py`) fires from the `triggered_payment` branch, which is where the deterministic order flow **and** the LLM fallback both converge — one hook covers both paths.

**The deadline is anchored to the order row's `created_at`, not to "now".** That's the same column `cancel_last_pending_order()` filters on, so the timer expires at the exact instant cancelling stops working. Deriving it from "now" would land a second or two late and leave the countdown showing time remaining after the SQL cutoff had passed. SQLite writes `CURRENT_TIMESTAMP` as naive UTC `'YYYY-MM-DD HH:MM:SS'`, so `timezone.utc` is attached before `.isoformat()` — a naive string would be read as local and land 5 hours out in Karachi.

Best-effort by design: any failure is logged and swallowed. The receipt has already gone out and nothing about the order depends on the countdown.

Checks: `python -m tests.test_countdown` — 3 checks covering the schema, empty-optional omission, and that the deadline matches the SQL rule exactly.

---

## Calculator (JNO-233)

A card the customer fills in themselves; the estimate recomputes live client-side. Four stories, one card: JNO-234 (render fields) and JNO-235 (live result) are **pure Flutter**, JNO-236 is the inbound half, JNO-237 is everything in this repo.

**Python never evaluates the formula.** The client recomputes it on every keystroke, so `calculator_service.py` only decides whether a formula is safe enough to ship. That check is an `ast.parse` + node allowlist, never a regex: a charset regex passes `__import__("os").system(...)`, which is nothing but letters, underscores, quotes and parens. `ast.Pow` and `ast.Mod` are deliberately outside the allowlist — `9**9**9` is a one-token DoS on whichever runtime evaluates it, and the epic's grammar is `+ - * /` only.

**Two independent gates, and Python is not the boundary.** DSL also reaches the room straight from Supabase Edge Functions, which never pass through `safe_send_dsl()` (gap #4) — on that path the Dart parser is the *only* thing between a payload and the evaluator. The client re-implements the identical grammar; neither side may assume the other ran.

**The field cap is enforced three times and only two of them count.** `MAX_FIELDS = 8` in Python, `maxItems: 8` in `schema.json`, and again client-side inside `render()`. The schema copy is documentation only — quicktype drops `maxItems` from the generated model, so it enforces nothing on the client. Same for the formula: an unparseable one is a perfectly valid JSON string, so `_tryParse` sees nothing wrong. The client's working hook is throwing inside `render()`, which `dsl_event_extension.dart` catches into the standard failed card.

**The model does not write the card — it names categories, the tool builds it.** `send_calculator` takes `quantity_label` plus `categories=["Hot Classics", ...]`, then reads the live `menu_items` rows and constructs the fields, the option labels, the prices and the formula itself. The model never emits a price, an item list or an expression. **Categories are the only selector** — there is no single-item mode, see below.

This replaced a version where the model passed `fields` and `formula` directly, after three rounds of prompt tuning failed in a different way each time:

1. it made the *price* a customer input (`guests * item_price`, "Price per item"), so 20 coffees quoted as 40 PKR — the customer quoting themselves, rendered as if the cafe had computed it;
2. told to use real prices, it offered 3 of 13 coffees, stranding anyone wanting a cappuccino;
3. told to list every matching item, it listed all 17 including Mango Smoothie and Berry Mojito in a *coffee* estimate;
4. told explicitly "every one they asked about and nothing else", it did (3) again.

A prompt is not a constraint (gap #25). Category names resolve against the real `category` column, so an un-asked-for item can no longer appear and a wrong price cannot be expressed. An unknown category returns `CALC_NO_CATEGORY|` **with the valid names**, so the model self-corrects instead of guessing.

**`item_name` was the hole in that, and it produced failure (1) again in production — so it was removed (2026-08-13).** Asked to "calculate 20 coffee" the model called `send_calculator(item_name="Latte")` — a general request narrowed to one drink. That branch answered with a bare quantity field and `formula = "quantity * 30"`, so a card titled *Coffee Estimate* priced every coffee at Latte's 30 (espresso is 10, Spanish Latte Premium 1999) **without naming the item anywhere on the card**. The customer cannot see that as wrong. Worse, `inputs` then echoed back no item, `_derive_order_from_inputs` returned `("", 0)`, no order was offered, and the "yes" fell through to the LLM — which answered it with a *second calculator card*, the exact loop this section says was designed out.

Both symptoms were one cause: `item_name` was the only card the bot builds whose result carries no item. Every card is now a category card — quantity plus a real picker, `formula` always `"quantity * choice"`, no branch folding a price into an expression. That makes every calculator orderable **by construction** rather than by convention, since the option labels are what come back in `inputs`.

Expanding `item_name` to its category siblings was tried first and worked, but then did nothing `categories` doesn't — except pick the category *indirectly*, via whichever item the model happened to name, which is the same judgement call with less information. A single-item request is a category request: "25 lattes" → `categories=["Hot Classics"]`, and Latte is in the picker with its neighbours. `tests/test_calculator.py` asserts `item_name` is not in `send_calculator.args`, so re-adding it fails the suite.

The `_RATE_WORDS` guard in `validate_fields` (rejecting a `number` field labelled price/cost/rate/charge/fee) stays as a backstop for any other caller of `send_calculator_card()` — the service is still generic per JNO-237, and only the *tool* is menu-bound.

**`calculator_result` never reaches the LLM.** It summarises the result, offers the order, and a plain "yes" runs `_start_order_flow()` — the same path every other order takes. The item and quantity come out of `inputs` by matching each value against `MENU_PRICES` (the integer is the quantity, the value naming a real menu row is the item), so a non-menu calculator yields nothing and no order is invented.

This replaced a fall-through-to-the-agent design copied from `order_summary`. Two things went wrong with it, both fixed by not involving the LLM at all: the agent answered a calculator result with *another calculator card* (twice running — a result is a calculator question, so it reached for a calculator), and a later "yes" had to rebuild the order through tool calls, which is exactly what the deterministic order flow exists to avoid. A `suppress_calculator` flag was threaded through `handle_message` → `_dispatch_agent_result` to stop the loop; handling the event deterministically removed the loop **and** the flag.

`order_summary` still falls through, correctly — it carries no result and the customer hasn't agreed to anything yet.

**A literal `/ 0` is rejected at send time**; every other division-by-zero depends on runtime input and is the client's "can't calculate yet" state. That state is gated on `result.isFinite`, not on a zero-divisor check — a 1e-9 divisor overflows the same way and a zero check misses it.

**`calculator_result` falls through to the agent** (`return False, message`), exactly like `order_summary` — "what do I do with this number" is judgement, not a state machine. Nothing server-side records the card that was sent, which is *why* the client echoes display labels rather than field keys: the inbound payload is the only source of them. The handler clips every field before building the text — it lands in `conversation_history`, which is persisted and re-injected into every later agent call, so an unbounded blob would eat the model's context on every subsequent turn.

**…except while a checkout is open, where it is handled instead of falling through.** The fall-through re-enters `handle_message` *above* its `order_flows` check, so a customer sitting at `await_name`/`await_phone` who taps an old calculator card in scrollback would have the estimate text stored as their name or phone by `save_customer()` — onto the order and onto the receipt. The guard acknowledges the estimate, re-asks whatever the flow was waiting on, and leaves the flow untouched. This is the same stage-check every other inbound handler does; `order_summary`, which this branch was modelled on, is the exception rather than the rule, because starting a fresh order mid-flow is a legitimate thing to do.

**No table, no state, no cleanup** — same shape as `countdown`. Dismissing the card sends nothing at all; a `calculator_declined` nobody consumes would be a type, a schema, an example, a generated model and a handler registration for zero behaviour.

Checks: `python -m tests.test_calculator` — 10 checks: the formula gate (13 unsafe/invalid rejected), the field gate, the schema, the sender, the inbound fall-through incl. clipping, the mid-checkout guard, the history-sentinel rule, server-built options, category grouping, and the no-loop guard.

---

## FAQ (JNO-54)

Two stories, **one** DSL type: JNO-55 (one relevant answer) and JNO-56 (browse them all) differ only in how many `items` the card carries, and the client picks the layout. The agent sends what it selected.

**Matching is deterministic, in `message_handler.py`, not an LLM tool call.** This was learned the hard way — the LLM-driven version failed three separate ways in testing, all the same root cause (see below). `db.get_faq()` runs exact → substring either direction → stop. A miss is safe: nothing is sent and the message falls through to the agent.

**There is deliberately no fuzzy tier.** `fuzzy_match_key()` was tried first, copying `show_item`'s ladder. On real questions difflib only fires where substring already failed, and every such case measured wrong: `"do you have parking for a minibus"` scores **0.60** against *"Do you cater for allergies?"*, while a question that genuinely should match (`"what payment methods do you take"` → *"How can I pay?"*) reaches only **0.35**. No cutoff separates those. Typo tolerance isn't needed either — the LLM writes that string, not the customer. Embeddings are the upgrade if logs show real misses.

`show_faq`/`show_faqs` stay registered as the **semantic layer above the mechanical one**: a wording the substring match can't reach still gets to the agent, which can rephrase it toward a stored question. Confirmed working in testing — a message mangled by a copy-paste artefact missed the fast path and the LLM path still sent the right card.

**No seeded question may contain a bare "cancel".** `\bcancel\b` (`message_handler.py`) intercepts before the agent, so such an FAQ is unreachable — and mid-checkout it drops the customer's in-flight order instead of answering. The seed says *"What is your cancellation policy?"* (`\b` doesn't match inside "cancellation"); `tests/test_faq.py` fails if any seeded question breaks this.

**The seeded answers are drafts** and restate what the code does (cash-only, the 15-minute window, DHA Phase 4 delivery) — so they drift exactly like `TERMS_BODY`. Edit the rows, not the constant, once live.

Checks: `python -m tests.test_faq` — 4 checks covering the schema (incl. non-string `answer`), the placeholder guard, seed/matching/cancel-shadowing, and the real emitted payload.

### Card-only turns must never write a placeholder into history

The bug that cost three debugging rounds, and the reason `clean_reply` matters more than it looks. `message_handler.py` writes `clean_reply` into conversation history, which is re-injected into the next agent call — **so the model copies it back out as its own answer instead of calling the tool.** Observed in production, in order:

1. `clean_reply = "[FAQ card sent]"` → the customer was shown that literal string, three turns running
2. Changed to `""` → the `or "Got it!"` fallback kicked in → the model answered `"Got it!"` instead
3. Then it stopped calling the tool at all

Fixed at the source: **no dispatch branch returns a bracketed pseudo-reply any more** — `[Banner sent: …]`, `[Poll history card sent]` and `[Rating poll(s) sent for: …]` all became `""`. The FAQ branch stores the **real answer text**, so if it does get imitated the customer still gets a correct reply, just as text. `_is_history_placeholder()` remains as a backstop for rows written before the fix.

Any new card-only branch must follow the same rule: whatever goes in `clean_reply` should be a sentence that is **harmless if the model repeats it verbatim**.

**Fourth and fifth instances — poll history and banner, caught in production 2026-08-13.** Both still assigned `clean_reply = ""`, which is failure mode (2), not a fix for it. Live, `"Send poll history"` was answered `"Got it!"`; the summariser then compressed that turn into *"'poll history' and 'term history' … are not supported functionalities"* — and since summaries are re-injected on every later turn, the bot went on asserting a real, registered tool didn't exist. That is gap #27 with a false **capability** claim rather than a false fact, and it outlives the turn that caused it. Both branches now keep the model's own sentence with a plain fallback (`"Here's your poll history."`, the banner's own `message`). `tests/test_calculator.py` now asserts at **source level** that no branch in `agent_dispatch.py` assigns an empty `clean_reply`, which covers branches no test drives — the two above were exactly that.

**Third instance — the calculator, JNO-237.** The branch stored the card *title* (`clean_reply = "Catering Estimate"`), copying the poll precedent. It doesn't transfer: a poll stores its **question**, which is a sentence; a title is a label. Within three turns the model was answering catering questions with the literal words "Catering Estimate" and had stopped calling `send_calculator` — failure mode (1) and (3) at once. The branch now keeps the model's own lead-in sentence, falling back to a fixed sentence when it wrote none. **`""` is not an escape hatch**: `message_handler.py` substitutes `"Got it!"` for an empty `clean_reply`, which is failure mode (2). `tests/test_calculator.py` asserts the value is non-empty, isn't the title, and contains a space.

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
| `OPENROUTER_API_KEY` | OpenRouter key — **the live LLM credential** |
| `OPENROUTER_MODEL` | Model slug — currently `minimax/minimax-m2.7:free`. Falls back to `google/gemini-2.5-flash` if unset |
| `OPENROUTER_MAX_TOKENS` | Reply cap, default `2048`. Lower it on a paid model with a low balance — the reservation alone can 402. See Agent → LLM |
| `AGENT_HISTORY_WINDOW` | How many past messages get injected per agent call, default `20` (= `HISTORY_CAP`). Lower it to shrink the prompt on a paid model with a low balance |
| `GOOGLE_API_KEY` | **Unused.** Only the commented-out direct-Gemini block in `agent/llm.py` reads it. Kept so switching back is an uncomment, not a re-key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service key |
| `SUPABASE_ANON_KEY` | Supabase anon key (for REST calls) |
| `SWICH_CLIENT_ID` | Swich payment gateway client ID |
| `SWICH_SECRET_KEY` | Swich HMAC signing secret |
| `BUSINESS_NAME` | Displayed on payment cards — ⚠️ there's a SEPARATE `BUSINESS_NAME` env var on the `swich-callback` Edge Function (different repo/secrets store). Not shared; can silently drift out of sync. |
| `REVIEW_CARD_ENABLED` | `"true"` / `"false"` (default `"true"`) |
| `ONLINE_PAYMENTS_ENABLED` | Master Swich kill switch — default `"false"`. See Cash-Only Mode |
| `TIPS_ENABLED` | Post-order tip card on/off — default `"false"`. See Cash-Only Mode |
| `LANGSMITH_API_KEY` | LangSmith tracing key. (`LANGCHAIN_API_KEY` is the legacy alias and still works — `.env` uses the `LANGSMITH_*` names) |
| `LANGSMITH_TRACING` | `"true"` / `"false"`. ⚠️ Currently **ignored**: `message_handler.py` passes `enabled=True` to `ls.tracing_context()` explicitly, so setting this `false` does not turn tracing off. Pass `enabled=None` to make the env var the switch |
| `LANGSMITH_PROJECT` | Also **ignored** — the same call hardcodes `project_name="dot-cafe-bot"` |

---

## Running

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt   # (if requirements.txt exists)
python main.py
```

`python-olm` must be installed for encrypted rooms (see Matrix Client below). Without it the bot still runs, logs a warning, and cannot read or send in any encrypted room.

Startup sequence:
1. `init_db()` — creates SQLite tables if missing
2. `login()` — restores the saved session from `store/credentials.json` if present, otherwise a fresh password login that writes it; then `load_store()` for the olm keys
3. Full sync
4. Event callbacks registered — `auto_join` (invites), `handle_message` (text), `handle_custom_event` (unknown/DSL)
5. Review scheduler started as background task
6. Auction scheduler started as background task
7. `sync_forever()` — main event loop

---

## Matrix Client, Encryption & Concurrency

**E2EE (`bot/matrix_client.py`)** — nio enables encryption on its own whenever `python-olm` is importable; the client is configured with a `store_path` of `store/` so keys survive a restart. Two Windows-specific choices that are load-bearing:

- **`store=SqliteStore`, not the default `DefaultStore`** — `DefaultStore` writes device-trust state to files named `<mxid>_<device>.blacklisted_devices`, and the `:` in a Matrix ID is an illegal Windows filename character → `WinError 123` on every encrypted send.
- **`store_name="nio.db"`** — the default is `<mxid>_<device>.db`; on NTFS that same colon silently redirects the whole crypto DB into an **alternate data stream**, which vanishes the moment the folder is zipped or copied to Linux.

**Session reuse (`main.py::login()`)** — the `user_id`/`device_id`/`access_token` are persisted to `store/credentials.json` and restored via `restore_login()`. A fresh `login()` each start would mint a new device with new olm keys, forcing every customer's client to re-share room keys with it.

**Blanket device trust** — `matrix_client.room_send` is wrapped to default `ignore_unverified_devices=True`. Customers never verify a shop bot, and without it every send into an encrypted room raises `OlmUnverifiedDeviceError`. Patched at the client rather than per-sender because every DSL sender in the repo routes through `room_send`. Marked `ponytail:` in the source; the upgrade path is a real verification flow if anyone ever needs it.

**Auto-join (`main.py::auto_join`)** — accepts DM invites from the app's Discovery → "Start chat" flow (3 retries), then greets. The greeting is deferred to `_greet_when_room_ready()`: the encrypted send path looks the room up in `client.rooms`, but a freshly joined room only appears after the *next* sync — and the callback fires mid-sync — so it waits on `client.synced` rather than sending immediately. Invite events repeat every sync until the join lands, hence the `_joining` set.

**Per-sender concurrency (`bot/message_handler.py`)** — nio awaits event callbacks inline (`async_client.py::_on_event`), so handling work directly in the callback serializes *every* room behind the slowest turn: one 30s agent call stalls all other customers. `handle_message`/`handle_custom_event` are now thin wrappers that `_spawn()` a task per event:

- **Serialized per sender** via `_sender_locks` — `order_flows` is a per-user state machine, so two events from the same user must not interleave. Text and custom (poll) events share one lock: a poll answer and a typed message drive the same flow.
- **Exceptions are swallowed and logged** inside `_run_serialized` — previously an exception propagated out of the nio callback and killed `sync_forever` (the whole bot) for every user.
- **Tasks are held in `_running_tasks`** — asyncio only keeps weak references, so an unheld task can be garbage-collected mid-flight.
- **No automated check** — `test_concurrency.py` (asserted same-sender events don't interleave and different-sender ones do) was written and then deleted; nothing guards the serialization now.

**SQLite under concurrency (`db/connection.py`)** — `timeout=30` (wait for the writer lock instead of raising "database is locked" the instant another handler is mid-write) and `PRAGMA journal_mode=WAL` (readers don't block on the writer). WAL leaves `restaurant.db-shm`/`-wal` sidecar files, both gitignored.

---

## Conversation History

Per-user conversation history lives in the `conversation_histories` dict, write-through cached to the SQLite `conversation_history` table (`db/conversation_history.py`):
- Last `HISTORY_WINDOW` messages injected into each agent call — 20 by default, overridable with `AGENT_HISTORY_WINDOW`. Separate constant from `HISTORY_CAP` (what's *stored*) purely so the injected prompt can be shrunk without throwing history away: on a paid model OpenRouter caps prompt size by credit balance (see Agent → LLM). The three injection sites (`message_handler.py`, `custom_events.py` ×2) all read it — a fourth that hardcodes a slice would silently escape the lever
- Capped at 20 messages per user — the cap lives in `persist_history()` (`bot/router/state.py`), **not** at each call site: the poll paths in `custom_events.py`/`order_flow.py` append without capping, and persistence would turn that into an unbounded row on disk
- **Survives a bot restart.** `ensure_history_loaded(sender)` reloads from SQLite on first touch per sender; `persist_history(sender)` must be called after every mutation
- Writes are synchronous SQLite on the asyncio loop — one small blob per message, fine at this scale, revisit if it ever shows up in latency

The deterministic order-flow state (`order_flows`, `pending_orders`, `last_orders`, `last_order_line`/`last_order_state`, `awaiting_reorder_confirmation`, `awaiting_calculator_order`) lives alongside the history dict in `bot/router/state.py` and is **also write-through cached now** (gap #5, fixed) — one `checkout_state` row per user holding all of it as one JSON blob, via `ensure_checkout_loaded`/`persist_checkout`, built on the `ensure_history_loaded`/`persist_history` template.

**The hook is at `_run_serialized` (`bot/message_handler.py`), not at the mutation sites.** These dicts are written from ~20 places across `order_flow.py`, `dsl_text_events.py` and `message_handler.py`, and one missed call site is an order that silently doesn't survive a restart — so instead the whole per-user checkout is loaded once on the way into a turn and snapshotted once on the way out, capturing whatever the turn did by whichever path, including paths added later. The per-sender lock is what makes that safe: nothing else can be mid-mutation, so the snapshot is never torn. The save is in a `finally`, so a handler that raises mid-checkout still persists the stage it reached.

**`CHECKOUT_TTL_MINUTES = 180`** (`db/checkout_state.py`) — a parked checkout older than this is not restored. Resuming a two-day-old "what size would you like?" after a restart is more confusing than starting fresh, and the `order_id` inside it was minted against a cancel window that lapsed long ago. Same class of bound as `CANCEL_WINDOW_MINUTES`, same reason: nothing else marks the flow abandoned.

**A LangGraph checkpointer does not solve this and would break history.** None of these dicts are graph state — the graph only ever sees `{"messages": [...]}`, rebuilt fresh each turn in `agent_invoke.py`. Adding a checkpointer with a `thread_id` would make LangGraph *append* those re-injected messages to already-persisted thread state, doubling the history every turn. The re-injection and the checkpointer are two answers to the same question; this repo uses the first.

Checks: `python -m tests.test_checkout_state` — 4 checks covering the round-trip, row cleanup after an order is placed, the TTL, and that the hook is actually wired into `_run_serialized`.

---

## Key Design Decisions

- **Signal strings** — tools return sentinel strings (e.g. `PAYMENT_TRIGGERED|...`) instead of side effects; `bot/router/agent_dispatch.py` parses them and dispatches. This keeps tools pure and testable.
- **Deterministic order flow over LLM tool-calling** — every step of checkout (size, instructions, customer-info reuse, final confirmation, placing the order) is driven by explicit code (`bot/router/order_flow.py`), not the LLM deciding to call a tool each turn. This was a deliberate fix: with no checkpointer, the LLM would silently drop tool calls (replying with plain text instead of sending a poll card) on repeat orders in the same conversation. The LLM is only in the loop for genuinely open-ended parts (menu Q&A, reservations, free-form chat, or an order whose item list can't be parsed).
- **Split DB** — orders/reservations/menu in SQLite (always available, no network), payments/reviews in Supabase (need real-time access from mobile clients). The menu moved local last: it was the one thing a customer could hit before saying a word, so a Supabase blip meant "menu is not available right now" on first contact, and `confirm_order` priced off that same fetch.
- **Domain-grouped packages over flat directories** — `bot/`, `agent/tools/`, and `db.py` were reorganized (this session) into subfolders/files per domain (polls, menu, orders, payment, reviews, auction) instead of one flat pile of same-level files. Every move was a pure relocation — function bodies copied verbatim, cross-references rewired, public import surface (`from db import X`, `from bot.message_handler import handle_message`, etc.) kept unchanged — verified by actually importing every module afterward, not just checking syntax.
- **stable_order_id** — a human-readable ID (`ORD-XXXXXX`, or `TIP-XXXXXX` for tips) separate from the SQLite auto-increment, used as the Supabase payment intent key.
- **Poll chaining is fire-and-forget, enforced client-side** — the bot sends all N chained polls at once and never tracks which have been answered. The alternative (send one, wait for its `poll_response`, send the next) is another per-user state machine in `order_flows`, which is in-memory only and would strand a customer mid-ratings on any restart. The cost is that collision-avoidance is now pure convention — see gap #21.
- **Fast-path bypass** — common intents (menu, pay, order history, cancel) are intercepted before the agent to reduce latency and LLM cost.
- **DSL validation** — all outbound DSL payloads are validated against a JSON schema before sending to prevent malformed UI cards reaching clients. Inbound DSL (customer responses) is not schema-validated.
- **Fulfillment and terms before final confirm, not after** — the fulfillment method/detail cards fire right after customer info is settled, the terms card (if needed) right after those, and the "Shall I proceed?" poll is deliberately the *last* step, once fulfillment details and consent are both already settled. The receipt reflects everything at once, and the customer never confirms an order before fulfillment or the agreement has entered the picture.
- **Feature flags over code removal for the cash-only transition** — `ONLINE_PAYMENTS_ENABLED`/`TIPS_ENABLED` gate behavior; the Swich integration, tip handlers, and v1/v2 receipt senders all stay in the tree, untouched. Re-enabling is an `.env` edit plus a restart, not a revert. Both default to `false` on purpose: this gates real money movement, so a missing env var must fail closed.
- **Terms gate is automatic and version-keyed, not a CLI** — unlike auctions and order status, consent isn't staff-triggered: it fires from the order flow itself, and `has_agreed(user_id, terms_id, version)` means a repeat customer signs once rather than per order. Sending it on first contact was rejected — most customers never place an order, so it would store thousands of signatures for interactions that never happened, and open every conversation with a legal document. The terms text also deliberately never passes through the LLM: a paraphrased contract is a fabricated one.
- **Manual CLI triggers over premature admin UI** — both `create_and_send_auction()` and `send_order_status_update()` are called directly (via `test.py`/`update_order_status.py`), not exposed to the LLM or gated behind a permission system that doesn't exist yet. A real staff surface can call the same functions later without touching the underlying logic.

---

## Known Fragility / Gaps

1. **Name-matching between Supabase and SQLite** for per-item ordering — plain text match on lowercased item name, no foreign keys. Typo or rename in Supabase silently defaults the item back to orderable.
2. **No admin/staff permission check** anywhere — `ordering_config.py` mitigates this for ordering toggles (hand-edited, restart-required, no chat exposure), but is worth keeping in mind for any future admin-style feature. `HumanInTheLoopMiddleware` would be the right LangChain primitive if this becomes a priority.
3. **`orders` table (SQLite) vs. `stable_order_id`** — confirm whether `update_order_room_id` / `update_order_stable_id` fully reconcile these now, or if older rows predate the random-ID scheme and still carry the old DB-generated ID.
4. **Schema/DSL version drift** — any new DSL type or field needs simultaneous updates to the sender, `schema.json` (Python-validated outbound path only), and the Dart handler. DSL sent from Edge Functions bypasses Python's `safe_send_dsl()` entirely — no schema enforcement on that path.
5. ~~**In-memory checkout state doesn't survive a restart**~~ — **fixed.** `order_flows` and friends are write-through cached to the `checkout_state` table, hooked once at `_run_serialized` rather than at each mutation site (see Conversation History). What's left is narrower: state is only saved at *turn* boundaries, so a crash *during* a turn loses that turn's progress (the `finally` covers a raised exception, not a killed process), and the legacy native-poll debounce path in `custom_events.py` mutates `last_orders` outside the lock, so those writes aren't captured.
6. **No dedicated `tips` table** — tip payments live in `payment_intents` distinguished only by the `TIP-` prefix; fine for the payment mechanics, not great for tip-specific reporting.
7. **`poll_results` DSL's `results` breakdown array is only ever populated for rating polls** — the Flutter side already renders a generic percentage-bar breakdown for any poll type, but no Python service computes/sends that data for size/flavor/confirmation polls yet.
8. **No auction creation trigger** — `create_and_send_auction()` has to be called manually (see Auctions section); there's no chat command, Supabase-polling, or staff UI wired up yet. Deliberate scope cut, not an oversight.
9. **No permission check on who can create an auction or place a bid** — same class of gap as #2, just unmitigated here (ordering_config's "hand-edited, restart-required" approach doesn't apply to auctions since creation is a runtime function call). Anyone who can call `create_and_send_auction()` or send a `bid_confirmation` DSL event can act.
10. **Auction `payment_intents` reuse the winner's Matrix `user_id`/saved customer info** — same "no `tips` table"-style tradeoff as #6: fine for payment mechanics, no dedicated auction-analytics table if that's ever needed.
11. **No order_status trigger UI** — `update_order_status.py` is a manually-run CLI script (same deliberate scope cut as auction creation, gap #8); a real staff dashboard is the natural next step if this goes into an actual restaurant, calling the same `send_order_status_update()` function underneath.
12. ~~**In-memory checkout state doesn't distinguish "mid-fulfillment" from any other in-progress stage**~~ — **fixed with gap #5.** The fulfillment stages were never special; they persist and restore with every other `order_flows` stage now, since the whole dict is snapshotted rather than named fields.
13. **Marking cash collected is a manual CLI step, and nothing reconciles it** — `update_order_status.py ORD-XXXXXX paid` is the only way an order leaves `'pending'` now that no gateway callback fires. Forget to run it and the order sits in the customer's "Pending" tab forever with no Reorder button. There's no till integration, no daily reconciliation, and no permission check on who can run it (gap #2).
14. **JNO-238 (Cash as a *selectable* payment method) deliberately not built** — while `ONLINE_PAYMENTS_ENABLED=false` cash is the only option, so a picker with one choice is UI for a decision that can't be made. Build it when online payment returns and there are genuinely two options.
15. **The Flutter history page silently swallows unknown statuses** — `_filtered` matches `paid`/`completed`/`delivered`, then `pending`, then `cancelled`, with no catch-all tab. Any status outside that set makes the order vanish from every filter rather than showing under a fallback. This already bit the `"cash"` attempt (see Cash-Only Mode); anything new written to `orders.status` must map onto one of those three buckets or the UI needs a fourth.
16. **Auction winners get a dead "Pay Now" button while cash-only** — `auction_result`'s winner card renders a pay button off `order_id`, but no payment intent exists to back it, exactly like the `order_confirmation` receipt did before v3. Latent rather than live, since auctions have no creation trigger (gap #8), but it's the same unfixed shape.
17. **Every device is trusted, and every invite is accepted** — `ignore_unverified_devices=True` on all sends, and `auto_join` joins any room it's invited to with no allowlist. Both are deliberate (a shop bot nobody verifies, an app whose Discovery flow creates the DM), but they're the same unmitigated-permission class as gaps #2/#9.
18. **`store/` is unbackuped local state that can't be regenerated** — delete it and the bot logs in as a *new* device, so every customer's client has to re-share room keys; history in encrypted rooms sent to the old device becomes unreadable. It's gitignored (it holds an access token), so nothing backs it up.
19. **Per-sender locks and task set grow without bound** — `_sender_locks`/`_running_tasks` in `bot/message_handler.py` never evict a sender. One `asyncio.Lock` per customer forever; fine at a coffee shop's user count, would need eviction at scale.
20. **The in-repo schema is now a second copy, not the source of truth** — `dsl-spec/` was an unversioned Desktop folder shared by hand with the Flutter app; copying it in fixed the "not in git" problem but made the drift in gap #4 concrete. Nothing checks the two copies against each other.
21. **Nothing guards against a `chain_id` collision** — since the client dropped its "previous question already answered" check, two senders picking the same value silently merge into one card, with no warning on either side. Only convention (the `rate_` prefix on rating polls) keeps them out of the checkout chain. See DSL Protocol → Poll chaining.
22. **No terms-authoring surface** — the terms text is a hand-edited constant with a hand-bumped `TERMS_VERSION` (same "no admin UI yet" class as gaps #8/#11). The version suffixes in git history (`2026-08-05b/c/d`) are test artefacts from re-testing against the client's `terms_id@version` cache, not real revisions.
23. ~~**`awaiting_terms` is in-memory like every other checkout stage**~~ — **fixed with gap #5.** A restart while the terms card is open now restores the parked order at `awaiting_terms` and the inbound `terms_response` resumes it. The agreement was always safe regardless: written to SQLite before the order resumes, and recorded even when there's no flow to resume.
24. **FAQ matching is substring-only, so re-worded questions miss** — `"what payment methods do you take"` never reaches *"How can I pay?"* mechanically. The LLM layer catches some of these by rephrasing; the rest fall through to normal chat. Deliberate (see FAQ section — the fuzzy tier only ever produced wrong answers), but it means coverage is exactly what you seed.
25. **The bot will still state facts it doesn't have** — the FAQ fast path is only authoritative for seeded questions. Everything else relies on a prompt rule, and a prompt is not a constraint. It invented "plenty of parking right outside the cafe" once. Prompt hardening reduced it (wifi/seating/catering now defer correctly), but the only deterministic fix is seeding the question. Hours and location ARE legitimately known — they're in `CAFE KNOWLEDGE` in `agent/prompt.py`.
26. **The calculator's item set is only as good as the category the model picks.** *(Narrowed 2026-08-13: `item_name` is gone, so the model can no longer collapse a general request to one silently-priced item — see Calculator. What's left is the category choice itself, and note `CALC_NO_CATEGORY|` only catches names that don't **exist** — a valid-but-wrong category, "coffee" → `["Cold Drinks"]`, still produces a perfectly good card answering the wrong question. It is at least visible: every option is a real item at its real price, so the customer can see the list is wrong and say so. Closing it entirely needs intent→category mapping — a keyword column on `menu_items` is the obvious shape — which is the same class of problem as FAQ matching (gap #24). Not built: the failure is now cosmetic and self-correcting, and tagging would move the judgement rather than delete it.)* Prices and option lists are resolved server-side from `menu_items`, so neither can be wrong. What remains model-judgement is *which* categories a request maps to — "coffee for 40" → `["Hot Classics", "Premium Brews", "Specialty Lattes"]` is a semantic call. Far smaller surface than the original gap (one category name vs. 17 hand-written options with hand-written prices), and bounded — every option shown is a real item at its real price whatever it picks.
27. **A hallucination self-reinforces through history** — a wrong answer written to `conversation_history` gets copied verbatim on the next similar question until it ages out of the 20-message cap. Same mechanism as the placeholder bug above, but with a false fact instead of a stub. There is no detection for this; it needs a manual history clear.
