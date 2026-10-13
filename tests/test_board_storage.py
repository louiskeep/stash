"""Board storage: merge groups (durable, reindex-safe, cascades) and tags."""

from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.reindex import reindex
from stash.storage import Storage


def _now():
    return datetime.now(timezone.utc).isoformat()


def _mk():
    s = Storage.open(":memory:")
    e = FakeEmbedder()
    a = capture(s, e, "invoice from the vendor @work #billing", "web", _now()).note_id
    b = capture(s, e, "gym plan for the week #fitness", "web", _now()).note_id
    return s, e, a, b


def test_board_notes_includes_group_and_tags():
    s, e, a, b = _mk()
    s.save_groups([[a, b]])
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["pile_id"] is not None
    assert notes[a]["pile_id"] == notes[b]["pile_id"]
    assert notes[a]["seq"] == 0 and notes[b]["seq"] == 1
    assert "billing" in notes[a]["tags"] and notes[a]["category"] == "work"
    # A standalone note comes back ungrouped.
    c = capture(s, e, "a loose thought", "web", _now()).note_id
    assert next(n for n in s.board_notes() if n["id"] == c)["pile_id"] is None


def test_board_notes_includes_pending_timer():
    s = Storage.open(":memory:")
    e = FakeEmbedder()
    nid = capture(s, e, "remind me in 30min to stretch", "web", _now()).note_id
    note = next(n for n in s.board_notes() if n["id"] == nid)
    assert note["timer"] is not None  # pending reminder fire_at surfaced


def test_save_groups_assigns_in_order():
    s, e, a, b = _mk()
    written = s.save_groups([[b, a]])  # order matters: b is the top segment
    assert written == 1
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[b]["seq"] == 0 and notes[a]["seq"] == 1


def test_save_groups_ignores_unknown_and_singletons():
    s, e, a, b = _mk()
    # A group needs >= 2 real notes: an unknown id and a singleton are dropped.
    written = s.save_groups([[a, 999999], [b]])
    assert written == 0
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["pile_id"] is None and notes[b]["pile_id"] is None


def test_merge_never_touches_raw_notes():
    # The core invariant: merging is a grouping, never a rewrite or delete.
    s, e, a, b = _mk()
    before = {n["id"]: n["raw"] for n in (s.get_note(a), s.get_note(b))}
    s.save_groups([[a, b]])
    assert s.get_note(a)["raw"] == before[a]
    assert s.get_note(b)["raw"] == before[b]
    # Both rows still exist after the merge.
    assert s.get_note(a) is not None and s.get_note(b) is not None


def test_save_groups_disjoint_across_groups():
    # A note named in two groups lands in only one; the other group loses it and,
    # left with a single real member, is dropped.
    s, e, a, b = _mk()
    c = capture(s, e, "third note", "web", _now()).note_id
    written = s.save_groups([[a, b], [b, c]])  # b appears twice
    notes = {n["id"]: n for n in s.board_notes()}
    # First group keeps a+b together; c is left alone, so its group is dropped.
    assert notes[a]["pile_id"] == notes[b]["pile_id"] is not None
    assert notes[c]["pile_id"] is None
    assert written == 1


def test_save_groups_rebuild_detaches_old():
    s, e, a, b = _mk()
    s.save_groups([[a, b]])
    s.save_groups([])  # snip everything apart
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["pile_id"] is None and notes[b]["pile_id"] is None
    assert s.conn.execute("SELECT COUNT(*) AS n FROM board_piles").fetchone()["n"] == 0


def test_reindex_preserves_groups():
    # Merge grouping is durable state; a reindex must not touch it.
    s, e, a, b = _mk()
    s.save_groups([[a, b]])
    reindex(s, e)
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["pile_id"] is not None
    assert notes[a]["pile_id"] == notes[b]["pile_id"]
    assert notes[a]["seq"] == 0 and notes[b]["seq"] == 1
    # And the raw notes survived untouched.
    assert s.get_note(a)["raw"].startswith("invoice from the vendor")


def test_delete_note_cascades_layout():
    s, e, a, b = _mk()
    s.save_groups([[a, b]])
    assert s.delete_note(a) is True
    ids = {n["id"] for n in s.board_notes()}
    assert a not in ids and b in ids
    row = s.conn.execute("SELECT COUNT(*) AS n FROM board_layout WHERE note_id=?", (a,)).fetchone()
    assert row["n"] == 0
