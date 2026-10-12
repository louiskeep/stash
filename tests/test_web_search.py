"""Task 4: search page, htmx fragment, add-note."""

from tests.conftest import login


async def test_search_finds_seeded_note(client, seed):
    await login(client)
    r = await client.get("/search", params={"q": "milk"})
    assert r.status_code == 200
    assert "milk" in r.text
    assert "/note/" in r.text


async def test_empty_query_returns_no_tiles_no_error(client, seed):
    await login(client)
    r = await client.get("/search", params={"q": "   "})
    assert r.status_code == 200
    assert 'class="note"' not in r.text


async def test_search_page_renders_capture_and_find(client, seed):
    await login(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert 'action="/add"' in r.text
    assert 'name="q"' in r.text


async def test_add_note_source_web(client, storage, seed):
    csrf = await login(client)
    await client.post("/add", data={"text": "remember to water the plants",
                                    "csrf_token": csrf})
    newest = storage.recent_notes(1)[0]
    assert newest["raw"] == "remember to water the plants"
    assert newest["source"] == "web"


async def test_malformed_query_does_not_500(client, seed):
    await login(client)
    for q in ['"', "a AND", "((", "NOT", "*"]:
        r = await client.get("/search", params={"q": q})
        assert r.status_code == 200
