"""Capture pipeline: durable raw write first, then derivation. Crash-safe."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from stash.parse import parse


@dataclass
class CaptureResult:
    note_id: int | None
    intent: str
    receipt: str
    deduped: bool


def _fire_at(created_at: str, seconds: int) -> str:
    base = datetime.fromisoformat(created_at)
    return (base + timedelta(seconds=seconds)).isoformat()


def derive(storage, embedder, note_id: int, raw: str, created_at: str) -> str:
    parsed = parse(raw)
    storage.set_tags(note_id, parsed.tags)
    storage.set_meta(note_id, parsed.category, parsed.intent)
    storage.set_embedding(note_id, embedder.embed(raw))
    if parsed.intent == "reminder" and parsed.remind_in_seconds is not None:
        fire_at = _fire_at(created_at, parsed.remind_in_seconds)
        # Carry the delivery target (channel + chat) from the note, so a later
        # channel adapter knows where to send. unique(note_id) + DO NOTHING makes
        # this idempotent across re-derivation.
        with storage.conn:
            storage.conn.execute(
                "INSERT INTO reminders(note_id, fire_at, status, channel, chat_id)"
                " SELECT id, ?, 'pending', source, source_chat_id"
                " FROM notes WHERE id=?"
                " ON CONFLICT(note_id) DO NOTHING",
                (fire_at, note_id),
            )
    storage.mark_derived(note_id, datetime.now(timezone.utc).isoformat())
    return parsed.intent


def _receipt(parsed_intent: str, raw: str, resolvable: bool) -> str:
    if parsed_intent == "reminder" and not resolvable:
        return "saved as a note. I could not read a time, so no reminder was set."
    if parsed_intent == "reminder":
        return "stashed and reminder set."
    return "stashed."


def capture(storage, embedder, raw, source, created_at,
            source_chat_id=None, source_msg_id=None) -> CaptureResult:
    note_id = storage.add_note(
        raw, source, created_at, source_chat_id, source_msg_id)
    if note_id is None:
        return CaptureResult(None, "note", "already stashed.", deduped=True)
    parsed = parse(raw)
    resolvable = parsed.intent == "reminder" and parsed.remind_in_seconds is not None
    intent = derive(storage, embedder, note_id, raw, created_at)
    return CaptureResult(
        note_id, intent, _receipt(parsed.intent, raw, resolvable), deduped=False)


def repair(storage, embedder) -> int:
    count = 0
    for note_id in storage.underived_note_ids():
        row = storage.get_note(note_id)
        derive(storage, embedder, note_id, row["raw"], row["created_at"])
        count += 1
    return count
