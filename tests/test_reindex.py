from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.recall import search
from stash.reindex import reindex
from stash.storage import Storage


def _now():
    return datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()


def test_reindex_preserves_search_and_reminders(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "the quick brown fox", "cli", _now())
    capture(s, FakeEmbedder(), "remind me in 30min to stretch", "cli", _now())
    s.kv_set("offset", "77")
    before = [r.note_id for r in search(s, FakeEmbedder(), "brown fox")]

    reindex(s, FakeEmbedder())

    after = [r.note_id for r in search(s, FakeEmbedder(), "brown fox")]
    assert after == before                       # derived rebuilt identically
    assert s.kv_get("offset") == "77"            # kv preserved
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 1
