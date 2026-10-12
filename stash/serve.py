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


def _scrub(text: str, token: str | None) -> str:
    """Redact the bot token from text bound for stderr.

    The token lives in the Telegram request URL by design (see
    telegram_client.py), so any exception whose repr happens to carry that
    URL would otherwise leak it into logs. Returns `text` unchanged when
    `token` is falsy.
    """
    if token:
        return text.replace(token, "***")
    return text


async def handle_updates(storage, embedder, delivery, ingest, allowed_ids, now_fn,
                         token: str | None = None) -> None:
    """One poll+dispatch pass.

    Polls from the stored `tg_offset`, then for each update in order: handles
    it if authorized (a private-chat text message from an allowed sender),
    or silently skips it otherwise. A capture is fully handled once its note
    is committed; the receipt reply is best-effort, so a send failure is
    logged and swallowed rather than re-raised. The offset always advances
    to `update_id + 1` after that, whether or not the reply went out, so a
    persistently failing send can never block later updates -- `capture`'s
    per-(source, chat, msg) unique index makes any replay from a genuine
    crash a no-op instead of a duplicate note.
    """
    offset_raw = storage.kv_get("tg_offset")
    offset = int(offset_raw) if offset_raw else None
    for msg in await ingest.poll(offset):
        if msg.text and is_authorized(msg, allowed_ids):
            reply = await handle_message(storage, embedder, msg, now_fn())
            if reply is not None:
                try:
                    await delivery.send(msg.chat_id, reply)
                except Exception as e:
                    print(f"stash serve: reply send failed: {_scrub(repr(e), token)}",
                          file=sys.stderr)
        # else: unauthorized / group / non-text -> skip, but still advance.
        storage.kv_set("tg_offset", str(msg.update_id + 1))


async def scheduler_tick(storage, delivery, lease, max_attempts, now_fn, lock,
                         allowed_ids) -> None:
    if lock.locked():
        return
    async with lock:
        await run_due(storage, delivery, now_fn(), lease, max_attempts, allowed_ids)


def acquire_single_instance(db_path: str):
    """Take an OS-level exclusive lock next to the DB so at most one `stash
    serve` runs against it. Held for the process lifetime (a leaked fd is not
    a leak here: it lives as long as the process and the OS reclaims it on
    exit or crash, which also releases the lock).

    Uses fcntl.flock on POSIX and msvcrt.locking on Windows, since fcntl does
    not exist on Windows. Both take an advisory exclusive lock that the OS drops
    when the process ends, so a crashed daemon never strands the lock.
    """
    _ALREADY = "another `stash serve` is already running on this database"
    lock_path = db_path + ".serve.lock"
    fh = open(lock_path, "w")
    try:
        import fcntl
    except ModuleNotFoundError:
        # Windows: no fcntl. Lock one byte with msvcrt instead.
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            fh.close()
            raise RuntimeError(_ALREADY) from exc
        return fh
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise RuntimeError(_ALREADY) from exc
    return fh


def _telegram_configured(config) -> bool:
    """True when both Telegram inputs are present. Either both or neither:
    a bot token with no allow-list would accept no one, and an allow-list with
    no token has nothing to poll, so a half-configured channel is an error, not
    a channel."""
    return bool(config.telegram_bot_token) and bool(config.allowed_sender_ids)


def _web_configured(config) -> bool:
    return bool(config.web_password) and bool(config.web_session_secret)


def _validate_config(config) -> None:
    # Fail fast, before touching the DB or the single-instance lock. The daemon
    # needs at least one channel to be worth running: Telegram, the web UI, or
    # both. A half-configured Telegram (token without allow-list, or the
    # reverse) is a misconfiguration, not a valid web-only start, so it is
    # rejected with a specific message rather than silently ignored.
    tg_token = bool(config.telegram_bot_token)
    tg_ids = bool(config.allowed_sender_ids)
    if tg_token and not tg_ids:
        raise RuntimeError(
            "stash serve: STASH_TELEGRAM_BOT_TOKEN is set but "
            "STASH_ALLOWED_SENDER_IDS is empty; refusing to start")
    if tg_ids and not tg_token:
        raise RuntimeError(
            "stash serve: STASH_ALLOWED_SENDER_IDS is set but "
            "STASH_TELEGRAM_BOT_TOKEN is not; refusing to start")
    if not _telegram_configured(config) and not _web_configured(config):
        raise RuntimeError(
            "stash serve: nothing to run. Configure the web UI "
            "(STASH_WEB_PASSWORD + STASH_WEB_SESSION_SECRET) or Telegram "
            "(STASH_TELEGRAM_BOT_TOKEN + STASH_ALLOWED_SENDER_IDS), or both.")


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

    stop = asyncio.Event()

    def _request_stop(*_):
        stop.set()
    loop = asyncio.get_running_loop()
    # loop.add_signal_handler is POSIX-only (Windows' event loop raises
    # NotImplementedError), and SIGTERM does not exist on Windows. Try the loop
    # handler per signal and fall back to signal.signal, which handles SIGINT
    # (Ctrl+C) on Windows. A signal we cannot install on this platform is
    # skipped rather than fatal.
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError, ValueError, OSError):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except (ValueError, OSError):
                pass  # not installable on this platform/thread; skip it

    # The Telegram poller and the reminder scheduler run only when Telegram is
    # configured. The scheduler delivers reminders through Telegram and only
    # ever claims reminders whose chat_id is in the allow-list, so with no
    # Telegram there is nothing for it to deliver; a web-only start skips both,
    # and reminders are still captured and shown in the web UI. The client is
    # built only on this path so a web-only start opens no Telegram connection.
    client = None
    coros = []
    if _telegram_configured(config):
        client = TelegramClient(config.telegram_bot_token)
        ingest = TelegramIngest(client)
        delivery = TelegramDelivery(client)
        sched_lock = asyncio.Lock()

        async def poll_loop():
            while not stop.is_set():
                try:
                    await handle_updates(storage, embedder, delivery, ingest,
                                         config.allowed_sender_ids, _utc_now,
                                         config.telegram_bot_token)
                except Exception as e:
                    print(f"stash serve: poll error: {_scrub(repr(e), config.telegram_bot_token)}",
                          file=sys.stderr)
                    await asyncio.sleep(1)

        async def sched_loop():
            while not stop.is_set():
                try:
                    await scheduler_tick(storage, delivery, config.reminder_lease_seconds,
                                         config.reminder_max_attempts, _utc_now, sched_lock,
                                         config.allowed_sender_ids)
                except Exception as e:
                    print(f"stash serve: scheduler error: "
                          f"{_scrub(repr(e), config.telegram_bot_token)}", file=sys.stderr)
                await asyncio.sleep(config.scheduler_tick_seconds)

        coros.extend([poll_loop(), sched_loop()])

    # Spec 6.11: the web server shares this one process and DB with the poller
    # and scheduler. It is optional, and so is Telegram, but _validate_config
    # guarantees at least one is configured, so there is always something here.
    web_coro = _web_loop(config, storage, embedder, stop)
    if web_coro is not None:
        coros.append(web_coro)

    try:
        await asyncio.gather(*coros)
    finally:
        if client is not None:
            await client.aclose()
        lock.close()


def _web_loop(config, storage, embedder, stop):
    """Return a coroutine that runs the uvicorn web server until `stop` is set,
    or None if the web UI is not configured.

    The server is driven directly (not uvicorn.run, which installs its own
    signal handlers and owns the loop): a watcher coroutine flips
    `server.should_exit` when the shared stop event fires, so one SIGINT/SIGTERM
    brings down all three loops together.
    """
    if not config.web_password or not config.web_session_secret:
        print("stash serve: web UI disabled (STASH_WEB_PASSWORD / "
              "STASH_WEB_SESSION_SECRET not set); running Telegram-only.",
              file=sys.stderr)
        return None

    import uvicorn
    from stash.web import build_app

    app = build_app(storage, embedder, config)
    uconfig = uvicorn.Config(
        app, host=config.web_bind, port=config.web_port,
        log_level="warning", lifespan="off",
    )
    server = uvicorn.Server(uconfig)
    # serve() owns shutdown: it installs its own SIGINT/SIGTERM handlers on the
    # loop and flips the shared stop event. uvicorn's serve() otherwise wraps
    # itself in capture_signals(), which reassigns those same signals to its own
    # handle_exit and restores the originals on exit, fighting serve()'s
    # handlers. Replace it with a no-op context manager so uvicorn never touches
    # signals; the stop-event watcher below is what tells it to exit.
    import contextlib

    @contextlib.contextmanager
    def _no_signals():
        yield

    server.capture_signals = _no_signals

    async def run():
        async def _watch_stop():
            await stop.wait()
            server.should_exit = True
        watcher = asyncio.ensure_future(_watch_stop())
        try:
            await server.serve()
        finally:
            watcher.cancel()

    return run()
