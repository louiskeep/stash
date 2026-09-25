"""Capture pipeline: durable raw write first, then derivation. Crash-safe."""

import asyncio
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


def derive(storage, embedder, note_id: int, raw: str, created_at: str,
           embedding: list[float] | None = None) -> str:
    parsed = parse(raw)
    storage.set_tags(note_id, parsed.tags)
    storage.set_meta(note_id, parsed.category, parsed.intent)
    vec = embedding if embedding is not None else embedder.embed(raw)
    storage.set_embedding(note_id, vec)
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
            source_chat_id=None, source_msg_id=None,
            embedding: list[float] | None = None) -> CaptureResult:
    note_id = storage.add_note(
        raw, source, created_at, source_chat_id, source_msg_id)
    if note_id is None:
        # Dedupe hit on (source, chat, msg). If the existing note from a
        # prior capture never finished deriving (e.g. it crashed between the
        # raw write and derive()), heal it now instead of leaving it stuck.
        existing = storage.get_note_by_source_key(source, source_chat_id, source_msg_id)
        if existing is not None and existing["derived_at"] is None:
            derive(storage, embedder, existing["id"], existing["raw"], existing["created_at"])
        return CaptureResult(None, "note", "already stashed.", deduped=True)
    parsed = parse(raw)
    resolvable = parsed.intent == "reminder" and parsed.remind_in_seconds is not None
    intent = derive(storage, embedder, note_id, raw, created_at, embedding=embedding)
    return CaptureResult(
        note_id, intent, _receipt(parsed.intent, raw, resolvable), deduped=False)


async def capture_async(storage, embedder, raw, source, created_at,
                        source_chat_id=None, source_msg_id=None) -> CaptureResult:
    """Raw-first capture with the embed call offloaded off the event loop.

    The raw note is written before anything touches the embedder, so an
    embed() failure (or any derive() failure) leaves a recoverable note
    instead of losing it. See `capture()` for the sync/CLI counterpart.
    """
    note_id = storage.add_note(
        raw, source, created_at, source_chat_id, source_msg_id)
    if note_id is None:
        existing = storage.get_note_by_source_key(source, source_chat_id, source_msg_id)
        if existing is not None and existing["derived_at"] is None:
            vec = await asyncio.to_thread(embedder.embed, existing["raw"])
            derive(storage, embedder, existing["id"], existing["raw"],
                   existing["created_at"], embedding=vec)
        return CaptureResult(None, "note", "already stashed.", deduped=True)
    parsed = parse(raw)
    resolvable = parsed.intent == "reminder" and parsed.remind_in_seconds is not None
    vec = await asyncio.to_thread(embedder.embed, raw)
    intent = derive(storage, embedder, note_id, raw, created_at, embedding=vec)
    return CaptureResult(
        note_id, intent, _receipt(parsed.intent, raw, resolvable), deduped=False)


def repair(storage, embedder) -> int:
    count = 0
    for note_id in storage.underived_note_ids():
        row = storage.get_note(note_id)
        derive(storage, embedder, note_id, row["raw"], row["created_at"])
        count += 1
    return count
