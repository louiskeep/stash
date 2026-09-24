import struct

from stash.storage import Storage


def _vec(*xs):
    return list(xs) + [0.0] * (384 - len(xs))


def test_add_and_get_note_roundtrip(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("hello #world", "cli", "2026-09-24T00:00:00+00:00")
    row = s.get_note(nid)
    assert row["raw"] == "hello #world"
    assert row["source"] == "cli"
    assert row["derived_at"] is None


def test_scoped_dedupe_only_when_msg_id_present(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    a = s.add_note("x", "telegram", "t0", source_chat_id="c1", source_msg_id="m1")
    dup = s.add_note("x", "telegram", "t1", source_chat_id="c1", source_msg_id="m1")
    other_chat = s.add_note("x", "telegram", "t2", source_chat_id="c2", source_msg_id="m1")
    assert a is not None
    assert dup is None                # same chat + msg id -> deduped
    assert other_chat is not None     # same msg id, different chat -> distinct


def test_cli_notes_with_null_msg_id_are_distinct(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    a = s.add_note("note one", "cli", "t0")
    b = s.add_note("note two", "cli", "t1")
    assert a is not None and b is not None and a != b


def test_underived_note_ids_and_mark(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("plain", "cli", "t0")
    assert s.underived_note_ids() == [nid]
    s.mark_derived(nid, "2026-09-24T00:00:01+00:00")
    assert s.underived_note_ids() == []


def test_bm25_and_vec_search_find_the_note(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("the quick brown fox", "cli", "t0")
    s.set_embedding(nid, _vec(1.0, 0.0))
    assert nid in s.search_bm25("brown fox", 5)
    assert s.search_vec(_vec(1.0, 0.0), 5)[0] == nid


def test_reembedding_same_note_syncs_fts_without_duplicates(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("the quick brown fox", "cli", "t0")
    s.set_embedding(nid, _vec(1.0, 0.0))
    s.set_embedding(nid, _vec(0.0, 1.0))  # simulate re-embedding on reindex
    results = s.search_bm25("brown fox", 5)
    assert results.count(nid) == 1


def test_kv_roundtrip(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert s.kv_get("offset") is None
    s.kv_set("offset", "42")
    assert s.kv_get("offset") == "42"
