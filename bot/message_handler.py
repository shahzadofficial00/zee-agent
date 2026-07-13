
# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
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


import secrets
import string

# ─────────────────────────────────────────────────────────────────────────────
# ORDER ID GENERATION — human-readable stable_order_id (ORD-XXXXXX)
# ─────────────────────────────────────────────────────────────────────────────
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


def generate_tip_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(alphabet) for _ in range(6))
    return f"TIP-{suffix}"

async def generate_unique_tip_id() -> str:
    from db import order_id_exists
    for _ in range(5):
        candidate = generate_tip_id()
        if not await order_id_exists(candidate):
            return candidate
    return generate_tip_id() + secrets.choice(string.ascii_uppercase)


# ─────────────────────────────────────────────────────────────────────────────
# DETERMINISTIC ORDER FLOW — per-item size + special-instructions loop
# ─────────────────────────────────────────────────────────────────────────────
_QTY_WORD_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1,
}


def _parse_distinct_items(items_str: str) -> list[tuple[str, int]]:
    """Parse 'Espresso x1, Latte x1, Americano x1' into distinct (base_name, qty)
    pairs in first-seen order, summing quantities for repeated items."""
    items_str = _re.sub(r'\s+and\s+', ', ', items_str, flags=_re.IGNORECASE)
    order: list[str] = []
    qtys: dict[str, int] = {}
    for raw in items_str.split(","):
        raw = raw.strip()
        if not raw:
            continue
        match = _re.match(r'^(.+?)\s+[xX]\s*(\d+)$', raw)
        if match:
            name, qty = match.group(1).strip(), int(match.group(2))
        else:
            qty = 1
            name = raw
            parts = raw.split(None, 1)
            if len(parts) == 2:
                first = parts[0].lower()
                if first in _QTY_WORD_MAP:
                    qty = _QTY_WORD_MAP[first]
                    name = parts[1].strip()
                elif first.isdigit():
                    qty = int(first)
                    name = parts[1].strip()
        if not name:
            continue
        if name not in qtys:
            order.append(name)
            qtys[name] = 0
        qtys[name] += qty
    return [(name, qtys[name]) for name in order]


async def _start_order_flow(sender: str, room_id: str, message: str) -> bool:
    """Kick off the deterministic size/instructions loop for a fresh order.
    Returns False (and starts nothing) if the item list couldn't be parsed,
    so the caller falls back to the normal agent-driven path."""
    items_str = message.split(":", 1)[1].strip() if ":" in message else message
    parsed = _parse_distinct_items(items_str)
    if not parsed:
        return False

    order_flows[sender] = {
        "distinct_items": [name for name, _ in parsed],
        "qtys": {name: qty for name, qty in parsed},
        "stage": "size",
        "index": 0,
        "sizes": {},
        "instructions": {},
    }

    item_list_text = ", ".join(f"{name} x{qty}" for name, qty in parsed)
    await send_text(room_id, f"Got it — {item_list_text}! Let's get the details sorted.")
    await _send_next_order_flow_poll(sender, room_id)
    return True


async def _send_next_order_flow_poll(sender: str, room_id: str) -> None:
    state = order_flows.get(sender)
    if not state:
        return
    item = state["distinct_items"][state["index"]]

    if state["stage"] == "size":
        from bot.poll_service import send_single_choice_poll_to_room
        await send_single_choice_poll_to_room(
            matrix_client, room_id, f"What size would you like for {item}?", ["Small", "Medium", "Large"]
        )
    elif state["stage"] == "instructions":
        from bot.special_instructions_poll_service import send_special_instructions_poll_to_room
        await send_special_instructions_poll_to_room(
            matrix_client, room_id,
            placeholder="e.g. extra hot, less sugar, no ice",
            question=f"Any special instructions for your {item}?",
        )


async def _send_final_confirm_poll(sender: str, room_id: str) -> None:
    from bot.poll_service import send_single_choice_poll_to_room
    await send_single_choice_poll_to_room(
        matrix_client, room_id, "Confirm your order — Shall I proceed?", ["Yes", "No"]
    )


async def _start_customer_info_stage(sender: str, room_id: str) -> None:
    """Size + instructions are fully collected — deterministically ask whether to
    reuse the saved name/phone (or collect fresh ones), instead of handing this
    judgment call to the LLM. Also snapshots the order into last_order_line /
    last_order_state for a reliable "same again?" reorder later."""
    state = order_flows.get(sender)
    if not state:
        return

    line_parts = []
    for item in state["distinct_items"]:
        qty = state["qtys"].get(item, 1)
        size = state["sizes"].get(item, "Medium")
        instructions = state["instructions"].get(item, "")
        suffix = f"{size} - {instructions}" if instructions else size
        line_parts.append(f"{item} ({suffix}) x{qty}")
    last_order_line[sender] = ", ".join(line_parts)
    last_order_state[sender] = {
        "distinct_items": list(state["distinct_items"]),
        "qtys": dict(state["qtys"]),
        "sizes": dict(state["sizes"]),
        "instructions": dict(state["instructions"]),
    }

    from db import get_customer
    customer = get_customer(sender)

    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
    if customer:
        state["stage"] = "customer_confirm"
        state["candidate_name"] = customer["name"]
        state["candidate_phone"] = customer["phone"]
        from bot.poll_service import send_single_choice_poll_to_room
        await send_single_choice_poll_to_room(
            matrix_client, room_id,
            f"Should I use {customer['name']}, {customer['phone']} as before?",
            ["Yes", "No"],
        )
    else:
        state["stage"] = "await_name"
        await send_text(room_id, "Could I get your full name for the order?")
    await matrix_client.room_typing(room_id, typing_state=False)


async def _place_deterministic_order(sender: str, room_id: str) -> None:
    """Final Yes received — call confirm_order directly (no LLM in the loop) and
    dispatch the resulting signal string through the normal card-sending path."""
    state = order_flows.pop(sender, None)
    if not state:
        return

    line_parts = []
    for item in state["distinct_items"]:
        qty = state["qtys"].get(item, 1)
        size = state["sizes"].get(item, "Medium")
        instructions = state["instructions"].get(item, "")
        suffix = f"{size} - {instructions}" if instructions else size
        line_parts.append(f"{item} ({suffix}) x{qty}")
    items_str = ", ".join(line_parts)

    from agent.tools.confirm_order import confirm_order
    result_text = confirm_order.invoke({
        "items": items_str,
        "customer_name": state["customer_name"],
        "phone": state["customer_phone"],
        "sender": sender,
    })
    if "ORDER_SAVED" in result_text:
        distinct_items = ", ".join(state["distinct_items"])
        result_text += f"\nRATING_POLL_TRIGGERED|{distinct_items}"

    class _FakeMessage:
        def __init__(self, content):
            self.content = content

    fake_result = {"messages": [_FakeMessage(result_text)]}

    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
    clean_reply = await _dispatch_agent_result(fake_result, room_id, sender)
    if sender not in conversation_histories:
        conversation_histories[sender] = []
    conversation_histories[sender].append({
        "role": "assistant",
        "content": clean_reply or "Got it!"
    })

    if "ORDER_SAVED" in result_text:
        from bot.tip_service import send_tip_request_to_room
        await send_tip_request_to_room(matrix_client, room_id)


async def _handle_order_flow_poll_answer(sender: str, room_id: str, question: str, answer: str) -> bool:
    """Try to route a poll answer into the active deterministic order flow.
    Returns True if it was consumed here (caller should not also invoke the agent)."""
    state = order_flows.get(sender)
    if not state:
        return False
    stage = state["stage"]

    if stage in ("size", "instructions"):
        index = state["index"]
        if index >= len(state["distinct_items"]):
            return False
        item = state["distinct_items"][index]

        if stage == "size":
            if question != f"What size would you like for {item}?":
                return False
            state["sizes"][item] = answer.strip() or "Medium"
        else:
            if question != f"Any special instructions for your {item}?":
                return False
            ans = answer.strip()
            state["instructions"][item] = "" if ans.lower() in ("none", "no", "no instructions", "") else ans

        state["index"] += 1
        await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)

        if state["index"] < len(state["distinct_items"]):
            await _send_next_order_flow_poll(sender, room_id)
            await matrix_client.room_typing(room_id, typing_state=False)
            return True

        if stage == "size":
            state["stage"] = "instructions"
            state["index"] = 0
            await _send_next_order_flow_poll(sender, room_id)
            await matrix_client.room_typing(room_id, typing_state=False)
            return True

        await _start_customer_info_stage(sender, room_id)
        return True

    if stage == "customer_confirm":
        expected_q = f"Should I use {state['candidate_name']}, {state['candidate_phone']} as before?"
        if question != expected_q:
            return False
        if answer.strip().lower() == "yes":
            state["customer_name"] = state["candidate_name"]
            state["customer_phone"] = state["candidate_phone"]
            state["stage"] = "final_confirm"
            await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
            await _send_final_confirm_poll(sender, room_id)
            await matrix_client.room_typing(room_id, typing_state=False)
        else:
            state["stage"] = "await_name"
            await send_text(room_id, "No problem — what's your full name?")
        return True

    if stage == "final_confirm":
        if question != "Confirm your order — Shall I proceed?":
            return False
        if answer.strip().lower() == "yes":
            await _place_deterministic_order(sender, room_id)
        else:
            order_flows.pop(sender, None)
            await _handle_declined_confirmation(sender, room_id, question, answer)
        return True

    return False


async def _handle_declined_confirmation(sender: str, room_id: str, question: str, answer: str) -> bool:
    """If the customer just said "No" to the final order confirmation poll,
    handle the immediate follow-up deterministically instead of leaving "what
    changes would you like" to the LLM — that path was reconstructing the item
    list from conversation history, which drifts/hallucinates in long chats.
    Returns True if handled here (caller should not also route to the agent)."""
    if question != "Confirm your order — Shall I proceed?" or answer.strip().lower() != "no":
        return False
    if sender not in last_order_line:
        return False
    await send_text(
        room_id,
        "No worries! Reply \"yes\" to place the exact same order again, or tell me what you'd like instead."
    )
    awaiting_reorder_confirmation.add(sender)
    return True


async def _handle_reorder_affirmation(sender: str, room_id: str, message: str) -> bool:
    """Called from handle_message for senders awaiting a reorder decision.
    Returns True if the message was consumed here."""
    if sender not in awaiting_reorder_confirmation:
        return False
    awaiting_reorder_confirmation.discard(sender)

    if message.strip().lower() not in _REORDER_AFFIRMATIONS:
        # Not an affirmation — let it fall through to normal handling (a fresh
        # order attempt, or free-form chat).
        return False

    saved_state = last_order_state.get(sender)
    if not saved_state:
        return False

    # Rebuild order_flows straight at the customer-info stage — size and
    # instructions are already known, so re-derive nothing via the LLM.
    order_flows[sender] = {
        "distinct_items": list(saved_state["distinct_items"]),
        "qtys": dict(saved_state["qtys"]),
        "sizes": dict(saved_state["sizes"]),
        "instructions": dict(saved_state["instructions"]),
        "stage": "size",
        "index": len(saved_state["distinct_items"]),
    }
    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
    await _start_customer_info_stage(sender, room_id)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# AGENT INVOCATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _is_empty_agent_response(result) -> bool:
    """True if the model returned neither text nor a tool call — a blank generation.
    A blank final message is NOT considered empty if a tool already ran this turn
    (e.g. a card-only poll turn that intentionally ends with no text reply)."""
    if not isinstance(result, dict) or not result.get("messages"):
        return True
    messages = result["messages"]
    last = messages[-1]
    content = last.content if hasattr(last, "content") else str(last)
    if isinstance(content, list):
        text = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    else:
        text = (content or "").strip()
    tool_calls = getattr(last, "tool_calls", None)
    if text or tool_calls:
        return False
    prev = messages[-2] if len(messages) >= 2 else None
    if prev is not None and getattr(prev, "type", None) == "tool":
        return False
    return True


async def _invoke_agent_with_retry(messages, sender: str, retries: int = 4):
    """Gemini occasionally returns a blank generation (no text, no tool call) with
    no error raised — retry before falling back to the apology text."""
    result = None
    for attempt in range(retries + 1):
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": messages}, context=Context(user_id=sender)),
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


# ─────────────────────────────────────────────────────────────────────────────
# AGENT RESULT DISPATCH — parse signal strings out of the agent's messages,
# send the matching DSL card/flow, and return cleaned reply text for history.
# ─────────────────────────────────────────────────────────────────────────────
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
        return f"[Banner sent: {triggered_banner['title']}]"

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
    if triggered_poll or triggered_multi_poll or triggered_ranking_poll:
        # The poll card already renders the question — never also send it as text.
        reply = ""

    # ── Nothing triggered and no text — fall back to a generic apology ────────
    if not reply and not triggered_menu and not triggered_payment and not triggered_order_history and not triggered_item and not triggered_category and not triggered_poll and not triggered_multi_poll and not triggered_ranking_poll and not triggered_banner and not triggered_open_poll and not triggered_rating_poll and not triggered_poll_history:

        reply = "I'm sorry, I didn't quite get that. Could you please repeat?"

    print(f"🤖 REPLY: {reply}")
    print(f"🍽️ TRIGGERED MENU: {triggered_menu}")
    print(f"💳 TRIGGERED PAYMENT: {triggered_payment}")
    print(f"📜 TRIGGERED ORDER HISTORY: {triggered_order_history}")

    clean_reply = reply.replace("ORDER_HISTORY_TRIGGERED", "").replace("ORDER_HISTORY_CARD", "").replace("MENU_TRIGGERED", "").replace("MENU_CARD", "").strip()

    await matrix_client.room_typing(room_id, typing_state=False)

    # ── Dispatch: send the one card/flow that matches whichever trigger fired ──
    if triggered_menu:
        await send_menu(room_id)
    elif triggered_payment:
        # Order confirmed — generate a stable order id, persist it, send the
        # receipt card, create the Swich payment intent, and schedule the review.
        if sender in pending_orders:
            del pending_orders[sender]
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
        from bot.poll_service import send_single_choice_poll_to_room
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
        from bot.flavor_poll_service import send_flavor_preference_poll_to_room
        await send_flavor_preference_poll_to_room(matrix_client, room_id)
        clean_reply = reply or triggered_multi_poll["question"]
    elif triggered_ranking_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.ranking_poll_service import send_ranking_poll_to_room
        await send_ranking_poll_to_room(
            matrix_client, room_id, triggered_ranking_poll["question"], triggered_ranking_poll["options"]
        )
        clean_reply = reply or triggered_ranking_poll["question"]
    elif triggered_open_poll:
        if reply:
            await send_text(room_id, reply)
        from bot.special_instructions_poll_service import send_special_instructions_poll_to_room
        await send_special_instructions_poll_to_room(
            matrix_client, room_id,
            placeholder=triggered_open_poll.get("placeholder", "e.g. extra hot, less sugar"),
            question=triggered_open_poll["question"],
        )
        clean_reply = reply or triggered_open_poll["question"]

    elif triggered_poll_history:
        from bot.poll_history_service import send_poll_history_card
        await send_poll_history_card(matrix_client, room_id, sender)
        clean_reply = "[Poll history card sent]"


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

    # ── Rating poll can fire alongside another trigger (e.g. right after payment) ──
    # One card per distinct item ordered, sent one after another.
    if triggered_rating_poll:
        from bot.rating_poll_service import send_rating_poll_to_room
        item_names = triggered_rating_poll["item_names"]
        for item_name in item_names:
            await send_rating_poll_to_room(matrix_client, room_id, item_name)
        if not clean_reply:
            clean_reply = f"[Rating poll(s) sent for: {', '.join(item_names)}]"

    return clean_reply


# ─────────────────────────────────────────────────────────────────────────────
# MAIN MESSAGE HANDLER — routes every incoming RoomMessageText event
# ─────────────────────────────────────────────────────────────────────────────
async def handle_message(room: MatrixRoom, event: RoomMessageText):
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
        dsl_type = dsl.get('type')

        # ── payment_success — dead code path, kept for reference only ────────
        # (Flutter's event isn't nested under ai.jaeno.dsl, so this never actually fires;
        # real payment confirmation happens server-to-server via the swich-callback Edge Function.)
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

        # ── review_submit — customer submitted a star rating + comment ───────
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

        # ── tip_selected — customer picked a preset/custom tip amount ────────
        # Deterministic, no LLM: create a payment intent for the tip amount
        # (same Swich flow as an order) and send the payment card.
        if dsl_type == 'tip_selected':
            data = dsl.get('data', {})
            try:
                amount = int(float(data.get('amount', 0)))
            except (TypeError, ValueError):
                amount = 0
            if amount < 10:
                await send_text(room_id, "That tip amount is a bit too small to process — please pick a larger amount 🙏")
                return

            from db import get_customer
            customer = get_customer(sender)
            if not customer:
                await send_text(room_id, "We couldn't find your details to process the tip — please place an order first 🙏")
                return

            tip_order_id = await generate_unique_tip_id()
            from bot.payment_service import send_payment_card
            await send_payment_card(
                room_id=room_id,
                amount=amount,
                customer_name=customer["name"],
                phone=customer["phone"],
                order_id=tip_order_id,
                user_id=sender,
            )
            return

        # ── tip_declined — customer skipped the tip, just acknowledge ────────
        if dsl_type == 'tip_declined':
            await send_text(room_id, "No worries at all — thanks for stopping by! 🙏")
            return

        # ── order_summary — customer built an order via the menu card UI ─────
        if dsl_type == 'order_summary':
            data = dsl.get('data', {})
            items = data.get('items', [])
            items_str = ", ".join(f"{i.get('name','')} x{i.get('qty',1)}" for i in items)
            message = f"I want to order: {items_str}"
            # NOT returning here — falls through to the agent below

        # ── bid_confirmation — customer placed/updated a bid on a live auction ──
        # Deterministic, no LLM: re-validate server-side (the client only checks
        # the amount locally) and record it as the customer's current bid.
        elif dsl_type == 'bid_confirmation':
            from db import place_bid_if_higher
            data = dsl.get('data', {})
            auction_id = data.get('auction_id', '').strip()
            try:
                amount = float(data.get('amount', 0))
            except (TypeError, ValueError):
                amount = 0

            result = place_bid_if_higher(auction_id, sender, room_id, amount)
            if not result['accepted']:
                await send_text(room_id, f"❌ {result['reason']}")
            else:
                logger.info(f"🔨 Bid accepted | {auction_id} | {sender} | Rs {amount}")
            return

        # ── poll — the bot's own poll card being echoed back to the room ─────
        elif dsl_type == 'poll':
            # Bot's own poll card being echoed back — skip
            logger.info(f"⏭️ Skipping bot's own poll DSL from {sender}")
            return

        # ── any other DSL type is not handled here — ignore ──────────────────
        else:
            logger.info(f"⏭️ Skipping DSL event type={dsl_type} from {sender}")
            return

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
            flow_state["stage"] = "final_confirm"
            await matrix_client.room_typing(room_id, typing_state=False)
            await _send_final_confirm_poll(sender, room_id)
        return

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
        if 'cancel' in message.lower().strip():
            if sender in pending_orders:
                del pending_orders[sender]
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
            from bot.order_history_service import send_order_history_card
            await send_order_history_card(room_id, sender)
            return

        # ── Fast-path: payment intent request bypasses the agent entirely ────
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
        # Add context hint for very short messages so Gemini doesn't generate empty
        if len(message.strip().split()) <= 2:
           user_content = f"{message}\n[Please continue the conversation naturally based on context above]"
        else:
            user_content = message

        messages = []
        for msg in conversation_histories[sender][-12:]:
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

        if len(conversation_histories[sender]) > 20:
            conversation_histories[sender] = conversation_histories[sender][-20:]

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


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM EVENT HANDLER — routes non-text Matrix events (poll responses, reviews)
# ─────────────────────────────────────────────────────────────────────────────
async def handle_custom_event(room: MatrixRoom, event: UnknownEvent):
    # ── Ignore events from before bot start, from itself, or already processed ──
    if event.server_timestamp < BOT_START_TIME:
        return
    if event.sender == matrix_client.user_id:
        return
    if event.event_id in processed_event_ids:
        return
    processed_event_ids.add(event.event_id)


    # ── ai.jaeno.poll_response — answer to any custom DSL poll (size, flavor, ──
    # confirm Yes/No, special instructions, rating) ───────────────────────────
    if event.type == 'ai.jaeno.poll_response':
        content = event.source.get('content', {})
        poll_id = content.get('poll_id', '')
        question = content.get('question', '')
        selected = content.get('selected', '')
        selected_text = content.get('selected_text', '')
        sender = event.sender
        room_id = room.room_id

        print(f"🗳️ DSL Poll response: {sender} answered '{question}' → {selected_text}")

        # ── Persist every DSL poll answer to poll_answers, resolving the real ──
        # poll_type via poll_id so poll history shows the correct icon/type ────
        from db import get_poll_by_poll_id, save_poll_answer
        poll_record = get_poll_by_poll_id(poll_id) if poll_id else None
        poll_type = poll_record["poll_type"] if poll_record else (
            "rating" if question.startswith("How would you rate your ") else "single_choice"
        )
        try:
            save_poll_answer(
                room_id=room_id,
                sender=sender,
                poll_event_id=poll_id or event.event_id,
                question=question,
                answer=selected_text,
                poll_type=poll_type,
            )
        except Exception as e:
            logger.error(f"❌ Failed to save DSL poll answer: {e}", exc_info=True)

        # ── Deterministic order flow — size/instructions loop, no LLM needed ──────
        if await _handle_order_flow_poll_answer(sender, room_id, question, selected_text):
            return

        # ── Declined confirmation — offer a deterministic "same again?" recovery ──
        if await _handle_declined_confirmation(sender, room_id, question, selected_text):
            return

        # ── Rating polls are handled separately: save to item_ratings and thank ──
        # the customer directly, without routing back through the agent ─────────
        if question.startswith("How would you rate your "):
            item_name = question.replace("How would you rate your ", "").rstrip("?")
            rating_value = int(selected_text) if selected_text.isdigit() else 0
            if 1 <= rating_value <= 5:
                from db import save_item_rating
                saved = await save_item_rating(
                    room_id=room_id,
                    sender=sender,
                    menu_item=item_name,
                    rating=rating_value,
                )
                if saved:
                    print(f"⭐ Item rating saved: {item_name} = {rating_value} stars")
                    await send_text(room_id, "Thank you for your rating! ⭐")
                    from db import get_item_rating_summary
                    summary = get_item_rating_summary(item_name)
                    if summary:
                        from bot.poll_results_service import send_item_rating_results_to_room
                        await send_item_rating_results_to_room(
                            matrix_client, room_id, item_name,
                            summary["average"], summary["total_votes"],
                        )
                else:
                    logger.error(f"❌ Rating not saved for {item_name} ({rating_value} stars), not thanking customer")
                    await send_text(room_id, "Sorry, we couldn't save your rating right now. Please try again later 🙏")
            else:
                logger.error(f"❌ Invalid rating value received: {selected_text!r} for '{question}'")
                await send_text(room_id, "Sorry, that didn't look like a valid rating 🙏")
            return

        # ── Every other DSL poll answer — feed it back into the agent as the ──
        # next turn in the conversation, replacing any stale answer to the same question ──
        if sender not in conversation_histories:
            conversation_histories[sender] = []
        conversation_histories[sender] = [
            m for m in conversation_histories[sender]
            if not isinstance(m.get("content"), str) or
               not m["content"].startswith(f"[Poll answer to \"{question}\"]")
        ]
        conversation_histories[sender].append({
            "role": "user",
            "content": f"[Poll answer to \"{question}\"]: {selected_text}"
        })

        messages = []
        for msg in conversation_histories[sender][-12:]:
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
        return

    # ── ai.jaeno.review_submit — customer submitted a post-order review card ──
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
    # (native Matrix single-choice poll, org.matrix.msc3381 — legacy path, separate
    # from the ai.jaeno.poll_response DSL polls handled above)
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

        # ── Resolve the selected answer id(s) back to their option text ───────
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

        # Save poll answer to SQLite for history
        try:
            from db import save_poll_answer
            save_poll_answer(
                room_id=room_id,
                sender=sender,
                poll_event_id=poll_event_id,
                question=poll["question"],
                answer=selected_text,
                poll_type="rating" if poll["question"].startswith("How would you rate your ") else "single_choice",
            )
        except Exception as e:
            logger.error(f"❌ Failed to save poll answer: {e}", exc_info=True)

        # ── Feed the answer back into the agent as the next conversation turn ──
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
            for msg in conversation_histories[sender][-12:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
            if sender in pending_orders:
                messages.append({"role": "user", "content": f"[Reminder — order in progress: {pending_orders[sender]}]"})

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

        # ── Single-choice polls reply immediately; multi-select polls debounce ──
        # so the agent only responds once the customer stops tapping options ────
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
