"""Async Telegram Bot API client. Never logs the bot token or message text."""

import httpx


class TelegramClient:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org",
                 client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._own = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url)

    def _path(self, method: str) -> str:
        return f"/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict) -> dict:
        resp = await self._client.post(self._path(method), json=payload)
        if resp.status_code != 200:
            # Do not include the token (in the URL) or the payload text.
            raise RuntimeError(f"telegram {method} failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} not ok")
        return data

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload: dict = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = await self._call("getUpdates", payload)
        return data.get("result", [])

    async def send_message(self, chat_id: str, text: str) -> None:
        await self._call("sendMessage", {"chat_id": chat_id, "text": text})

    async def aclose(self) -> None:
        if self._own:
            await self._client.aclose()
