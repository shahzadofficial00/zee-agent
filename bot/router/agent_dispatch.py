import re as _re
import asyncio
import uuid as _uuid
from bot.matrix_client import matrix_client, send_text
from bot.menu.menu_service import send_menu, send_item_card, send_category_card
from bot.reviews.review_scheduler import schedule_review
from bot.router.state import pending_orders, last_orders, logger
from bot.router.ids import generate_unique_order_id

async def _send_cancel_countdown(room_id: str, stable_order_id: str) -> None:
    """Show the customer the 15-minute free-cancellation clock (JNO-85).

    Anchored to the order row's `created_at` — the same column
    cancel_last_pending_order() filters on — rather than to "now", which would
    land a second or two late and leave the timer running after cancelling had
    already stopped working.

    SQLite writes CURRENT_TIMESTAMP as naive UTC 'YYYY-MM-DD HH:MM:SS'; the
    client needs an offset-aware ISO string, so the tzinfo is attached here.
    Best-effort: a countdown is a nicety, and nothing about the order depends
    on it, so any failure is logged and swallowed rather than breaking a
    receipt that already went out.
    """
    try:
        from datetime import datetime, timedelta, timezone
        from db import get_order_by_stable_id
        from db.orders import CANCEL_WINDOW_MINUTES
        from bot.countdown.countdown_service import send_countdown_card

        order = get_order_by_stable_id(stable_order_id)
        created_at = (order or {}).get("created_at")
        if not created_at:
            logger.warning(f"⏳ No created_at for {stable_order_id} — skipping countdown")
            return

        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        ends_at = (created + timedelta(minutes=CANCEL_WINDOW_MINUTES)).isoformat()

        event_id = await send_countdown_card(
            room_id,
            title="Free to cancel",
            ends_at=ends_at,
            subtitle='Reply "cancel" to call off this order — no charge.',
            expired_text="Cancellation window closed — give us a call and we'll help.",
        )
        if event_id:
            # Kept on the order row so cancelling can redact the card — a
            # countdown still offering "free to cancel" above an "order
            # cancelled" message is a small lie, and nothing else can retract it.
            from db import set_order_countdown_event
            set_order_countdown_event(stable_order_id, event_id)
    except Exception as e:
        logger.error(f"⏳ Cancel countdown failed for {stable_order_id}: {e}")


def _is_history_placeholder(text: str) -> bool:
    """True for a bracketed pseudo-reply like "[Rating poll(s) sent for: X]" or
    "[FAQ card sent]".

    No branch produces these any more — that was the bug. clean_reply is written
    into conversation history by message_handler and re-injected into the next
    agent call, so the model started emitting one as its own literal answer
    instead of calling the tool. Seen in production: a customer got
    "[FAQ card sent]" three turns running, no cards.

    Kept as a backstop, because rows written before the fix are still on disk
    and the model will copy those until they age out of the 20-message cap.
    """
    return bool(_re.fullmatch(r'\[.*\bsent\b.*\]', text.strip(), _re.DOTALL))


# ─────────────────────────────────────────────────────────────────────────────
# AGENT RESULT DISPATCH — parse signal strings out of the agent's messages,
# send the matching DSL card/flow, and return cleaned reply text for history.
# ─────────────────────────────────────────────────────────────────────────────
async def _dispatch_agent_result(result, room_id: str, sender: str, fulfillment: dict | None = None) -> str:
    """Detect trigger markers in an agent result, dispatch the matching card/flow,
    and return the cleaned reply text for conversation-history bookkeeping.
    `fulfillment` (only set by the deterministic order flow) carries the
    {method, summary, order_id} collected via the fulfillment_method flow —
    when present, the order gets the pre-generated order_id and an
    order_confirmation v2 card instead of a freshly generated id + v1 card."""
    reply = ""
    if isinstance(result, dict) and "messages" in result:
        last    = result["messages"][-1]
        content = last.content if hasattr(last, "content") else str(last)
        if isinstance(content, list):
            reply = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ).strip()
        else:
            reply = content
    else:
        reply = str(result)
    print(f"📋 ALL MESSAGES: {[str(m.content) for m in result['messages']]}")

    # ── Flags for every signal string a tool can emit this turn ──────────────
    triggered_menu = False
    triggered_payment = None
    triggered_order_history = False
    triggered_item = None
    triggered_category = None
    triggered_poll = None
    triggered_multi_poll = None
    triggered_ranking_poll = None
    triggered_banner = None
    triggered_faq = None
    triggered_faq_list = False
    triggered_open_poll = None
    triggered_rating_poll = None
    triggered_poll_history = False


    all_content = [str(m.content) if hasattr(m, 'content') else str(m) for m in result.get('messages', [])]
    print(f"🔍 ALL CONTENT STRINGS: {all_content}")

    # ── Model call failed (retries exhausted) — show an outage banner instead of the raw error ──
    if any("Model call failed" in c for c in all_content):
        triggered_banner = {
            "variant": "outage",
            "title": "Having Trouble Responding",
            "message": "Zee is briefly having trouble responding right now — please try again in a moment.",
            "meta": "",
        }
        reply = ""
        await matrix_client.room_typing(room_id, typing_state=False)
        from bot.banner_service import send_banner_card
        await send_banner_card(room_id=room_id, **triggered_banner)
        return ""

    # ── ORDER_HISTORY_TRIGGERED / MENU_TRIGGERED — simple membership checks ──
    if any("ORDER_HISTORY_TRIGGERED" in c for c in all_content):
        triggered_order_history = True
    if any("MENU_TRIGGERED" in c for c in all_content):
        triggered_menu = True

    # ── ITEM_TRIGGERED|{name} — single item card ──────────────────────────────
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "ITEM_TRIGGERED" in msg_content:
                item_match = _re.search(r'ITEM_TRIGGERED\|(.+)', msg_content)
                if item_match:
                    triggered_item = item_match.group(1).strip()
                break

    # ── CATEGORY_TRIGGERED|{name} — category card ─────────────────────────────
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "CATEGORY_TRIGGERED" in msg_content:
                cat_match = _re.search(r'CATEGORY_TRIGGERED\|(.+)', msg_content)
                if cat_match:
                    triggered_category = cat_match.group(1).strip()
                break

    # ── FAQ_TRIGGERED|{question} / FAQ_LIST_TRIGGERED — JNO-55 / JNO-56 ───────
    # No substring collision between the two markers, unlike the POLL_ family.
    if any("FAQ_LIST_TRIGGERED" in c for c in all_content):
        triggered_faq_list = True
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "FAQ_TRIGGERED" in msg_content:
                faq_match = _re.search(r'FAQ_TRIGGERED\|(.+)', msg_content)
                if faq_match:
                    triggered_faq = faq_match.group(1).strip()
                break

    # ── POLL_TRIGGERED|{question}|{options} — single-choice poll ──────────────
    # (excludes MULTI_POLL_TRIGGERED / OPEN_POLL_TRIGGERED / RANKING_POLL_TRIGGERED,
    # which all share the "POLL_TRIGGERED" substring)
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if (isinstance(msg_content, str) and "POLL_TRIGGERED" in msg_content
                    and "MULTI_POLL_TRIGGERED" not in msg_content
                    and "OPEN_POLL_TRIGGERED" not in msg_content
                    and "RANKING_POLL_TRIGGERED" not in msg_content):
                poll_match = _re.search(r'POLL_TRIGGERED\|(.+?)\|(.+)', msg_content)
                if poll_match:
                    question = poll_match.group(1).strip()
                    options = [o.strip() for o in poll_match.group(2).split(",") if o.strip()]
                    triggered_poll = {"question": question, "options": options}
                break

    # ── MULTI_POLL_TRIGGERED|{question}|{options} — multi-select poll (flavor poll) ──
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "MULTI_POLL_TRIGGERED" in msg_content:
                match = _re.search(r'MULTI_POLL_TRIGGERED\|(.+?)\|(.+)', msg_content)
                if match:
                    question = match.group(1).strip()
                    options = [o.strip() for o in match.group(2).split(",") if o.strip()]
                    triggered_multi_poll = {"question": question, "options": options}
                break

    # ── RANKING_POLL_TRIGGERED|{question}|{options} — drag-to-reorder poll ────
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "RANKING_POLL_TRIGGERED" in msg_content:
                match = _re.search(r'RANKING_POLL_TRIGGERED\|(.+?)\|(.+)', msg_content)
                if match:
                    question = match.group(1).strip()
                    options = [o.strip() for o in match.group(2).split(",") if o.strip()]
                    triggered_ranking_poll = {"question": question, "options": options}
                break

    # ── OPEN_POLL_TRIGGERED|{question}|{placeholder} — free-text poll (special instructions) ──
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "OPEN_POLL_TRIGGERED" in msg_content:
                match = _re.search(r'OPEN_POLL_TRIGGERED\|(.+?)\|(.+)', msg_content)
                if match:
                    triggered_open_poll = {
                        "question": match.group(1).strip(),
                        "placeholder": match.group(2).strip(),
                    }
                break


    # ── RATING_POLL_TRIGGERED|{item_name} — post-order star rating poll ──────
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "RATING_POLL_TRIGGERED" in msg_content:
                match = _re.search(r'RATING_POLL_TRIGGERED\|(.+)', msg_content)
                if match:
                    item_names = [n.strip() for n in match.group(1).split(",") if n.strip()]
                    triggered_rating_poll = {"item_names": item_names}
                break

    # ── POLL_HISTORY_TRIGGERED — customer asked to see their past poll answers ──
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "POLL_HISTORY_TRIGGERED" in msg_content:
                triggered_poll_history = True
                break

    # ── BANNER_TRIGGERED|{variant}|{title}|{message}|{meta} — visual callout ──
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "BANNER_TRIGGERED" in msg_content:
                banner_match = _re.search(r'BANNER_TRIGGERED\|(.+?)\|(.+?)\|(.*?)\|(.*?)(?:\n|$)', msg_content)
                if banner_match:
                    triggered_banner = {
                        "variant": banner_match.group(1).strip(),
                        "title":   banner_match.group(2).strip(),
                        "message": banner_match.group(3).strip(),
                        "meta":    banner_match.group(4).strip(),
                    }
                break

    # ── ORDER_HISTORY_CARD — alternate marker for the same order-history flow ──
    if any("ORDER_HISTORY_CARD" in c for c in all_content):
        triggered_order_history = True

    # ── PAYMENT_TRIGGERED|{amount}|{name}|{phone}|{order_id}|{items}|{line_items_json} ──
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "PAYMENT_TRIGGERED" in msg_content:
                payment_match = _re.search(
                    r'PAYMENT_TRIGGERED\|(\d+)\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|(.+?)(?:\n|$)',
                    msg_content
                )
                if payment_match:
                    items_raw = payment_match.group(5) or ""
                    import json as _json
                    try:
                        line_items = _json.loads(payment_match.group(6))
                    except Exception:
                        line_items = []
                    triggered_payment = {
                        "amount":      int(payment_match.group(1)),
                        "name":        payment_match.group(2),
                        "phone":       payment_match.group(3),
                        "order_id":    payment_match.group(4).strip(),
                        "items":       [s.strip() for s in items_raw.split(";") if s.strip()],
                        "line_items":  line_items,
                    }
                    print(f"🔍 MATCH RESULT: {triggered_payment}")
                break

    # ── ORDER_HISTORY_TRIGGERED — also check list-shaped message content (multimodal blocks) ──
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "ORDER_HISTORY_TRIGGERED" in msg_content:
                triggered_order_history = True
                break
            elif isinstance(msg_content, list):
                for block in msg_content:
                    if "ORDER_HISTORY_TRIGGERED" in str(block):
                        triggered_order_history = True
                        break

    # ── Strip every signal marker out of the visible reply text ───────────────
    # Longer/more-specific markers (MULTI_/OPEN_/RATING_/RANKING_POLL_TRIGGERED) must be
    # stripped BEFORE the generic POLL_TRIGGERED pattern, since that pattern would
    # otherwise match the "POLL_TRIGGERED|..." tail of those and leave a garbage
    # "MULTI_"/"RANKING_" prefix behind in the visible reply.
    reply = reply.replace("MENU_TRIGGERED", "").replace("MENU_CARD", "").strip()
    reply = _re.sub(r'ITEM_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'CATEGORY_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = reply.replace("CATEGORY_CARD", "").strip()
    reply = _re.sub(r'PAYMENT_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = reply.replace("ORDER_HISTORY_TRIGGERED", "").strip()
    reply = _re.sub(r'MULTI_POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'OPEN_POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'RATING_POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'RANKING_POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'BANNER_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = reply.replace("POLL_HISTORY_TRIGGERED", "").strip()
    reply = _re.sub(r'FAQ_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = reply.replace("FAQ_LIST_TRIGGERED", "").strip()
    # show_faq's miss signal — the model is told to answer normally after one,
    # but it sometimes echoes the marker alongside its answer.
    reply = reply.replace("FAQ_NO_MATCH", "").strip()

    if _is_history_placeholder(reply):
        reply = ""


    if "ORDER_HISTORY_CARD" in reply:
        triggered_order_history = True
    reply = reply.replace("ORDER_HISTORY_CARD", "").strip()

    # ── Card-only turns should never also show the raw signal text as a reply ──
    if triggered_menu:
        reply = ""
    if triggered_banner:
        reply = ""
    if triggered_open_poll:
        reply = ""

    if triggered_rating_poll:
        reply = ""
    if triggered_poll_history:
        reply = ""
    if triggered_faq or triggered_faq_list:
        # The card already shows the question and the answer.
        reply = ""
    if triggered_poll or triggered_multi_poll or triggered_ranking_poll:
        # The poll card already renders the question — never also send it as text.
        reply = ""

    # ── Nothing triggered and no text — fall back to a generic apology ────────
    if not reply and not triggered_menu and not triggered_payment and not triggered_order_history and not triggered_item and not triggered_category and not triggered_poll and not triggered_multi_poll and not triggered_ranking_poll and not triggered_banner and not triggered_open_poll and not triggered_rating_poll and not triggered_poll_history and not triggered_faq and not triggered_faq_list:

        reply = "I'm sorry, I didn't quite get that. Could you please repeat?"

    print(f"🤖 REPLY: {reply}")
    print(f"🍽️ TRIGGERED MENU: {triggered_menu}")
    print(f"💳 TRIGGERED PAYMENT: {triggered_payment}")
    print(f"📜 TRIGGERED ORDER HISTORY: {triggered_order_history}")

    clean_reply = reply.replace("ORDER_HISTORY_TRIGGERED", "").replace("ORDER_HISTORY_CARD", "").replace("MENU_TRIGGERED", "").replace("MENU_CARD", "").strip()

    await matrix_client.room_typing(room_id, typing_state=False)

    # ── Dispatch: send the one card/flow that matches whichever trigger fired ──
    if triggered_faq or triggered_faq_list:
        # One card type for both stories — a single entry renders expanded
        # (JNO-55), several render as the accordion (JNO-56).
        from db import get_faq, get_faqs
        from bot.faq.faq_service import send_faq_card
        # Whatever goes in clean_reply lands in conversation history and the
        # model copies it back out as its own answer on the next similar
        # question — that is the whole bug this feature kept hitting
        # ("[FAQ card sent]", then "Got it!"). So store the real answer: if it
        # gets imitated the customer still gets a correct reply as text, just
        # without the card, instead of a meaningless stub.
        if triggered_faq:
            faq = get_faq(triggered_faq)
            await send_faq_card(room_id, [faq] if faq else [], title=triggered_faq)
            clean_reply = faq["answer"] if faq else ""
        else:
            await send_faq_card(room_id, get_faqs())
            clean_reply = "Here are the questions we get asked most — tap any one to see the answer."
    elif triggered_menu:
        await send_menu(room_id)
    elif triggered_payment:
        # Order confirmed — reuse the fulfillment flow's pre-generated order id
        # if there is one (keeps every fulfillment card, the receipt, and the
        # payment intent on the same id), otherwise generate a fresh one same
        # as before. Persist it, send the receipt card, create the Swich
        # payment intent, and schedule the review.
        if sender in pending_orders:
            del pending_orders[sender]
        stable_order_id = (fulfillment or {}).get("order_id") or await generate_unique_order_id()
        print(f"🔑 stable_order_id: {stable_order_id}")
        print(f"🔑 agent order_id: {triggered_payment['order_id']}")
        last_orders[sender] = {**triggered_payment, "order_id": stable_order_id, "room_id": room_id}
        from db import update_order_room_id
        update_order_room_id(triggered_payment['order_id'], room_id)
        from db import update_order_stable_id
        update_order_stable_id(triggered_payment['order_id'], stable_order_id)
        if fulfillment:
            from db import update_order_fulfillment
            update_order_fulfillment(triggered_payment['order_id'], fulfillment["method"], fulfillment.get("summary", ""))

        from config import ONLINE_PAYMENTS_ENABLED
        if not ONLINE_PAYMENTS_ENABLED:
            # JNO-240 — cash receipt. v3 handles a missing fulfillment block, so
            # this branch covers the no-fulfillment case too and v1 is only ever
            # reached when online payments are back on.
            from bot.orders.order_confirmation_service import send_order_confirmation_card_v2
            await send_order_confirmation_card_v2(
                room_id=room_id,
                line_items=triggered_payment.get("line_items", []),
                total=triggered_payment["amount"],
                customer_name=triggered_payment["name"],
                order_id=stable_order_id,
                user_id=sender,
                fulfillment=fulfillment or {},
                version=3,
            )
        elif fulfillment:
            from bot.orders.order_confirmation_service import send_order_confirmation_card_v2
            await send_order_confirmation_card_v2(
                room_id=room_id,
                line_items=triggered_payment.get("line_items", []),
                total=triggered_payment["amount"],
                customer_name=triggered_payment["name"],
                order_id=stable_order_id,
                user_id=sender,
                fulfillment=fulfillment,
            )
        else:
            from bot.orders.order_confirmation_service import send_order_confirmation_card
            await send_order_confirmation_card(
                room_id=room_id,
                line_items=triggered_payment.get("line_items", []),
                total=triggered_payment["amount"],
                customer_name=triggered_payment["name"],
                order_id=stable_order_id,
                user_id=sender,
            )
        # ── Cancel-window countdown (JNO-85) ─────────────────────────────────
        # The 15-minute free-cancellation rule is real and already enforced in
        # SQL, but until now the customer had no way to see the clock — it was
        # only mentioned in an FAQ answer and clause 3 of the terms. Anchored to
        # the order row's own created_at, which is exactly what
        # cancel_last_pending_order() compares against, so the timer can't still
        # show time left after cancelling has stopped working.
        # Sits here rather than in order_flow.py so the LLM fallback path gets
        # it too — both order paths converge on this branch.
        await _send_cancel_countdown(room_id, stable_order_id)

        from bot.payment.payment_service import create_payment_intent
        await create_payment_intent(
            room_id=room_id,
            amount=triggered_payment["amount"],
            customer_name=triggered_payment["name"],
            phone=triggered_payment["phone"],
            order_id=stable_order_id,
            user_id=sender,
        )
        from config import REVIEW_CARD_ENABLED
        if REVIEW_CARD_ENABLED:
            async def _schedule_review():
                try:
                    await schedule_review(room_id=room_id, order_id=stable_order_id, menu_item="Your Order")
                except Exception as e:
                    logger.error(f"schedule_review failed for order {stable_order_id}: {e}", exc_info=True)
            asyncio.create_task(_schedule_review())

    elif triggered_order_history:
        from bot.orders.order_history_service import send_order_history_card
        if reply:
            await send_text(room_id, reply)
        await send_order_history_card(room_id, sender)
    elif triggered_item:
        await send_item_card(room_id, triggered_item)
        if triggered_banner:
            from bot.banner_service import send_banner_card
            await send_banner_card(room_id=room_id, **triggered_banner)
    elif triggered_category:
        await send_category_card(room_id, triggered_category)
    elif triggered_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.polls.poll_service import send_single_choice_poll_to_room
        await send_single_choice_poll_to_room(
            matrix_client, room_id, triggered_poll["question"], triggered_poll["options"]
        )
        # Store the plain question as this turn's history — NOT a bracket-tagged
        # placeholder. A synthetic "[Poll sent: ...]" tag in assistant history gets
        # mimicked verbatim by the model on later turns instead of triggering a
        # real tool call (observed: it started replying with the literal tag text).
        clean_reply = reply or triggered_poll["question"]
    elif triggered_multi_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.polls.flavor_poll_service import send_flavor_preference_poll_to_room
        await send_flavor_preference_poll_to_room(matrix_client, room_id)
        clean_reply = reply or triggered_multi_poll["question"]
    elif triggered_ranking_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.polls.ranking_poll_service import send_ranking_poll_to_room
        await send_ranking_poll_to_room(
            matrix_client, room_id, triggered_ranking_poll["question"], triggered_ranking_poll["options"]
        )
        clean_reply = reply or triggered_ranking_poll["question"]
    elif triggered_open_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.polls.special_instructions_poll_service import send_special_instructions_poll_to_room
        await send_special_instructions_poll_to_room(
            matrix_client, room_id,
            placeholder=triggered_open_poll.get("placeholder", "e.g. extra hot, less sugar"),
            question=triggered_open_poll["question"],
        )
        clean_reply = reply or triggered_open_poll["question"]

    elif triggered_poll_history:
        from bot.polls.poll_history_service import send_poll_history_card
        await send_poll_history_card(matrix_client, room_id, sender)
        clean_reply = ""


    elif triggered_banner:
        from bot.banner_service import send_banner_card
        await send_banner_card(
            room_id=room_id,
            variant=triggered_banner["variant"],
            title=triggered_banner["title"],
            message=triggered_banner["message"],
            meta=triggered_banner["meta"],
        )
        clean_reply = ""
    else:
        await send_text(room_id, reply)

    # ── Rating poll can fire alongside another trigger (e.g. right after payment) ──
    # One event per distinct item, all sharing a chain_id so the client draws them
    # as ONE card that advances item-to-item instead of N stacked cards.
    if triggered_rating_poll:
        from bot.polls.rating_poll_service import send_rating_poll_to_room
        item_names = triggered_rating_poll["item_names"]
        chain_id = f"rate_{last_orders.get(sender, {}).get('order_id') or _uuid.uuid4().hex[:8]}"
        for item_name in item_names:
            await send_rating_poll_to_room(matrix_client, room_id, item_name, chain_id=chain_id)

    return clean_reply
