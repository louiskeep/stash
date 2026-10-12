"""Task 1: the /board/search endpoint (top-bar search over hybrid recall)."""

from datetime import datetime, timezone

from stash.capture import capture
from tests.conftest import login


def _now():
    return datetime.now(timezone.utc).isoformat()


async def test_search_returns_matching_ids(client, storage, embedder):
    a = capture(storage, embedder, "the quarterly invoice from the vendor", "web", _now()).note_id
    capture(storage, embedder, "trail run on saturday", "web", _now())
    await login(client)
    r = await client.get("/board/search", params={"q": "invoice"})
    assert r.status_code == 200
    data = r.json()
    assert a in data["ids"]


async def test_empty_query_is_inactive(client, storage, embedder):
    capture(storage, embedder, "a note", "web", _now())
    await login(client)
    r = await client.get("/board/search", params={"q": "   "})
    assert r.status_code == 200
    assert r.json()["ids"] == []


async def test_search_requires_auth(client, storage, embedder):
    capture(storage, embedder, "secret", "web", _now())
    r = await client.get("/board/search", params={"q": "secret"})
    assert r.status_code == 303
    assert "secret" not in r.text


async def test_odd_query_does_not_500(client, storage, embedder):
    capture(storage, embedder, "note", "web", _now())
    await login(client)
    for q in ['"', "a AND", "((", "NOT", "*"]:
        r = await client.get("/board/search", params={"q": q})
        assert r.status_code == 200
