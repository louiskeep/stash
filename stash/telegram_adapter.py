"""Bind the Telegram client to the stash async ports."""

import asyncio

from stash.ports import IncomingMessage


def parse_update(update: dict) -> IncomingMessage | None:
    msg = update.get("message")
    if not msg or "text" not in msg:
        return None
    chat = msg.get("chat", {})
    frm = msg.get("from", {})
    if "id" not in chat or "id" not in frm:
        return None
    return IncomingMessage(
        update_id=int(update["update_id"]),
        chat_id=str(chat["id"]),
        chat_type=str(chat.get("type", "")),
        sender_id=str(frm["id"]),
        msg_id=str(msg.get("message_id", "")),
        text=msg["text"],
    )


def is_authorized(msg: IncomingMessage, allowed_ids) -> bool:
    return msg.chat_type == "private" and msg.sender_id in tuple(allowed_ids)


class TelegramIngest:
    def __init__(self, client) -> None:
        self._client = client

    async def poll(self, offset):
        raw = await self._client.get_updates(offset)
        out = []
        for u in raw:
            m = parse_update(u)
            if m is not None:
                out.append(m)
            elif "update_id" in u:
                out.append(IncomingMessage(int(u["update_id"]), "", "", "", "", ""))
        return out


class TelegramDelivery:
    def __init__(self, client, attempts: int = 3) -> None:
        self._client = client
        self._attempts = attempts

    async def send(self, chat_id: str, text: str) -> None:
        last = None
        for _ in range(self._attempts):
            try:
                await self._client.send_message(chat_id, text)
                return
            except Exception as e:  # transient; retry a couple times
                last = e
                await asyncio.sleep(0)
        raise last
