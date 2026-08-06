from bot.matrix_client import send_text
from bot.router.state import last_orders, order_flows, logger
from bot.router.ids import generate_unique_tip_id
from db import get_payment, update_payment_status, save_review


async def handle_dsl_text_event(dsl: dict, sender: str, room_id: str) -> tuple[bool, str | None]:
    """Handle a DSL payload nested in a RoomMessageText event's content
    (content['ai.jaeno.dsl']). Returns (handled, message_override):
    handled=True means the caller (handle_message) should return immediately —
    the event was fully processed here. handled=False means fall through to the
    rest of handle_message; message_override (only set for order_summary) should
    replace event.body as the message text."""
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
            return True, None
        if payment['sender'] != sender:
            await send_text(room_id, "❌ Unauthorized.")
            return True, None
        if payment['status'] == 'cancelled':
            await send_text(room_id, "❌ This order was cancelled.")
            return True, None
        if payment['status'] == 'paid':
            print(f"⏭️ payment_success already processed for {order_id}, skipping")
            return True, None

        await update_payment_status(order_id=order_id, sender=sender, status='paid')
        if sender in last_orders:
            del last_orders[sender]
        await send_text(room_id, "✅ Payment confirmed! Thank you. Enjoy your meal 🍽️")
        return True, None

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
        return True, None

    # ── tip_selected — customer picked a preset/custom tip amount ────────
    # Deterministic, no LLM: create a payment intent for the tip amount
    # (same Swich flow as an order) and send the payment card.
    if dsl_type == 'tip_selected':
        # JNO-241 — tip_request cards sent before the cash-only switch are still
        # tappable in the customer's scrollback. Without this, send_payment_card()
        # bails on the guarded (None, None) intent and the tap does nothing at all.
        from config import ONLINE_PAYMENTS_ENABLED
        if not ONLINE_PAYMENTS_ENABLED:
            await send_text(room_id,
                "💵 Thank you! We're cash-only right now — "
                "feel free to tip in person when you get your order 🙏"
            )
            return True, None

        data = dsl.get('data', {})
        try:
            amount = int(float(data.get('amount', 0)))
        except (TypeError, ValueError):
            amount = 0
        if amount < 10:
            await send_text(room_id, "That tip amount is a bit too small to process — please pick a larger amount 🙏")
            return True, None

        from db import get_customer
        customer = get_customer(sender)
        if not customer:
            await send_text(room_id, "We couldn't find your details to process the tip — please place an order first 🙏")
            return True, None

        tip_order_id = await generate_unique_tip_id()
        from bot.payment.payment_service import send_payment_card
        await send_payment_card(
            room_id=room_id,
            amount=amount,
            customer_name=customer["name"],
            phone=customer["phone"],
            order_id=tip_order_id,
            user_id=sender,
        )
        return True, None

    # ── tip_declined — customer skipped the tip, just acknowledge ────────
    if dsl_type == 'tip_declined':
        await send_text(room_id, "No worries at all — thanks for stopping by! 🙏")
        return True, None

    # ── order_summary — customer built an order via the menu card UI ─────
    if dsl_type == 'order_summary':
        data = dsl.get('data', {})
        items = data.get('items', [])
        items_str = ", ".join(f"{i.get('name','')} x{i.get('qty',1)}" for i in items)
        message = f"I want to order: {items_str}"
        # NOT handled — falls through to the agent below
        return False, message

    # ── fulfillment_selection — customer picked how to receive the order ─────
    # Deterministic, no LLM: branch to whichever detail card the method needs.
    if dsl_type == 'fulfillment_selection':
        data = dsl.get('data', {})
        method = data.get('method', '').strip().lower()
        state = order_flows.get(sender)
        if not state or state.get('stage') != 'fulfillment_method':
            logger.info(f"⏭️ Skipping fulfillment_selection — no active fulfillment stage for {sender}")
            return True, None
        state['fulfillment_method'] = method
        order_id = state.get('order_id', '')

        if method in ('dine_in', 'pickup'):
            state['stage'] = 'awaiting_name'
            from bot.orders.fulfillment_service import send_name_request_card
            await send_name_request_card(room_id, order_id, method)
        elif method == 'car':
            state['stage'] = 'awaiting_car'
            from bot.orders.fulfillment_service import send_car_request_card
            await send_car_request_card(room_id, order_id)
        elif method == 'delivery':
            state['stage'] = 'awaiting_address'
            from bot.orders.fulfillment_service import send_address_request_card
            await send_address_request_card(room_id, order_id)
        else:
            order_flows.pop(sender, None)
            await send_text(room_id, "Sorry, I didn't recognize that — please try ordering again.")
        return True, None

    # ── name_response — dine-in/pickup name captured, ask final confirmation ──
    if dsl_type == 'name_response':
        data = dsl.get('data', {})
        state = order_flows.get(sender)
        if not state or state.get('stage') != 'awaiting_name':
            logger.info(f"⏭️ Skipping name_response — not awaiting a name for {sender}")
            return True, None
        state['fulfillment_summary'] = data.get('name', '').strip()
        state['stage'] = 'final_confirm'
        from bot.router.order_flow import _send_final_confirm_poll
        await _send_final_confirm_poll(sender, room_id)
        return True, None

    # ── car_response — vehicle details shared, ask final confirmation ────────
    if dsl_type == 'car_response':
        data = dsl.get('data', {})
        state = order_flows.get(sender)
        if not state or state.get('stage') != 'awaiting_car':
            logger.info(f"⏭️ Skipping car_response — not awaiting car details for {sender}")
            return True, None
        summary = f"{data.get('color', '').strip()} {data.get('make', '').strip()} {data.get('model', '').strip()}".strip()
        plate = data.get('plate_number', '').strip()
        if plate:
            summary = f"{summary} ({plate})".strip()
        state['fulfillment_summary'] = summary
        state['stage'] = 'final_confirm'
        from bot.router.order_flow import _send_final_confirm_poll
        await _send_final_confirm_poll(sender, room_id)
        return True, None

    # ── address_response — delivery address shared, ask final confirmation ───
    if dsl_type == 'address_response':
        data = dsl.get('data', {})
        state = order_flows.get(sender)
        if not state or state.get('stage') != 'awaiting_address':
            logger.info(f"⏭️ Skipping address_response — not awaiting an address for {sender}")
            return True, None
        parts = [data.get('line1', '').strip(), data.get('line2', '').strip(), data.get('city', '').strip()]
        state['fulfillment_summary'] = ", ".join(p for p in parts if p)
        state['stage'] = 'final_confirm'
        from bot.router.order_flow import _send_final_confirm_poll
        await _send_final_confirm_poll(sender, room_id)
        return True, None

    # ── bid_confirmation — customer placed/updated a bid on a live auction ──
    # Deterministic, no LLM: re-validate server-side (the client only checks
    # the amount locally) and record it as the customer's current bid.
    if dsl_type == 'bid_confirmation':
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
        return True, None

    # ── terms_response — customer agreed to / declined the order terms ───
    # Deterministic, no LLM: record the consent, then resume or cancel the
    # order that _send_final_confirm_poll() parked at 'awaiting_terms'.
    if dsl_type == 'terms_response':
        from bot.terms.terms_service import TERMS_ID, TERMS_VERSION, TERMS_BODY
        from db import save_agreement
        data = dsl.get('data', {})

        # Inbound DSL is never schema-validated, so everything below is a
        # trust boundary. An agreement recorded against a terms_id/version we
        # never published would be a signature on a document that doesn't
        # exist — drop it rather than store it.
        terms_id = str(data.get('terms_id', '')).strip()
        version = str(data.get('version', '')).strip()
        if terms_id != TERMS_ID or version != TERMS_VERSION:
            logger.warning(
                f"⏭️ Dropping terms_response for unknown {terms_id}@{version} from {sender}"
            )
            return True, None

        method = str(data.get('method', '')).strip()
        if method not in ('tap', 'typed', 'drawn'):
            logger.warning(f"⏭️ Dropping terms_response with bad method={method!r} from {sender}")
            return True, None

        agreed = data.get('agreed') is True
        signature = data.get('signature') if agreed else None
        save_agreement(
            user_id=sender,
            room_id=room_id,
            terms_id=terms_id,
            version=version,
            agreed=agreed,
            method=method,
            signature=signature if isinstance(signature, str) else None,
            signed_at=str(data.get('signed_at', '')),
            # Safe to snapshot the constant here and *only* here: the version
            # guard above already rejected anything that isn't the currently
            # published version, so TERMS_BODY is by definition the text this
            # customer was shown. Reads must come from the row, never from here.
            body=TERMS_BODY,
        )

        state = order_flows.get(sender)
        if not state or state.get('stage') != 'awaiting_terms':
            # Consent still recorded above — it's valid on its own. There's
            # just no parked order to resume (restart, or a stale card tapped
            # from scrollback).
            await send_text(
                room_id,
                "✅ Thanks — your agreement has been recorded." if agreed
                else "No problem — nothing has been recorded against your account."
            )
            return True, None

        if not agreed:
            order_flows.pop(sender, None)
            await send_text(
                room_id,
                "No problem — I haven't placed the order. "
                "We can't take orders without agreeing to the terms, but I'm here if you have questions 🙏"
            )
            return True, None

        # Consent is on file now, so the gate inside _send_final_confirm_poll()
        # falls through this time and the customer gets the "Shall I proceed?"
        # poll they'd otherwise have seen before the terms card.
        state['stage'] = 'final_confirm'
        from bot.router.order_flow import _send_final_confirm_poll
        await _send_final_confirm_poll(sender, room_id)
        return True, None

    # ── poll — the bot's own poll card being echoed back to the room ─────
    elif dsl_type == 'poll':
        # Bot's own poll card being echoed back — skip
        logger.info(f"⏭️ Skipping bot's own poll DSL from {sender}")
        return True, None

    # ── any other DSL type is not handled here — ignore ──────────────────
    else:
        logger.info(f"⏭️ Skipping DSL event type={dsl_type} from {sender}")
        return True, None
