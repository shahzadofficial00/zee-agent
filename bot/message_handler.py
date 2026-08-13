
# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
import asyncio
import logging
import langsmith as ls
from nio import MatrixRoom, RoomMessageText
from bot.matrix_client import matrix_client, BOT_START_TIME, send_text
from bot.menu.menu_service import send_menu
logger = logging.getLogger(__name__)
import re as _re_pay
import re as _re
from db import get_pending_payment_by_user, update_payment_status

# ── Extracted router pieces — deterministic order-flow state machine, agent ──
# invocation/dispatch, and DSL/custom event routing all now live in bot/router/. ──
from bot.router.state import (
    conversation_histories, processed_event_ids, last_orders, pending_orders,
    order_flows, awaiting_reorder_confirmation, awaiting_calculator_order,
    _REORDER_AFFIRMATIONS, ensure_history_loaded, persist_history,
)
from bot.router.order_flow import _start_order_flow, _start_fulfillment_stage, _handle_reorder_affirmation
from bot.router.agent_invoke import _invoke_agent_with_retry
from bot.router.agent_dispatch import _dispatch_agent_result
from bot.router.dsl_text_events import handle_dsl_text_event
from bot.router.custom_events import handle_custom_event as _handle_custom_event


# ─────────────────────────────────────────────────────────────────────────────
# CONCURRENCY — nio awaits event callbacks inline (async_client.py::_on_event),
# so doing the work here directly serializes every room behind the slowest turn:
# one 30s agent call would stall all other customers. Each event gets its own
# task, serialized per sender because order_flows is a per-user state machine and
# two of that user's events must not interleave. Text and custom (poll) events
# share one lock — a poll answer and a typed message drive the same flow.
# ─────────────────────────────────────────────────────────────────────────────
_sender_locks: dict[str, asyncio.Lock] = {}
_running_tasks: set[asyncio.Task] = set()   # asyncio only holds weak refs to tasks


# ── Agreement-history fast path (JNO-98) ─────────────────────────────────────
# Word-bounded like the other fast paths so "I agree" during a terms card can't
# trip it. `terms?`/`agreements?` are optional-plural because "term history"
# (singular) missed this, fell through to the agent, and got answered with "no
# such thing exists" — which the summariser then hardened into history as fact.
# Module scope so tests bind to the shipped pattern rather than a copy of it.
_TERMS_HISTORY_PATTERN = _re.compile(
    r'\b(agreements? history|my agreements?|show my agreements?|terms? history|'
    r'my terms|signed terms|what did i agree to|what have i agreed to)\b',
    flags=_re.IGNORECASE,
)


async def _run_serialized(handler, room, event):
    lock = _sender_locks.setdefault(event.sender, asyncio.Lock())
    async with lock:
        try:
            await handler(room, event)
        except Exception as e:
            # Must not escape: an exception here used to propagate out of the nio
            # callback and kill sync_forever (the whole bot) for every user.
            logger.error(f"unhandled error handling {event.event_id}: {e}", exc_info=True)


def _spawn(handler, room, event):
    task = asyncio.create_task(_run_serialized(handler, room, event))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


async def handle_message(room: MatrixRoom, event: RoomMessageText):
    _spawn(_handle_message, room, event)


async def handle_custom_event(room: MatrixRoom, event):
    _spawn(_handle_custom_event, room, event)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN MESSAGE HANDLER — routes every incoming RoomMessageText event
# ─────────────────────────────────────────────────────────────────────────────
async def _redact_cancel_countdown(room_id: str, stable_order_id: str) -> None:
    """Remove the cancel-window countdown once the window has actually been used
    (JNO-85).

    The card only knows its `ends_at`, so left alone it keeps ticking "Free to
    cancel" above the "order cancelled" message for the rest of the 15 minutes.
    Redaction is the only way back: there's no inbound event and no id on the
    card, both deliberate in the DSL contract.

    Best-effort — the order is already cancelled and the customer has been told,
    so a failed redact is a cosmetic leftover, never a lost cancellation.
    """
    try:
        from db import get_order_by_stable_id
        event_id = (get_order_by_stable_id(stable_order_id) or {}).get("countdown_event_id")
        if not event_id:
            return
        await matrix_client.room_redact(room_id, event_id, reason="Order cancelled")
        logger.info(f"⏳ Countdown redacted for {stable_order_id}")
    except Exception as e:
        logger.error(f"⏳ Countdown redact failed for {stable_order_id}: {e}")


async def _handle_message(room: MatrixRoom, event: RoomMessageText):
    # ── Ignore messages from before bot start, from itself, or already processed ──
    if event.server_timestamp < BOT_START_TIME:
        return
    if event.sender == matrix_client.user_id:
        return
    if event.event_id in processed_event_ids:
        return
    processed_event_ids.add(event.event_id)

    room_id = room.room_id
    sender  = event.sender

    # ── Handle DSL events FIRST ───────────────────────────────────────────────
    source = getattr(event, 'source', {})
    content = source.get('content', {})
    dsl = content.get('ai.jaeno.dsl', {})

    message = None  # set below — either from an order_summary DSL or from event.body

    if dsl:
        handled, message_override = await handle_dsl_text_event(dsl, sender, room_id)
        if handled:
            return
        message = message_override

    # ── Now safe to check body ────────────────────────────────────────────────
    if message is None:
        message = event.body.strip()
    print(f"📨 MESSAGE RECEIVED: '{message}' from {sender}")
    if not message:
        return

    # ── Deterministic order flow is waiting on freeform name/phone text ──────────
    flow_state = order_flows.get(sender)
    if flow_state and flow_state.get("stage") in ("await_name", "await_phone"):
        await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
        if flow_state["stage"] == "await_name":
            flow_state["customer_name"] = message.strip()
            flow_state["stage"] = "await_phone"
            await matrix_client.room_typing(room_id, typing_state=False)
            await send_text(room_id, "And what's the best phone number to reach you?")
        else:
            flow_state["customer_phone"] = message.strip()
            from db import save_customer
            save_customer(sender, flow_state["customer_name"], flow_state["customer_phone"])
            await matrix_client.room_typing(room_id, typing_state=False)
            await _start_fulfillment_stage(sender, room_id)
        return

    # ── "Shall I place that order?" after a calculator result ────────────────
    # The order line was already built from real menu rows when the result came
    # in, so a "yes" runs the ordinary deterministic flow — no LLM, no
    # rebuilding the item list from conversation history.
    if sender in awaiting_calculator_order:
        order_line = awaiting_calculator_order.pop(sender)
        if message.strip().lower() in _REORDER_AFFIRMATIONS:
            await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
            if await _start_order_flow(sender, room_id, order_line):
                await matrix_client.room_typing(room_id, typing_state=False)
                return
        # Anything else (a "no", or a different question) falls through to
        # normal handling — the offer is simply dropped.

    # ── Sender just declined confirmation — check if this is their "same again?" reply ──
    if sender in awaiting_reorder_confirmation:
        await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
        if await _handle_reorder_affirmation(sender, room_id, message):
            await matrix_client.room_typing(room_id, typing_state=False)
            return
        # Not an affirmation — falls through below (fresh order attempt or free-form chat).

    if message.lower().startswith("i want to order:"):
        pending_orders[sender] = message
        await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
        if await _start_order_flow(sender, room_id, message):
            await matrix_client.room_typing(room_id, typing_state=False)
            return
        # Couldn't parse an item list — fall through to the normal agent path below.

    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)

    try:
        # ── Cancel order ──────────────────────────────────────────────────────
        # \bcancel\b, not a substring check — this branch now writes 'cancelled'
        # into the orders table, so "what's your cancellation policy?" / "my last
        # order was cancelled" must not reach it. Same word-boundary treatment
        # the pay and order-history fast paths already get.
        _CANCEL_INTENT_PATTERN = _re.compile(r'\bcancel\b', flags=_re.IGNORECASE)
        if _CANCEL_INTENT_PATTERN.search(message):
            if sender in pending_orders:
                del pending_orders[sender]

            # JNO-239 — while cash-only there is no payment_intents row to cancel,
            # so the Supabase lookup below can't see the order the customer means.
            # Left unguarded it matched a stale pre-cash-only pending row for an
            # OLDER order and replied "cancelled!" while the real one stood.
            from config import ONLINE_PAYMENTS_ENABLED
            if not ONLINE_PAYMENTS_ENABLED:
                await matrix_client.room_typing(room_id, typing_state=False)
                # Mid-checkout there's no orders row yet — drop the in-flight flow
                # instead, or we'd cancel their previous (completed) order instead.
                if order_flows.pop(sender, None):
                    await send_text(room_id,
                        "❌ Order cancelled!\nYou can place a new order anytime. 🍽️")
                    return
                from db import cancel_last_pending_order
                cancelled_id = cancel_last_pending_order(sender, room_id)
                if cancelled_id:
                    last_orders.pop(sender, None)
                    await _redact_cancel_countdown(room_id, cancelled_id)
                    await send_text(room_id,
                        f"❌ Order {cancelled_id} cancelled!\n"
                        "You can place a new order anytime. 🍽️")
                else:
                    # Covers both "nothing pending" and "pending but past the
                    # cancel window" — one message rather than a second query to
                    # tell them apart, since the answer is the same either way.
                    await send_text(room_id,
                        "You don't have a recent order to cancel. "
                        "If you need to change an order already with the kitchen, "
                        "please give us a call. 📞")
                return

            pending = await get_pending_payment_by_user(sender=sender, room_id=room_id)
            if pending:
                await update_payment_status(
                    order_id=pending['order_id'],
                    sender=sender,
                    status='cancelled'
                )
                if sender in last_orders:
                    del last_orders[sender]
                await matrix_client.room_typing(room_id, typing_state=False)
                await send_text(room_id,
                    "❌ Order cancelled!\n"
                    "You can place a new order anytime. 🍽️"
                )
                return
            else:
                await matrix_client.room_typing(room_id, typing_state=False)
                await send_text(room_id, "No active order to cancel!")
                return

        # ── Fast-path: menu request bypasses the agent entirely ───────────────
        if 'menu' in message.lower():
            await matrix_client.room_typing(room_id, typing_state=False)
            await send_menu(room_id)
            return

        # ── Fast-path: order history request bypasses the agent entirely ─────
        _ORDER_HISTORY_PATTERN = _re.compile(
            r'\b(order history|my orders|show my orders|show my order history|previous orders|past orders|my order history|what did i order)\b',
            flags=_re.IGNORECASE,
        )
        if _ORDER_HISTORY_PATTERN.search(message.lower().strip()):
            await matrix_client.room_typing(room_id, typing_state=False)
            from bot.orders.order_history_service import send_order_history_card
            await send_order_history_card(room_id, sender)
            return

        # ── Fast-path: agreement history bypasses the agent entirely (JNO-98) ─
        # Pattern lives at module scope (above) so tests bind to the shipped
        # regex rather than a copy of it.
        if _TERMS_HISTORY_PATTERN.search(message.lower().strip()):
            await matrix_client.room_typing(room_id, typing_state=False)
            from bot.terms.terms_service import send_terms_history_card
            await send_terms_history_card(room_id, sender)
            return

        # ── Fast-path: payment intent request bypasses the agent entirely ────
        _PAY_INTENT_PATTERN = _re_pay.compile(
                r'\b(pay|payment|pay now|make payment|i want to pay|let me pay|can i pay|how do i pay|pay for (it|this|my order))\b',
                flags=_re_pay.IGNORECASE
        )
        if _PAY_INTENT_PATTERN.search(message.lower().strip()):
            await matrix_client.room_typing(room_id, typing_state=False)
            # JNO-239 — payment_intents rows created BEFORE the cash-only switch
            # are still 'pending', so this path would happily resend a live Swich
            # card for an old order. Answer with the cash message instead.
            from config import ONLINE_PAYMENTS_ENABLED
            if not ONLINE_PAYMENTS_ENABLED:
                await send_text(room_id,
                    "💵 We're taking cash payments only right now — "
                    "just pay when you receive your order. No online payment needed!"
                )
                return
            pending = await get_pending_payment_by_user(sender=sender, room_id=room_id)
            if pending:
                from bot.payment.payment_service import send_existing_payment_card
                await send_existing_payment_card(room_id=room_id, pending=pending)
            else:
                await send_text(room_id, "💳 Please place an order first before paying!")
            return

        # ── Fast-path: a confident FAQ hit bypasses the agent (JNO-55) ───────
        # Deterministic for the same reason the order flow is (CLAUDE.md):
        # matching a question against a stored list is mechanical, and leaving
        # it to the LLM meant it imitated its own flattened history instead of
        # calling show_faq — it answered "[FAQ card sent]", then "Got it!", and
        # once invented opening hours rather than using the stored answer.
        # Sits after the pay path so "how do I pay?" keeps its existing reply.
        # Anything get_faq() can't match confidently still falls through to the
        # agent, which can call show_faq/show_faqs itself.
        # Anchored to the WHOLE message: "help" is the browse-all trigger, but
        # "help me order a latte" is a real request that must reach the agent.
        # Leading/trailing quotes tolerated so this behaves like the single-FAQ
        # lookup below, which matches on substring and so never noticed them.
        _FAQ_LIST_PATTERN = _re.compile(
            r'[\'"]*(faq|faqs|help|questions|common questions|all questions)[\s?!.\'"]*',
            flags=_re.IGNORECASE,
        )
        from db import get_faq as _get_faq, get_faqs as _get_faqs
        _wants_faq_list = bool(_FAQ_LIST_PATTERN.fullmatch(message.strip()))
        _faq_hit = None if _wants_faq_list else _get_faq(message)
        if _faq_hit or _wants_faq_list:
            await matrix_client.room_typing(room_id, typing_state=False)
            from bot.faq.faq_service import send_faq_card
            if _faq_hit:
                await send_faq_card(room_id, [_faq_hit], title=_faq_hit["question"])
            else:
                await send_faq_card(room_id, _get_faqs())
            return

        # ── AI agent ──────────────────────────────────────────────────────────
        print(f"📨 [{room_id}] {sender}: {message}")

        ensure_history_loaded(sender)

        # ── Inject pending order context into agent ───────────────────────────
        # Add context hint for very short messages so Gemini doesn't generate empty
        if len(message.strip().split()) <= 2:
           user_content = f"{message}\n[Please continue the conversation naturally based on context above]"
        else:
            user_content = message

        messages = []
        for msg in conversation_histories[sender][-20:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        if sender in pending_orders:
            messages.append({"role": "user", "content": f"[Reminder — order in progress: {pending_orders[sender]}]"})
        messages.append({"role": "user", "content": user_content})

        # ── Invoke the agent under LangSmith tracing, with a retry for blank generations ──
        with ls.tracing_context(
            project_name="dot-cafe-bot",
            enabled=True,
            tags=["production", "dot-cafe"],
            metadata={"sender": sender, "room_id": room_id},
        ):
            try:
                result = await _invoke_agent_with_retry(messages, sender)
            except asyncio.TimeoutError:
                await matrix_client.room_typing(room_id, typing_state=False)
                await send_text(room_id, "Sorry, that took too long! Please try again 🙏")
                logger.warning(f"⏰ Agent timeout for {sender}")
                return

        clean_reply = await _dispatch_agent_result(result, room_id, sender)

        # ── Bookkeeping: append this turn to conversation history, cap at 20 ──
        conversation_histories[sender].append({"role": "user", "content": message})
        conversation_histories[sender].append({"role": "assistant", "content": clean_reply if clean_reply else "Got it!"})

        persist_history(sender)

    except Exception as e:
        # ── Catch-all: never let an unhandled exception go silent — show an outage banner ──
        await matrix_client.room_typing(room_id, typing_state=False)
        from bot.banner_service import send_banner_card
        await send_banner_card(
            room_id=room_id,
            variant="outage",
            title="Something Went Wrong",
            message="We ran into a small issue on our end — please try again in a moment.",
        )
        logger.error(f"handle_message error: {e}", exc_info=True)
