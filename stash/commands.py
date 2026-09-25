"""Telegram command dispatch. Plain text captures; /commands route. No chat."""

from stash.capture import capture
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


def handle_message(storage, embedder, msg, now: str) -> str | None:
    text = msg.text or ""
    if not text.strip():
        return None
    first = text.split(maxsplit=1)[0]
    if first in _COMMANDS:
        rest = text[len(first):].strip()
        if first == "/help":
            reply = "commands: /find <query>, /recent, /help. Any other text is stashed."
        elif first == "/find":
            reply = _tiles(search(storage, embedder, rest, limit=5))
        elif first == "/recent":
            rows = storage.conn.execute(
                "SELECT raw, created_at FROM notes ORDER BY id DESC LIMIT 5").fetchall()
            reply = "\n\n".join(f"[{r['created_at'][:10]}] {r['raw']}" for r in rows) or "no notes yet."
        return reply[:_MAX]
    # Not a registered command (including an unknown leading '/') -> capture.
    result = capture(storage, embedder, text, "telegram", now,
                     source_chat_id=msg.chat_id, source_msg_id=msg.msg_id)
    return result.receipt[:_MAX]
