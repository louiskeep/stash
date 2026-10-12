"""Task 5 (storage half): the read-only browse queries added for the web UI."""

from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.storage import Storage


def _mk():
    return Storage.open(":memory:"), FakeEmbedder()


def _add(storage, embedder, text):
    return capture(storage, embedder, text, "web",
                   datetime.now(timezone.utc).isoformat()).note_id


def test_recent_notes_newest_first_and_keyset():
    storage, embedder = _mk()
    ids = [_add(storage, embedder, f"note {i}") for i in range(5)]
    page1 = storage.recent_notes(2)
    assert [r["id"] for r in page1] == [ids[4], ids[3]]
    page2 = storage.recent_notes(2, before_id=page1[-1]["id"])
    assert [r["id"] for r in page2] == [ids[2], ids[1]]
    # No overlap across pages.
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


def test_notes_by_tag_and_counts():
    storage, embedder = _mk()
    _add(storage, embedder, "a #x")
    _add(storage, embedder, "b #x")
    _add(storage, embedder, "c #y")
    assert len(storage.notes_by_tag("x", 10)) == 2
    counts = {r["tag"]: r["n"] for r in storage.tag_counts()}
    assert counts == {"x": 2, "y": 1}


def test_notes_by_category_and_counts():
    storage, embedder = _mk()
    _add(storage, embedder, "a @work")
    _add(storage, embedder, "b @work")
    _add(storage, embedder, "c @home")
    assert len(storage.notes_by_category("work", 10)) == 2
    counts = {r["category"]: r["n"] for r in storage.category_counts()}
    assert counts == {"work": 2, "home": 1}


def test_related_excludes_self_and_handles_missing():
    storage, embedder = _mk()
    a = _add(storage, embedder, "alpha beta")
    _add(storage, embedder, "beta gamma")
    related = storage.related_by_embedding(a, 5)
    assert all(r["id"] != a for r in related)
    # A note id with no embedding row returns [].
    assert storage.related_by_embedding(999999, 5) == []


def test_note_detail_assembles_tags_meta_reminder():
    storage, embedder = _mk()
    rid = _add(storage, embedder, "remind me in 30min to ship #release @work")
    d = storage.note_detail(rid)
    assert d["raw"].startswith("remind me")
    assert "release" in d["tags"]
    assert d["category"] == "work"
    assert d["reminder"] is not None and d["reminder"]["status"] == "pending"
    assert storage.note_detail(999999) is None


def test_list_reminders_joins_raw():
    storage, embedder = _mk()
    _add(storage, embedder, "remind me in 5min to stretch")
    rows = storage.list_reminders(10)
    assert len(rows) == 1
    assert "stretch" in rows[0]["raw"]
    assert rows[0]["status"] == "pending"
