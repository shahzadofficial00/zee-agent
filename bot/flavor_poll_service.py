import aiohttp
import uuid
from urllib.parse import quote
from db import save_poll

HOMESERVER = "https://chat.jaeno.ai"

async def send_flavor_preference_poll_to_room(matrix_client, room_id: str):
    print("🟣 FLAVOR POLL SERVICE CALLED")
    question = "What flavors do you enjoy?"
    options = ["Bold & Strong", "Creamy", "Chocolatey", "Sweet", "Nutty", "Fruity", "Earthy & Matcha"]

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
            "max_selections": len(answers),
            "question": {
                "org.matrix.msc1767.text": question,
                "msgtype": "m.text",
                "body": question,
            },
            "answers": answers,
        },
    }

    print(f"🗳️ DEBUG flavor poll: len(answers)={len(answers)} max_selections={len(answers)}")

    txn_id = uuid.uuid4().hex
    encoded_room_id = quote(room_id, safe="")
    url = f"{HOMESERVER}/_matrix/client/v3/rooms/{encoded_room_id}/send/org.matrix.msc3381.poll.start/{txn_id}"

    headers = {
        "Authorization": f"Bearer {matrix_client.access_token}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.put(url, json=content, headers=headers) as resp:
            data = await resp.json()
            event_id = data.get("event_id")
            print(f"🗳️ Flavor poll sent: event_id={event_id} status={resp.status}")

    if event_id:
        save_poll(room_id, event_id, question, options, multi_select=True)

    return event_id