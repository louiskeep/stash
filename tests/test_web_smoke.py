"""Task 0 smoke: the web app builds and answers /healthz in-process.

Uses Starlette's in-process test transport (no socket, no network). The fake
embedder and an in-memory storage keep this fast and model-free.
"""

from starlette.testclient import TestClient

from stash.config import load_config
from stash.embed import FakeEmbedder
from stash.storage import Storage
from stash.web import build_app


def _app():
    storage = Storage.open(":memory:")
    embedder = FakeEmbedder()
    config = load_config({
        "STASH_WEB_PASSWORD": "pw",
        "STASH_WEB_SESSION_SECRET": "secret-secret-secret",
    })
    return build_app(storage, embedder, config)


def test_healthz_ok():
    client = TestClient(_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"
