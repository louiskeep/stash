"""The `stash serve` daemon: one asyncio process running the Telegram poller
and the reminder scheduler over a single SQLite DB.

Two loops share the DB: `poll_loop` ingests Telegram updates and captures
notes; `sched_loop` fires due reminders. `run_due` is only safe when calls to
it never overlap (see stash/reminders.py), so `scheduler_tick` serializes it
behind an asyncio.Lock -- a live daemon tick and a retry tick can otherwise
race, and skipping (rather than queuing) a tick when the lock is held is
fine because the next tick, seconds away, picks up the same due reminders.
"""

import asyncio
import signal
import sys
from datetime import datetime, timezone

from stash.capture import repair
from stash.commands import handle_message
from stash.reindex import reindex
from stash.reminders import run_due
from stash.storage import Storage
from stash.telegram_adapter import is_authorized, TelegramIngest, TelegramDelivery
from stash.telegram_client import TelegramClient


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def handle_updates(storage, embedder, delivery, ingest, allowed_ids, now_fn) -> None:
    """One poll+dispatch pass.

    Polls from the stored `tg_offset`, then for each update in order: handles
    it if authorized (a private-chat text message from an allowed sender)
    and sends the reply, or silently skips it otherwise. The offset advances
    to `update_id + 1` only after an update is fully handled, so a crash
    mid-handling replays that update on the next poll rather than losing it
    -- `capture`'s per-(source, chat, msg) unique index makes that replay a
    no-op instead of a duplicate note.
    """
    offset_raw = storage.kv_get("tg_offset")
    offset = int(offset_raw) if offset_raw else None
    for msg in await ingest.poll(offset):
        if msg.text and is_authorized(msg, allowed_ids):
            reply = await handle_message(storage, embedder, msg, now_fn())
            if reply is not None:
                await delivery.send(msg.chat_id, reply)
        # else: unauthorized / group / non-text -> skip, but still advance.
        storage.kv_set("tg_offset", str(msg.update_id + 1))


async def scheduler_tick(storage, delivery, lease, max_attempts, now_fn, lock) -> None:
    if lock.locked():
        return
    async with lock:
        await run_due(storage, delivery, now_fn(), lease, max_attempts)


def acquire_single_instance(db_path: str):
    """Take an OS-level exclusive lock next to the DB so at most one `stash
    serve` runs against it. Held for the process lifetime (a leaked fd is not
    a leak here: it lives as long as the process and the OS reclaims it on
    exit or crash, which also releases the flock).
    """
    import fcntl
    lock_path = db_path + ".serve.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise RuntimeError(
            "another `stash serve` is already running on this database") from exc
    return fh


def _validate_config(config) -> None:
    # Fail fast, before touching the DB or the single-instance lock: a
    # daemon started without a token or an allow-list would otherwise sit
    # there polling with every Telegram call erroring, or accepting no one's
    # messages, with nothing to say why until someone reads the logs.
    if not config.telegram_bot_token:
        raise RuntimeError("stash serve: STASH_TELEGRAM_BOT_TOKEN is not set")
    if not config.allowed_sender_ids:
        raise RuntimeError(
            "stash serve: STASH_ALLOWED_SENDER_IDS is empty; refusing to start")


async def serve(config) -> None:
    _validate_config(config)
    lock = acquire_single_instance(config.db_path)
    storage = Storage.open(config.db_path, config.busy_timeout_ms)
    from stash.embed import SentenceTransformerEmbedder
    embedder = SentenceTransformerEmbedder(config.embed_model)

    # Startup self-heal + embed-model reconcile (same policy as the CLI:
    # see stash/cli.py). Cheap when there is nothing to do, since
    # underived_note_ids() is a plain query and the embedder is lazy-loaded.
    if storage.underived_note_ids():
        repair(storage, embedder)
    stored = storage.kv_get("embed_model")
    if stored is None:
        storage.kv_set("embed_model", embedder.name)
    elif stored != embedder.name:
        reindex(storage, embedder)
        storage.kv_set("embed_model", embedder.name)

    client = TelegramClient(config.telegram_bot_token)
    ingest = TelegramIngest(client, config.allowed_sender_ids)
    delivery = TelegramDelivery(client)
    sched_lock = asyncio.Lock()
    stop = asyncio.Event()

    def _request_stop(*_):
        stop.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    async def poll_loop():
        while not stop.is_set():
            try:
                await handle_updates(storage, embedder, delivery, ingest,
                                     config.allowed_sender_ids, _utc_now)
            except Exception as e:
                print(f"stash serve: poll error: {e!r}", file=sys.stderr)
                await asyncio.sleep(1)

    async def sched_loop():
        while not stop.is_set():
            await scheduler_tick(storage, delivery, config.reminder_lease_seconds,
                                 config.reminder_max_attempts, _utc_now, sched_lock)
            await asyncio.sleep(config.scheduler_tick_seconds)

    try:
        await asyncio.gather(poll_loop(), sched_loop())
    finally:
        await client.aclose()
        lock.close()
