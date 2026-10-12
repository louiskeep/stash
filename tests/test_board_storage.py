"""Task 0: board layout storage (durable, reindex-safe, cascades)."""

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
    a = capture(s, e, "invoice from the vendor @work #billing", _now_src(), _now()).note_id
    b = capture(s, e, "gym plan for the week #fitness", _now_src(), _now()).note_id
    return s, e, a, b


def _now_src():
    return "web"


def test_board_notes_includes_position_and_tags():
    s, e, a, b = _mk()
    s.save_positions([{"note_id": a, "x": 10.0, "y": 20.0, "z": 1}])
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["x"] == 10.0 and notes[a]["y"] == 20.0 and notes[a]["z"] == 1
    assert "billing" in notes[a]["tags"] and notes[a]["category"] == "work"
    # An unpositioned note comes back with x/y None.
    assert notes[b]["x"] is None


def test_board_notes_includes_pending_timer():
    s = Storage.open(":memory:")
    e = FakeEmbedder()
    nid = capture(s, e, "remind me in 30min to stretch", "web", _now()).note_id
    note = next(n for n in s.board_notes() if n["id"] == nid)
    assert note["timer"] is not None  # pending reminder fire_at surfaced


def test_save_positions_ignores_unknown_note():
    s, e, a, b = _mk()
    written = s.save_positions([
        {"note_id": a, "x": 1, "y": 2, "z": 0},
        {"note_id": 999999, "x": 3, "y": 4, "z": 0},  # unknown: ignored
    ])
    assert written == 1


def test_reindex_preserves_board_layout():
    s, e, a, b = _mk()
    s.save_positions([{"note_id": a, "x": 5.0, "y": 6.0, "z": 2}])
    reindex(s, e)
    note = next(n for n in s.board_notes() if n["id"] == a)
    assert note["x"] == 5.0 and note["y"] == 6.0 and note["z"] == 2


def test_delete_note_cascades_layout():
    s, e, a, b = _mk()
    s.save_positions([{"note_id": a, "x": 1, "y": 1, "z": 0}])
    assert s.delete_note(a) is True
    ids = {n["id"] for n in s.board_notes()}
    assert a not in ids
    # Layout row gone (cascade); the other note is untouched.
    assert b in ids
    row = s.conn.execute("SELECT COUNT(*) AS n FROM board_layout WHERE note_id=?", (a,)).fetchone()
    assert row["n"] == 0


def test_save_piles_assigns_and_persists():
    s, e, a, b = _mk()
    mapping = s.save_piles(
        piles=[{"client_id": "p1", "label": "vendor stuff", "collapsed": False}],
        assignments={a: "p1", b: "p1"},
    )
    pid = mapping["p1"]
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["pile_id"] == pid and notes[b]["pile_id"] == pid
    piles = {p["id"]: p for p in s.board_piles()}
    assert piles[pid]["label"] == "vendor stuff"


def test_save_piles_rebuild_detaches_old():
    s, e, a, b = _mk()
    s.save_piles([{"client_id": "p1", "label": "x", "collapsed": False}], {a: "p1", b: "p1"})
    # Rebuild with no piles: both notes detach.
    s.save_piles([], {})
    notes = {n["id"]: n for n in s.board_notes()}
    assert notes[a]["pile_id"] is None and notes[b]["pile_id"] is None
    assert s.board_piles() == []
