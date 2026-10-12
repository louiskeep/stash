"""Task 7: the web server composes into the serve loop and stops with it.

These exercise `_web_loop` directly (the new composition logic) rather than the
full `serve()`, which would load the real embedder and open a Telegram client.
"""

import asyncio

from stash.config import load_config
from stash.embed import FakeEmbedder
from stash.serve import _web_loop
from stash.storage import Storage

WEB_ENV = {
    "STASH_WEB_PASSWORD": "hunter2",
    "STASH_WEB_SESSION_SECRET": "sess-secret-sess-secret-0123",
    "STASH_WEB_PORT": "8199",
    "STASH_WEB_BIND": "127.0.0.1",
}


def test_web_disabled_when_unconfigured(capsys):
    cfg = load_config({})
    storage = Storage.open(":memory:")
    result = _web_loop(cfg, storage, FakeEmbedder(), asyncio.Event())
    assert result is None
    err = capsys.readouterr().err
    assert "web UI disabled" in err


def test_web_enabled_returns_coroutine():
    cfg = load_config(dict(WEB_ENV))
    storage = Storage.open(":memory:")
    coro = _web_loop(cfg, storage, FakeEmbedder(), asyncio.Event())
    assert asyncio.iscoroutine(coro)
    coro.close()  # do not actually bind the socket in this unit test


async def test_stop_event_ends_the_server():
    # Run the server coroutine for real on an ephemeral port, then set the stop
    # event and assert the coroutine returns promptly (one signal stops it).
    cfg = load_config({**WEB_ENV, "STASH_WEB_PORT": "0"})
    storage = Storage.open(":memory:")
    stop = asyncio.Event()
    coro = _web_loop(cfg, storage, FakeEmbedder(), stop)
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0.2)  # let uvicorn start
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert task.done() and task.exception() is None


# --- W3 H1 fix: serve() must allow a web-only start (no Telegram config) -------

from stash.serve import _validate_config, _telegram_configured, _web_configured


def test_validate_allows_web_only():
    cfg = load_config({"STASH_WEB_PASSWORD": "pw",
                       "STASH_WEB_SESSION_SECRET": "x" * 20})
    _validate_config(cfg)  # must not raise
    assert _web_configured(cfg) and not _telegram_configured(cfg)


def test_validate_allows_telegram_only():
    cfg = load_config({"STASH_TELEGRAM_BOT_TOKEN": "t",
                       "STASH_ALLOWED_SENDER_IDS": "123"})
    _validate_config(cfg)
    assert _telegram_configured(cfg) and not _web_configured(cfg)


def test_validate_allows_both():
    cfg = load_config({"STASH_TELEGRAM_BOT_TOKEN": "t",
                       "STASH_ALLOWED_SENDER_IDS": "123",
                       "STASH_WEB_PASSWORD": "pw",
                       "STASH_WEB_SESSION_SECRET": "x" * 20})
    _validate_config(cfg)


def test_validate_rejects_neither():
    import pytest
    with pytest.raises(RuntimeError, match="nothing to run"):
        _validate_config(load_config({}))


def test_validate_rejects_half_telegram_token_only():
    import pytest
    cfg = load_config({"STASH_TELEGRAM_BOT_TOKEN": "t",
                       "STASH_WEB_PASSWORD": "pw",
                       "STASH_WEB_SESSION_SECRET": "x" * 20})
    with pytest.raises(RuntimeError, match="ALLOWED_SENDER_IDS is empty"):
        _validate_config(cfg)


def test_validate_rejects_half_telegram_ids_only():
    import pytest
    with pytest.raises(RuntimeError, match="BOT_TOKEN is not"):
        _validate_config(load_config({"STASH_ALLOWED_SENDER_IDS": "123"}))


async def test_web_only_serve_runs_without_telegram_client(monkeypatch):
    # The headline W3 scenario: a container configured for web only must start
    # the web server and NOT build a Telegram client, then stop on signal.
    import stash.serve as S

    built = {"n": 0}

    class _FakeStorage:
        def underived_note_ids(self): return []
        def kv_get(self, k): return None
        def kv_set(self, k, v): pass

    monkeypatch.setattr(S, "Storage",
                        type("S", (), {"open": staticmethod(lambda *a, **k: _FakeStorage())}))
    monkeypatch.setattr("stash.embed.SentenceTransformerEmbedder", FakeEmbedder)
    monkeypatch.setattr(S, "acquire_single_instance",
                        lambda db: type("L", (), {"close": lambda self: None})())

    def _tc(tok):
        built["n"] += 1
        return type("C", (), {"aclose": lambda self: asyncio.sleep(0)})()
    monkeypatch.setattr(S, "TelegramClient", _tc)

    cfg = load_config({"STASH_WEB_PASSWORD": "pw", "STASH_WEB_SESSION_SECRET": "x" * 20,
                       "STASH_WEB_PORT": "0", "STASH_WEB_BIND": "127.0.0.1"})
    task = asyncio.ensure_future(S.serve(cfg))
    await asyncio.sleep(0.4)
    assert not task.done(), "web-only serve should be running"
    import os
    import signal
    os.kill(os.getpid(), signal.SIGINT)
    try:
        await asyncio.wait_for(task, timeout=5)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    assert built["n"] == 0, "web-only must not build a Telegram client"
