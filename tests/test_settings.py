"""Task 1: settings storage + endpoints (theme, default sort, tag/category colors)."""

from stash.storage import _normalize_settings
from tests.conftest import login


def test_normalize_defaults_and_validation():
    assert _normalize_settings({}) == {"theme": "system", "defaultSort": "newest", "colors": {}}
    d = _normalize_settings({"theme": "dark", "defaultSort": "oldest",
                             "colors": {"tag:x": "#AABBCC", "cat:y": "#123456"}})
    assert d["theme"] == "dark" and d["defaultSort"] == "oldest"
    assert d["colors"]["tag:x"] == "#aabbcc"  # lowercased
    # Bad theme/sort fall back; bad colors dropped; injection never stored.
    d = _normalize_settings({"theme": "neon", "defaultSort": "z",
                             "colors": {"tag:a": "red", "tag:b": "#xyzxyz",
                                        "tag:c": "#00ff00", "bad:d": "#000000",
                                        "tag:e": "#000;background:url()"}})
    assert d["theme"] == "system" and d["defaultSort"] == "newest"
    assert d["colors"] == {"tag:c": "#00ff00"}


def test_storage_round_trip(storage):
    storage.save_settings({"theme": "light", "defaultSort": "oldest",
                           "colors": {"cat:work": "#112233"}})
    s = storage.get_settings()
    assert s["theme"] == "light" and s["colors"]["cat:work"] == "#112233"


def test_corrupt_settings_yields_defaults(storage):
    storage.kv_set("settings", "{ not json")
    assert storage.get_settings()["theme"] == "system"


async def test_settings_get_requires_auth(client):
    assert (await client.get("/board/settings")).status_code == 303


async def test_settings_save_and_get(client, storage):
    csrf = await login(client)
    r = await client.post("/board/settings", headers={"x-csrf-token": csrf},
                          json={"theme": "dark", "defaultSort": "oldest",
                                "colors": {"tag:x": "#00ff00", "tag:bad": "red"}})
    assert r.status_code == 200
    assert r.json()["theme"] == "dark"
    assert r.json()["colors"] == {"tag:x": "#00ff00"}  # bad dropped
    assert (await client.get("/board/settings")).json()["theme"] == "dark"


async def test_settings_bad_csrf_and_malformed(client):
    await login(client)
    r = await client.post("/board/settings", headers={"x-csrf-token": "bad"},
                          json={"theme": "light"})
    assert r.status_code == 403
    csrf = await login(client)
    r = await client.post("/board/settings",
                          headers={"x-csrf-token": csrf, "content-type": "application/json"},
                          content="not json")
    assert r.status_code == 400
    r = await client.post("/board/settings", headers={"x-csrf-token": csrf}, json=["a"])
    assert r.status_code == 400


def test_reindex_preserves_settings(storage, embedder):
    from stash.reindex import reindex
    storage.save_settings({"theme": "dark"})
    reindex(storage, embedder)
    assert storage.get_settings()["theme"] == "dark"
