import re as _re
from bot.matrix_client import matrix_client, send_text
from bot.router.state import (
    order_flows, last_order_line, last_order_state,
    awaiting_reorder_confirmation, _REORDER_AFFIRMATIONS, conversation_histories,
    ensure_history_loaded, persist_history,
)
from bot.router.agent_dispatch import _dispatch_agent_result

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
        from bot.polls.poll_service import send_single_choice_poll_to_room
        await send_single_choice_poll_to_room(
            matrix_client, room_id, f"What size would you like for {item}?", ["Small", "Medium", "Large"]
        )
    elif state["stage"] == "instructions":
        from bot.polls.special_instructions_poll_service import send_special_instructions_poll_to_room
        await send_special_instructions_poll_to_room(
            matrix_client, room_id,
            placeholder="e.g. extra hot, less sugar, no ice",
            question=f"Any special instructions for your {item}?",
        )


async def _send_final_confirm_poll(sender: str, room_id: str) -> None:
    from bot.polls.poll_service import send_single_choice_poll_to_room
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
        from bot.polls.poll_service import send_single_choice_poll_to_room
        await send_single_choice_poll_to_room(
            matrix_client, room_id,
            f"Should I use {customer['name']}, {customer['phone']} as before?",
            ["Yes", "No"],
        )
    else:
        state["stage"] = "await_name"
        await send_text(room_id, "Could I get your full name for the order?")
    await matrix_client.room_typing(room_id, typing_state=False)


async def _start_fulfillment_stage(sender: str, room_id: str) -> None:
    """Final Yes received — before actually placing the order, ask how the
    customer wants to receive it (JNO-164/165). The stable order_id is
    generated here (not later in agent_dispatch.py) so it's consistent across
    every fulfillment card, the eventual receipt, and the payment intent."""
    state = order_flows.get(sender)
    if not state:
        return
    from bot.router.ids import generate_unique_order_id
    state["order_id"] = await generate_unique_order_id()
    state["stage"] = "fulfillment_method"
    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
    from bot.orders.fulfillment_service import send_fulfillment_method_card
    await send_fulfillment_method_card(room_id, state["order_id"])
    await matrix_client.room_typing(room_id, typing_state=False)


async def _place_deterministic_order(sender: str, room_id: str) -> None:
    """Fulfillment details collected (or skipped) — call confirm_order directly
    (no LLM in the loop) and dispatch the resulting signal string through the
    normal card-sending path."""
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

    from agent.tools.orders.confirm_order import confirm_order
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

    fulfillment = None
    if state.get("fulfillment_method"):
        fulfillment = {
            "method": state["fulfillment_method"],
            "summary": state.get("fulfillment_summary", ""),
            "order_id": state.get("order_id"),
        }

    await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
    clean_reply = await _dispatch_agent_result(fake_result, room_id, sender, fulfillment=fulfillment)
    ensure_history_loaded(sender)
    conversation_histories[sender].append({
        "role": "assistant",
        "content": clean_reply or "Got it!"
    })
    persist_history(sender)

    if "ORDER_SAVED" in result_text:
        from bot.payment.tip_service import send_tip_request_to_room
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
            await _start_fulfillment_stage(sender, room_id)
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
