"""Task 5: Timeline, Topics, Note detail, Reminders pages + escaping."""

from datetime import datetime, timezone

from stash.capture import capture
from tests.conftest import login


def _now():
    return datetime.now(timezone.utc).isoformat()


async def test_timeline_newest_first(client, seed):
    await login(client)
    r = await client.get("/timeline")
    assert r.status_code == 200
    idea = r.text.find("second brain")
    milk = r.text.find("buy milk")
    assert idea != -1 and milk != -1 and idea < milk


async def test_timeline_pagination(client, storage, embedder):
    for i in range(5):
        capture(storage, embedder, f"note number {i}", "web", _now())
    await login(client)
    page1 = storage.recent_notes(2)
    page2 = storage.recent_notes(2, before_id=page1[-1]["id"])
    ids1 = {n["id"] for n in page1}
    ids2 = {n["id"] for n in page2}
    assert ids1 and ids2 and ids1.isdisjoint(ids2)
    assert max(ids2) < min(ids1)


async def test_topics_lists_tag_and_drilldown(client, seed):
    await login(client)
    r = await client.get("/topics")
    assert r.status_code == 200
    assert "groceries" in r.text
    r2 = await client.get("/topics/tag/groceries")
    assert r2.status_code == 200
    assert "buy milk" in r2.text


async def test_note_detail_shows_reminder(client, storage, embedder):
    res = capture(storage, embedder, "remind me in 30min to stretch", "web", _now())
    await login(client)
    r = await client.get(f"/note/{res.note_id}")
    assert r.status_code == 200
    assert "pending" in r.text


async def test_note_detail_404_for_missing(client, seed):
    await login(client)
    assert (await client.get("/note/999999")).status_code == 404


async def test_related_excludes_self(storage, embedder):
    ids = []
    for t in ["alpha beta gamma", "beta gamma delta", "unrelated xyzzy"]:
        ids.append(capture(storage, embedder, t, "web", _now()).note_id)
    related = storage.related_by_embedding(ids[0], 5)
    assert all(row["id"] != ids[0] for row in related)


async def test_escaping_on_all_surfaces(client, storage, embedder):
    capture(storage, embedder, "<script>alert('xss')</script>", "web", _now())
    await login(client)
    for path in ["/timeline", "/search?q=script"]:
        r = await client.get(path)
        assert "<script>alert('xss')</script>" not in r.text
    nid = storage.recent_notes(1)[0]["id"]
    r = await client.get(f"/note/{nid}")
    assert "<script>alert('xss')</script>" not in r.text
    assert "&lt;script&gt;" in r.text


async def test_reminders_page(client, storage, embedder):
    capture(storage, embedder, "remind me in 10min to call back", "web", _now())
    await login(client)
    r = await client.get("/reminders")
    assert r.status_code == 200
    assert "call back" in r.text
