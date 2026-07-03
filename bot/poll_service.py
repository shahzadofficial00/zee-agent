from db import save_poll

async def send_single_choice_poll_to_room(matrix_client, room_id: str, question: str, options: list[str]):
    answers = [
        {"id": f"answer-{i+1}", "org.matrix.msc1767.text": opt}
        for i, opt in enumerate(options)
    ]
    content = {
        "org.matrix.msc1767.text": question + "\n" + "\n".join(
            f"{i}. {opt}" for i, opt in enumerate(options)
        ),
        "org.matrix.msc3381.poll.start": {
            "kind": "org.matrix.msc3381.poll.disclosed",
            "max_selections": 1,
            "question": {
                "org.matrix.msc1767.text": question,
                "msgtype": "m.text",
                "body": question,
            },
            "answers": answers,
        },
    }
    response = await matrix_client.room_send(
        room_id=room_id,
        message_type="org.matrix.msc3381.poll.start",
        content=content,
    )
    event_id = getattr(response, "event_id", None)
    if event_id:
        save_poll(room_id, event_id, question, options, multi_select=False)
    return event_id