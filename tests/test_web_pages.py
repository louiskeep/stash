"""Task 5: Timeline, Topics, Note detail, Reminders pages + escaping."""

from tests.conftest import login


def test_timeline_newest_first(client, seed, storage):
    login(client)
    r = client.get("/timeline")
    assert r.status_code == 200
    # Newest seeded note ("idea: a second brain...") appears before the oldest.
    idea = r.text.find("second brain")
    milk = r.text.find("buy milk")
    assert idea != -1 and milk != -1 and idea < milk


def test_timeline_pagination(client, storage, embedder):
    from datetime import datetime, timezone
    from stash.capture import capture
    for i in range(5):
        capture(storage, embedder, f"note number {i}", "web",
                datetime.now(timezone.utc).isoformat())
    login(client)
    # Page of 2 via the storage layer directly to assert keyset continuity.
    page1 = storage.recent_notes(2)
    page2 = storage.recent_notes(2, before_id=page1[-1]["id"])
    ids1 = {n["id"] for n in page1}
    ids2 = {n["id"] for n in page2}
    assert ids1 and ids2 and ids1.isdisjoint(ids2)
    assert max(ids2) < min(ids1)  # strictly older


def test_topics_lists_tag_and_drilldown(client, seed):
    login(client)
    r = client.get("/topics")
    assert r.status_code == 200
    assert "groceries" in r.text
    r2 = client.get("/topics/tag/groceries")
    assert r2.status_code == 200
    assert "buy milk" in r2.text


def test_note_detail_shows_reminder(client, storage, embedder):
    from datetime import datetime, timezone
    from stash.capture import capture
    res = capture(storage, embedder, "remind me in 30min to stretch", "web",
                  datetime.now(timezone.utc).isoformat())
    login(client)
    r = client.get(f"/note/{res.note_id}")
    assert r.status_code == 200
    assert "pending" in r.text  # reminder status rendered


def test_note_detail_404_for_missing(client, seed):
    login(client)
    assert client.get("/note/999999").status_code == 404


def test_related_excludes_self(storage, embedder):
    from datetime import datetime, timezone
    from stash.capture import capture
    ids = []
    for t in ["alpha beta gamma", "beta gamma delta", "unrelated xyzzy"]:
        ids.append(capture(storage, embedder, t, "web",
                           datetime.now(timezone.utc).isoformat()).note_id)
    related = storage.related_by_embedding(ids[0], 5)
    assert all(row["id"] != ids[0] for row in related)


def test_escaping_on_all_surfaces(client, storage, embedder):
    from datetime import datetime, timezone
    from stash.capture import capture
    capture(storage, embedder, "<script>alert('xss')</script>", "web",
            datetime.now(timezone.utc).isoformat())
    login(client)
    for path in ["/timeline", "/search?q=script"]:
        r = client.get(path)
        assert "<script>alert('xss')</script>" not in r.text
    # Detail too.
    nid = storage.recent_notes(1)[0]["id"]
    r = client.get(f"/note/{nid}")
    assert "<script>alert('xss')</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_reminders_page(client, storage, embedder):
    from datetime import datetime, timezone
    from stash.capture import capture
    capture(storage, embedder, "remind me in 10min to call back", "web",
            datetime.now(timezone.utc).isoformat())
    login(client)
    r = client.get("/reminders")
    assert r.status_code == 200
    assert "call back" in r.text
