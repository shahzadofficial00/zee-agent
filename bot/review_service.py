from bot.matrix_client import matrix_client
async def send_review_card(room_id: str, order_id: str, menu_item: str = 'Your Order'):
    
    await matrix_client.room_send(
        room_id,
        message_type='m.room.message',
        content={
            'msgtype': 'm.text',
            'body': 'Review',
            'ai.jaeno.dsl': {
                'v': 1,
                'type': 'review',
                'data': {
                    'menu_item': menu_item,
                    'order_id': order_id,
                },
            },
        },
    )