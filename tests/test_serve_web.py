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
