
import asyncio
import logging
import langsmith as ls
from nio import MatrixRoom, RoomMessageText, UnknownEvent
from bot.matrix_client import matrix_client, BOT_START_TIME, send_text
from bot.menu_service import send_menu, send_item_card , send_category_card
from agent.agent import agent
from agent.prompt import SYSTEM_PROMPT
from agent.context import Context
from bot.review_scheduler import schedule_review
logger = logging.getLogger(__name__)
from bot.payment_service import send_payment_card
import re as _re_pay 
import re as _re
from db import get_pending_payment_by_user, save_order, save_reservation,  get_menu_items, get_payment, update_payment_status, save_review
from db import update_order_room_id
conversation_histories: dict[str, list] = {}
processed_event_ids: set[str] = set()
last_orders: dict[str, dict] = {}
order_counters: dict[str, int] = {}
poll_response_timers: dict[str, asyncio.TimerHandle] = {}


import secrets
import string

def generate_order_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(alphabet) for _ in range(6))
    return f"ORD-{suffix}"

async def generate_unique_order_id() -> str:
    from db import order_id_exists
    for _ in range(5):
        candidate = generate_order_id()
        if not await order_id_exists(candidate):
            return candidate
    return generate_order_id() + secrets.choice(string.ascii_uppercase)


def _is_empty_agent_response(result) -> bool:
    """True if the model returned neither text nor a tool call — a blank generation."""
    if not isinstance(result, dict) or not result.get("messages"):
        return True
    last = result["messages"][-1]
    content = last.content if hasattr(last, "content") else str(last)
    if isinstance(content, list):
        text = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    else:
        text = (content or "").strip()
    tool_calls = getattr(last, "tool_calls", None)
    return not text and not tool_calls


async def _invoke_agent_with_retry(messages, sender: str, retries: int = 2):
    """Gemini occasionally returns a blank generation (no text, no tool call) with
    no error raised — retry before falling back to the apology text."""
    result = None
    for attempt in range(retries + 1):
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": messages}),
            timeout=30.0,
        )
        if not _is_empty_agent_response(result):
            return result
        last = result["messages"][-1] if isinstance(result, dict) and result.get("messages") else None
        finish_reason = getattr(last, "response_metadata", {}).get("finish_reason") if last is not None else None
        logger.warning(
            f"⚠️ Empty agent response for {sender} (attempt {attempt + 1}/{retries + 1}), "
            f"finish_reason={finish_reason}, retrying"
        )
        if attempt < retries:
            await asyncio.sleep(0.8)
    return result


async def _dispatch_agent_result(result, room_id: str, sender: str) -> str:
    """Detect trigger markers in an agent result, dispatch the matching card/flow,
    and return the cleaned reply text for conversation-history bookkeeping."""
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

    triggered_menu = False
    triggered_payment = None
    triggered_order_history = False
    triggered_item = None
    triggered_category = None
    triggered_poll = None
    triggered_multi_poll = None
    triggered_banner = None
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
        return f"[Banner sent: {triggered_banner['title']}]"
    if any("ORDER_HISTORY_TRIGGERED" in c for c in all_content):
        triggered_order_history = True
    if any("MENU_TRIGGERED" in c for c in all_content):
        triggered_menu = True
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "ITEM_TRIGGERED" in msg_content:
                item_match = _re.search(r'ITEM_TRIGGERED\|(.+)', msg_content)
                if item_match:
                    triggered_item = item_match.group(1).strip()
                break
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "CATEGORY_TRIGGERED" in msg_content:
                cat_match = _re.search(r'CATEGORY_TRIGGERED\|(.+)', msg_content)
                if cat_match:
                    triggered_category = cat_match.group(1).strip()
                break
    if isinstance(result, dict) and "messages" in result:
        for msg in result["messages"]:
            msg_content = msg.content if hasattr(msg, "content") else ""
            if isinstance(msg_content, str) and "POLL_TRIGGERED" in msg_content and "MULTI_POLL_TRIGGERED" not in msg_content:
                poll_match = _re.search(r'POLL_TRIGGERED\|(.+?)\|(.+)', msg_content)
                if poll_match:
                    question = poll_match.group(1).strip()
                    options = [o.strip() for o in poll_match.group(2).split(",") if o.strip()]
                    triggered_poll = {"question": question, "options": options}
                break
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
    if any("ORDER_HISTORY_CARD" in c for c in all_content):
        triggered_order_history = True

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

    reply = reply.replace("MENU_TRIGGERED", "").replace("MENU_CARD", "").strip()
    reply = _re.sub(r'ITEM_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'CATEGORY_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = reply.replace("CATEGORY_CARD", "").strip()
    reply = _re.sub(r'PAYMENT_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = reply.replace("ORDER_HISTORY_TRIGGERED", "").strip()
    reply = _re.sub(r'POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'MULTI_POLL_TRIGGERED\|[^\n]+', '', reply).strip()
    reply = _re.sub(r'BANNER_TRIGGERED\|[^\n]+', '', reply).strip()
    if "ORDER_HISTORY_CARD" in reply:
        triggered_order_history = True
    reply = reply.replace("ORDER_HISTORY_CARD", "").strip()

    if triggered_menu:
        reply = ""
    if triggered_banner:
        reply = ""

    if not reply and not triggered_menu and not triggered_payment and not triggered_order_history and not triggered_item and not triggered_category and not triggered_poll and not triggered_multi_poll and not triggered_banner:

        reply = "I'm sorry, I didn't quite get that. Could you please repeat?"

    print(f"🤖 REPLY: {reply}")
    print(f"🍽️ TRIGGERED MENU: {triggered_menu}")
    print(f"💳 TRIGGERED PAYMENT: {triggered_payment}")
    print(f"📜 TRIGGERED ORDER HISTORY: {triggered_order_history}")

    clean_reply = reply.replace("ORDER_HISTORY_TRIGGERED", "").replace("ORDER_HISTORY_CARD", "").replace("MENU_TRIGGERED", "").replace("MENU_CARD", "").strip()

    await matrix_client.room_typing(room_id, typing_state=False)

    if triggered_menu:
        await send_menu(room_id)
    elif triggered_payment:
        stable_order_id = await generate_unique_order_id()
        print(f"🔑 stable_order_id: {stable_order_id}")
        print(f"🔑 agent order_id: {triggered_payment['order_id']}")
        last_orders[sender] = {**triggered_payment, "order_id": stable_order_id, "room_id": room_id}
        from db import update_order_room_id
        update_order_room_id(triggered_payment['order_id'], room_id)
        from db import update_order_stable_id
        update_order_stable_id(triggered_payment['order_id'], stable_order_id)
        from bot.order_confirmation_service import send_order_confirmation_card
        await send_order_confirmation_card(
            room_id=room_id,
            line_items=triggered_payment.get("line_items", []),
            total=triggered_payment["amount"],
            customer_name=triggered_payment["name"],
            order_id=stable_order_id,
            user_id=sender,
        )
        from bot.payment_service import create_payment_intent
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
        from bot.order_history_service import send_order_history_card
        if reply:
            await send_text(room_id, reply)
        await send_order_history_card(room_id)
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
        from bot.poll_service import send_single_choice_poll_to_room
        await send_single_choice_poll_to_room(
            matrix_client, room_id, triggered_poll["question"], triggered_poll["options"]
        )
        clean_reply = reply or f"[Poll sent: {triggered_poll['question']}]"
    elif triggered_multi_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.flavor_poll_service import send_flavor_preference_poll_to_room
        await send_flavor_preference_poll_to_room(matrix_client, room_id)
        clean_reply = reply or f"[Multi-select poll sent: {triggered_multi_poll['question']}]"
    elif triggered_banner:
        from bot.banner_service import send_banner_card
        await send_banner_card(
            room_id=room_id,
            variant=triggered_banner["variant"],
            title=triggered_banner["title"],
            message=triggered_banner["message"],
            meta=triggered_banner["meta"],
        )
        clean_reply = f"[Banner sent: {triggered_banner['title']}]"
    else:
        await send_text(room_id, reply)

    return clean_reply


async def handle_message(room: MatrixRoom, event: RoomMessageText):
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
        dsl_type = dsl.get('type')

        if dsl_type == 'payment_success':
            data = dsl.get('data', {})
            order_id = data.get('order_id', '').strip()
            print(f"💳 PAYMENT_SUCCESS received: order_id={order_id} sender={sender}")

            payment = await get_payment(sender=sender, order_id=order_id)

            if not payment:
                await send_text(room_id, "❌ Order not found.")
                return
            if payment['sender'] != sender:
                await send_text(room_id, "❌ Unauthorized.")
                return
            if payment['status'] == 'cancelled':
                await send_text(room_id, "❌ This order was cancelled.")
                return
            if payment['status'] == 'paid':
                print(f"⏭️ payment_success already processed for {order_id}, skipping")
                return

            await update_payment_status(order_id=order_id, sender=sender, status='paid')
            if sender in last_orders:
                del last_orders[sender]
            await send_text(room_id, "✅ Payment confirmed! Thank you. Enjoy your meal 🍽️")
            return

        if dsl_type == 'review_submit':
            data = dsl.get('data', {})
            await save_review(
                user_id=sender,
                room_id=room_id,
                menu_item=data.get('menu_item', ''),
                order_id=data.get('order_id', ''),
                rating=int(data.get('rating', 0)),
                comment=data.get('comment', ''),
            )
            await send_text(room_id, '⭐ Thanks for your review!')
            return

        if dsl_type == 'order_summary':
            data = dsl.get('data', {})
            items = data.get('items', [])
            items_str = ", ".join(f"{i.get('name','')} x{i.get('qty',1)}" for i in items)
            message = f"I want to order: {items_str}"
            # NOT returning here — falls through to the agent below

        else:
            logger.info(f"⏭️ Skipping DSL event type={dsl_type} from {sender}")
            return

    # ── Now safe to check body ────────────────────────────────────────────────
    if message is None:
        message = event.body.strip()
    print(f"📨 MESSAGE RECEIVED: '{message}' from {sender}")
    if not message:
        return

    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)

    try:
        # ── Cancel order ──────────────────────────────────────────────────────
        if 'cancel' in message.lower().strip():
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

        if 'menu' in message.lower():
            await matrix_client.room_typing(room_id, typing_state=False)
            await send_menu(room_id)
            return
        
        _ORDER_HISTORY_PATTERN = _re.compile(
            r'\b(order history|my orders|show my orders|show my order history|previous orders|past orders|my order history|what did i order)\b',
            flags=_re.IGNORECASE,
        )
        if _ORDER_HISTORY_PATTERN.search(message.lower().strip()):
            await matrix_client.room_typing(room_id, typing_state=False)
            from bot.order_history_service import send_order_history_card
            await send_order_history_card(room_id)
            return

        _PAY_INTENT_PATTERN = _re_pay.compile(
                r'\b(pay|payment|pay now|make payment|i want to pay|let me pay|can i pay|how do i pay|pay for (it|this|my order))\b',
                flags=_re_pay.IGNORECASE
        )
        if _PAY_INTENT_PATTERN.search(message.lower().strip()):
            await matrix_client.room_typing(room_id, typing_state=False)
            pending = await get_pending_payment_by_user(sender=sender, room_id=room_id)
            if pending:
                from bot.payment_service import send_existing_payment_card
                await send_existing_payment_card(room_id=room_id, pending=pending)
            else:
                await send_text(room_id, "💳 Please place an order first before paying!")
            return    




        # ── AI agent ──────────────────────────────────────────────────────────
        print(f"📨 [{room_id}] {sender}: {message}")

        if sender not in conversation_histories:
            conversation_histories[sender] = []

        # ── Inject pending order context into agent ───────────────────────────
        user_content = f"[user_id: {sender}]\n{message}"

        messages = []
        for msg in conversation_histories[sender][-6:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_content})

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

        conversation_histories[sender].append({"role": "user", "content": message})
        conversation_histories[sender].append({"role": "assistant", "content": clean_reply if clean_reply else "Got it!"})

        if len(conversation_histories[sender]) > 20:
            conversation_histories[sender] = conversation_histories[sender][-20:]

    except Exception as e:
        await matrix_client.room_typing(room_id, typing_state=False)
        from bot.banner_service import send_banner_card
        await send_banner_card(
            room_id=room_id,
            variant="outage",
            title="Something Went Wrong",
            message="We ran into a small issue on our end — please try again in a moment.",
        )
        logger.error(f"handle_message error: {e}", exc_info=True)


async def handle_custom_event(room: MatrixRoom, event: UnknownEvent):
    if event.server_timestamp < BOT_START_TIME:
        return
    if event.sender == matrix_client.user_id:
        return
    if event.event_id in processed_event_ids:
        return
    processed_event_ids.add(event.event_id)

    if event.type == 'ai.jaeno.review_submit':
        content = event.source.get('content', {})
        await save_review(
            user_id=event.sender,
            room_id=room.room_id,
            menu_item=content.get('menu_item', ''),
            order_id=content.get('order_id', ''),
            rating=int(content.get('rating', 0)),
            comment=content.get('comment', ''),
        )
        await send_text(room.room_id, '⭐ Thanks for your review!')
        return

    # ── Poll response handler ─────────────────────────────────────────────────
    if event.type == 'org.matrix.msc3381.poll.response':
        content = event.source.get('content', {})
        relates_to = content.get('m.relates_to', {})
        poll_event_id = relates_to.get('event_id')
        response_data = content.get('org.matrix.msc3381.poll.response', {})
        answer_ids = response_data.get('answers', [])

        if not poll_event_id or not answer_ids:
            return

        from db import get_poll_by_event_id
        poll = get_poll_by_event_id(poll_event_id)
        if not poll:
            return

        selected_options = []
        for aid in answer_ids:
            try:
                idx = int(aid.split("-")[1]) - 1
                selected_options.append(poll["options"][idx])
            except (IndexError, ValueError):
                continue

        if not selected_options:
            return

        selected_text = ", ".join(selected_options)
        sender = event.sender
        room_id = room.room_id

        print(f"🗳️ Poll response: {sender} answered '{poll['question']}' → {selected_text}")

        if sender not in conversation_histories:
            conversation_histories[sender] = []
        conversation_histories[sender] = [
            m for m in conversation_histories[sender]
            if not m["content"].startswith(f"[Poll answer to \"{poll['question']}\"]")
        ]
        conversation_histories[sender].append({
            "role": "user",
            "content": f"[Poll answer to \"{poll['question']}\"]: {selected_text}"
        })

        async def _run_agent_for_poll_answer():
            messages = []
            for msg in conversation_histories[sender][-6:]:
                messages.append({"role": msg["role"], "content": msg["content"]})

            await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
            try:
                result = await _invoke_agent_with_retry(messages, sender)
                clean_reply = await _dispatch_agent_result(result, room_id, sender)
                conversation_histories[sender].append({
                    "role": "assistant",
                    "content": clean_reply or "Got it!"
                })
            except asyncio.TimeoutError:
                await matrix_client.room_typing(room_id, typing_state=False)
                await send_text(room_id, "Sorry, that took too long! Please try again 🙏")

        if not poll.get("multi_select"):
            # Single-choice poll — reply immediately, no debounce.
            if sender in poll_response_timers:
                poll_response_timers[sender].cancel()
                poll_response_timers.pop(sender, None)
            await _run_agent_for_poll_answer()
            return

        # Multi-select poll — debounce so the agent only replies once the
        # customer stops tapping, instead of after every single selection.
        if sender in poll_response_timers:
            poll_response_timers[sender].cancel()

        async def _handle_after_debounce():
            poll_response_timers.pop(sender, None)
            await _run_agent_for_poll_answer()

        loop = asyncio.get_event_loop()
        poll_response_timers[sender] = loop.call_later(
            2.0, lambda: asyncio.create_task(_handle_after_debounce())
        )
        return
