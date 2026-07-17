import asyncio
from nio import MatrixRoom, UnknownEvent
from bot.matrix_client import matrix_client, BOT_START_TIME, send_text
from bot.router.state import (
    processed_event_ids, conversation_histories, pending_orders,
    poll_response_timers, logger, ensure_history_loaded, persist_history,
)
from bot.router.order_flow import _handle_order_flow_poll_answer, _handle_declined_confirmation
from bot.router.agent_invoke import _invoke_agent_with_retry
from bot.router.agent_dispatch import _dispatch_agent_result
from db import save_review


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
                        from bot.polls.poll_results_service import send_item_rating_results_to_room
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
        ensure_history_loaded(sender)
        conversation_histories[sender] = [
            m for m in conversation_histories[sender]
            if not isinstance(m.get("content"), str) or
               not m["content"].startswith(f"[Poll answer to \"{question}\"]")
        ]
        conversation_histories[sender].append({
            "role": "user",
            "content": f"[Poll answer to \"{question}\"]: {selected_text}"
        })
        persist_history(sender)

        messages = []
        for msg in conversation_histories[sender][-20:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        await matrix_client.room_typing(room_id, typing_state=True, timeout=8000)
        try:
            result = await _invoke_agent_with_retry(messages, sender)
            clean_reply = await _dispatch_agent_result(result, room_id, sender)
            conversation_histories[sender].append({
                "role": "assistant",
                "content": clean_reply or "Got it!"
            })
            persist_history(sender)
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
        ensure_history_loaded(sender)
        conversation_histories[sender] = [
            m for m in conversation_histories[sender]
            if not m["content"].startswith(f"[Poll answer to \"{poll['question']}\"]")
        ]
        conversation_histories[sender].append({
            "role": "user",
            "content": f"[Poll answer to \"{poll['question']}\"]: {selected_text}"
        })
        persist_history(sender)

        async def _run_agent_for_poll_answer():
            messages = []
            for msg in conversation_histories[sender][-20:]:
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
                persist_history(sender)
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
