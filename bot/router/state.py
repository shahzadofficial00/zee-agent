import asyncio
import logging

# Same logger name/identity every other message_handler-derived module shared
# before the split into bot/router/* — keeps log output unchanged.
logger = logging.getLogger("bot.message_handler")

# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL STATE — all in-memory, reset on bot restart
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

_REORDER_AFFIRMATIONS = {
    "yes", "yeah", "yup", "yep", "sure", "ok", "okay", "same", "same again",
    "same order", "keep it", "keep it the same", "go ahead", "please do",
    "yes please", "same please", "do it",
}
