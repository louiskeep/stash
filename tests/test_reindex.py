from datetime import datetime, timezone

import pytest

from stash.capture import capture, repair
from stash.embed import FakeEmbedder
from stash.recall import search
from stash.reindex import reindex
from stash.storage import Storage


def _now():
    return datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()


class _BoomEmbedder:
    """Fails every embed() call, simulating a reindex interrupted mid-loop."""

    def embed(self, text):
        raise RuntimeError("embedder boom")


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


def test_interrupted_reindex_marks_notes_underived_so_repair_recovers(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "the quick brown fox", "cli", _now())

    with pytest.raises(RuntimeError):
        reindex(s, _BoomEmbedder())

    # The note must be marked underived (not stranded with a stale
    # derived_at), so repair() can find and heal it.
    assert s.underived_note_ids() != []

    repaired = repair(s, FakeEmbedder())
    assert repaired == 1

    results = search(s, FakeEmbedder(), "brown fox")
    assert [r.raw for r in results] == ["the quick brown fox"]  # raw text intact
