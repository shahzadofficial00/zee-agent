import asyncio
import logging
import os
from db import (
    load_history, save_history,
    load_checkout_state, save_checkout_state, delete_checkout_state,
)

# Same logger name/identity every other message_handler-derived module shared
# before the split into bot/router/* — keeps log output unchanged.
logger = logging.getLogger("bot.message_handler")

# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL STATE — in-memory and reset on bot restart, except
# conversation_histories, which is write-through cached to SQLite (see bottom).
# ─────────────────────────────────────────────────────────────────────────────
conversation_histories: dict[str, list] = {}   # per-user chat history fed to the agent
processed_event_ids: set[str] = set()          # dedupe guard against Matrix event replays
last_orders: dict[str, dict] = {}               # most recent order per user (for payment/receipt flow)
order_counters: dict[str, int] = {}
poll_response_timers: dict[str, asyncio.TimerHandle] = {}  # debounce timers for multi-select polls
pending_orders: dict[str, str] = {}             # order text in progress per user, injected as a reminder

# order_flows[sender] drives the mandatory per-item size + special-instructions
# loop deterministically in code instead of leaving the sequencing decision to
# the LLM. The model has no real memory across turns (no checkpointer — every
# poll answer is a fresh agent.ainvoke), so after a few identically-shaped size
# questions it starts imitating its own flattened text history and silently
# drops the tool call instead of asking about the next item. Since "which items
# still need a size/instructions answer" is fully mechanical and already known
# from the order text, that decision doesn't need an LLM at all.
order_flows: dict[str, dict] = {}

# Correctly-built "ItemName (Size - instructions) xQty" summary from the last
# completed order_flow, captured deterministically at collection time — used
# to recover reliably if the customer declines the final confirmation and then
# wants the same order again, instead of making the LLM reconstruct the item
# list from (possibly truncated) conversation history.
last_order_line: dict[str, str] = {}
# Structured twin of last_order_line (distinct_items/qtys/sizes/instructions) —
# lets a "same again?" reorder rebuild order_flows directly and skip straight to
# the customer-info stage, instead of re-deriving it from text via the LLM.
last_order_state: dict[str, dict] = {}
# Senders currently waiting on a yes/no reply to "want the same order again?"
# after declining the confirmation poll — checked in handle_message.
awaiting_reorder_confirmation: set[str] = set()
# Senders who sent a calculator result and were asked "shall I place this
# order?" — maps to the ready-made order line ("I want to order: Latte x20").
# Same shape as awaiting_reorder_confirmation, but it carries the line because
# a calculator order has no last_order_state to rebuild from.
awaiting_calculator_order: dict[str, str] = {}

_REORDER_AFFIRMATIONS = {
    "yes", "yeah", "yup", "yep", "sure", "ok", "okay", "same", "same again",
    "same order", "keep it", "keep it the same", "go ahead", "please do",
    "yes please", "same please", "do it",
}

# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATION HISTORY PERSISTENCE — write-through SQLite cache
# ─────────────────────────────────────────────────────────────────────────────
HISTORY_CAP = 20

# How many of those messages actually get injected into an agent call. Separate
# from the cap because OpenRouter derives a prompt-token ceiling from the credit
# balance on PAID models (402 "Prompt tokens limit exceeded" at ~10k on a
# near-empty one), and the 27KB system prompt + tool schemas already eat most of
# it. Free (":free") models have no such cap, so this is back at the full cap --
# set AGENT_HISTORY_WINDOW=6 if OPENROUTER_MODEL is pointed at a paid model
# again before the balance is topped up.
HISTORY_WINDOW = int(os.getenv("AGENT_HISTORY_WINDOW", str(HISTORY_CAP)))


def ensure_history_loaded(sender: str) -> None:
    """First touch per sender since restart: pull their history back from SQLite
    instead of starting empty."""
    if sender not in conversation_histories:
        conversation_histories[sender] = load_history(sender)


def persist_history(sender: str) -> None:
    """Write-through so a restart doesn't lose this sender's history. Call after
    every mutation. The cap lives here rather than at each call site: the poll
    paths append without capping, and persistence turns that unbounded growth
    into an unbounded row on disk."""
    history = conversation_histories[sender][-HISTORY_CAP:]
    conversation_histories[sender] = history
    save_history(sender, history)


# ─────────────────────────────────────────────────────────────────────────────
# CHECKOUT STATE PERSISTENCE — write-through SQLite cache (gap #5)
#
# Same pair as conversation history above, but snapshotting the whole per-user
# checkout rather than one dict. Deliberately NOT hooked at each mutation site:
# these dicts are written from ~20 places across order_flow.py,
# dsl_text_events.py and message_handler.py, and one missed call site is an
# order that silently doesn't survive a restart. Instead message_handler.py
# loads once and saves once around the per-sender lock, so every mutation a turn
# makes is captured whichever path made it — including paths added later.
#
# A LangGraph checkpointer does not cover any of this: none of these dicts are
# graph state, and the agent is invoked with a freshly-built message list each
# turn rather than a thread.
# ─────────────────────────────────────────────────────────────────────────────
_checkout_loaded: set[str] = set()   # first-touch-since-restart guard, per sender
_checkout_on_disk: set[str] = set()  # senders with a row, so a no-op turn skips the DELETE


def ensure_checkout_loaded(sender: str) -> None:
    """First touch per sender since restart: pull their parked checkout back out
    of SQLite. A no-op every turn after that — the in-memory dicts are the live
    copy, disk is only the restart backstop."""
    if sender in _checkout_loaded:
        return
    _checkout_loaded.add(sender)
    saved = load_checkout_state(sender)
    if not saved:
        return
    _checkout_on_disk.add(sender)
    if saved.get("order_flow"):
        order_flows[sender] = saved["order_flow"]
    if saved.get("last_order_line"):
        last_order_line[sender] = saved["last_order_line"]
    if saved.get("last_order_state"):
        last_order_state[sender] = saved["last_order_state"]
    if saved.get("last_order"):
        last_orders[sender] = saved["last_order"]
    if saved.get("pending_order"):
        pending_orders[sender] = saved["pending_order"]
    if saved.get("calculator_order"):
        awaiting_calculator_order[sender] = saved["calculator_order"]
    if saved.get("awaiting_reorder"):
        awaiting_reorder_confirmation.add(sender)
    logger.info(f"🔄 Restored checkout state for {sender} (stage="
                f"{(saved.get('order_flow') or {}).get('stage', '—')})")


def persist_checkout(sender: str) -> None:
    """Snapshot this sender's checkout to SQLite. Called once per turn, after the
    handler has finished, while the per-sender lock is still held."""
    state = {
        "order_flow": order_flows.get(sender),
        "last_order_line": last_order_line.get(sender),
        "last_order_state": last_order_state.get(sender),
        "last_order": last_orders.get(sender),
        "pending_order": pending_orders.get(sender),
        "calculator_order": awaiting_calculator_order.get(sender),
        "awaiting_reorder": sender in awaiting_reorder_confirmation,
    }
    if any(state.values()):
        save_checkout_state(sender, state)
        _checkout_on_disk.add(sender)
    elif sender in _checkout_on_disk:
        # Order placed or cancelled — drop the row rather than storing an empty
        # blob, so the table only ever holds live checkouts.
        delete_checkout_state(sender)
        _checkout_on_disk.discard(sender)
