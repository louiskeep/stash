"""Telegram command dispatch. Plain text captures; /commands route. No chat."""

import asyncio

from stash.capture import capture_async
from stash.recall import search

_MAX = 4096
_COMMANDS = ("/find", "/recent", "/help")


def _tiles(results) -> str:
    if not results:
        return "nothing found."
    lines = []
    for r in results:
        tags = (" " + " ".join(f"#{t}" for t in r.tags)) if r.tags else ""
        lines.append(f"[{r.created_at[:10]}] {r.raw}{tags}")
    return "\n\n".join(lines)


async def handle_message(storage, embedder, msg, now: str) -> str | None:
    # embedder.embed() is a CPU-bound model call; it runs off the event loop
    # via asyncio.to_thread so it never stalls other coroutines (the
    # reminder scheduler, other polls) sharing the loop. The capture/search
    # SQLite calls stay on the loop thread, since sqlite3 connections are
    # not thread-safe across threads by default. capture_async is raw-first:
    # the note is written before the embed offload starts, so an embed
    # failure can never lose it (see stash/capture.py).
    text = msg.text or ""
    if not text.strip():
        return None
    parts = text.split(maxsplit=1)
    first = parts[0] if parts else ""
    if first in _COMMANDS:
        rest = parts[1].strip() if len(parts) > 1 else ""
        if first == "/help":
            reply = "commands: /find <query>, /recent, /help. Any other text is stashed."
        elif first == "/find":
            qvec = await asyncio.to_thread(embedder.embed, rest)
            reply = _tiles(search(storage, embedder, rest, limit=5, query_embedding=qvec))
        elif first == "/recent":
            rows = storage.conn.execute(
                "SELECT raw, created_at FROM notes ORDER BY id DESC LIMIT 5").fetchall()
            reply = "\n\n".join(f"[{r['created_at'][:10]}] {r['raw']}" for r in rows) or "no notes yet."
        return reply[:_MAX]
    # Not a registered command (including an unknown leading '/') -> capture.
    result = await capture_async(storage, embedder, text, "telegram", now,
                                 source_chat_id=msg.chat_id, source_msg_id=msg.msg_id)
    return result.receipt[:_MAX]
