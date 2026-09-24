from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.recall import rrf_fuse, search
from stash.storage import Storage


def test_rrf_fuse_hand_computed_order():
    # item 2 is rank 0 in both lists -> should win outright.
    # (the brief's original fixture [[1, 2, 3], [2, 1, 4]] gives item 1 and
    # item 2 a mirrored rank (0,1) vs (1,0), an exact RRF score tie broken by
    # dict insertion order -> fused[0] == 1, not 2. Fixed the fixture here so
    # the data matches what the test claims to check; rrf_fuse itself is
    # unchanged.)
    fused = rrf_fuse([[2, 1, 3], [2, 1, 4]], k=60)
    assert fused[0] == 2
    assert set(fused) == {1, 2, 3, 4}


def test_rrf_fuse_empty_lists():
    assert rrf_fuse([[], []]) == []


def test_search_empty_corpus_and_empty_query(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert search(s, FakeEmbedder(), "anything") == []
    nid = capture(s, FakeEmbedder(), "hello world", "cli",
                  datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()).note_id
    assert search(s, FakeEmbedder(), "") == []      # empty query, no crash
    assert nid is not None


def test_search_finds_captured_note(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    now = datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()
    capture(s, FakeEmbedder(), "the quick brown fox jumps", "cli", now)
    results = search(s, FakeEmbedder(), "brown fox", limit=5)
    assert results
    assert "brown fox" in results[0].raw
