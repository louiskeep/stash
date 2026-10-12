"""Shared fixtures for the web tests.

These build the app over a real in-memory Storage (so the browse/search queries
run against actual SQLite + sqlite-vec) with the deterministic FakeEmbedder, and
drive it with httpx's in-process ASGI transport. No socket, no model load.

The client is an async httpx client over ASGITransport, so request handlers run
on the same event-loop thread as the test and the Storage connection. That
matches production, where the serve loop thread owns the connection: it keeps
SQLite's single-thread affinity intact. (Starlette's sync TestClient would run
handlers on a separate portal thread and trip check_same_thread.)
"""

import re

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from stash.capture import capture
from stash.config import load_config
from stash.embed import FakeEmbedder
from stash.storage import Storage
from stash.web import build_app

WEB_ENV = {
    "STASH_WEB_PASSWORD": "hunter2",
    "STASH_WEB_SESSION_SECRET": "sess-secret-sess-secret-0123",
}


@pytest.fixture
def storage():
    return Storage.open(":memory:")


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def config():
    return load_config(dict(WEB_ENV))


@pytest.fixture
def app(storage, embedder, config):
    return build_app(storage, embedder, config)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://stash.test",
        follow_redirects=False,
    ) as c:
        yield c


@pytest.fixture
def seed(storage, embedder):
    """Capture a small corpus and return a helper to add more."""
    from datetime import datetime, timezone

    def add(text, when=None):
        now = when or datetime.now(timezone.utc).isoformat()
        return capture(storage, embedder, text, "web", now)

    add("buy milk and eggs #groceries")
    add("call the vendor about the invoice @work")
    add("idea: a second brain that isn't a chatbot #stash")
    return add


async def _login_csrf(client):
    page = (await client.get("/login")).text
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


async def login(client):
    """Log the client in; return the session CSRF token from the home page."""
    token = await _login_csrf(client)
    await client.post("/login", data={"password": "hunter2", "csrf_token": token})
    home = (await client.get("/")).text
    m = re.search(r'name="csrf-token" content="([^"]+)"', home)
    return m.group(1) if m else None
