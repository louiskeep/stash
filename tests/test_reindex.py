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


def test_reindex_clears_clusters(tmp_path):
    # Clusters are derived data; a reindex must leave no stale cluster rows or
    # assignments behind (recluster recomputes them separately).
    s = Storage.open(str(tmp_path / "c.db"))
    nid = capture(s, FakeEmbedder(), "a note #x", "cli", _now()).note_id
    # Simulate a prior recluster: one cluster, this note assigned to it.
    s.conn.execute("INSERT INTO clusters(id, label, size, updated_at) VALUES (1,'topic',1,?)",
                   (_now(),))
    s.conn.execute("UPDATE note_meta SET cluster_id = 1 WHERE note_id = ?", (nid,))
    s.conn.commit()
    assert s.cluster_topics()  # present before

    reindex(s, FakeEmbedder())

    assert s.cluster_topics() == []
    assert s.conn.execute(
        "SELECT COUNT(*) AS n FROM note_meta WHERE cluster_id IS NOT NULL"
    ).fetchone()["n"] == 0
    # The note itself survived the reindex.
    assert s.get_note(nid)["raw"] == "a note #x"


def test_reindex_preserves_board_layout(tmp_path):
    # Board layout is durable state; reindex must not touch it.
    s = Storage.open(str(tmp_path / "board.db"))
    nid = capture(s, FakeEmbedder(), "a note", "web", _now()).note_id
    s.save_positions([{"note_id": nid, "x": 7.0, "y": 8.0, "z": 3}])
    reindex(s, FakeEmbedder())
    note = next(n for n in s.board_notes() if n["id"] == nid)
    assert note["x"] == 7.0 and note["y"] == 8.0 and note["z"] == 3
