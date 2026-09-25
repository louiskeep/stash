from datetime import datetime, timezone

from stash.capture import capture, repair
from stash.embed import FakeEmbedder
from stash.recall import search
from stash.storage import Storage


def _now():
    return datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()


def test_plain_note_derives_with_empty_meta(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    r = capture(s, FakeEmbedder(), "just a plain thought", "cli", _now())
    assert r.note_id is not None
    row = s.get_note(r.note_id)
    assert row["derived_at"] is not None
    meta = s.conn.execute(
        "SELECT * FROM note_meta WHERE note_id=?", (r.note_id,)).fetchone()
    assert meta["intent"] == "note"
    assert meta["category"] is None
    tags = s.conn.execute(
        "SELECT tag FROM note_tags WHERE note_id=?", (r.note_id,)).fetchall()
    assert tags == []
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE note_id=?", (r.note_id,)
    ).fetchone()[0] == 0


def test_verbatim_unicode_and_long_text_preserved_and_embedded(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    raw = "emoji 🧠 combine é " + ("x" * 5000) + " #big"
    r = capture(s, FakeEmbedder(), raw, "cli", _now())
    assert s.get_note(r.note_id)["raw"] == raw          # byte-for-byte
    assert s.search_vec(FakeEmbedder().embed(raw), 5)   # embedded, no error


def test_reminder_note_creates_one_reminder(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    r = capture(s, FakeEmbedder(), "remind me to call mom in 30min", "cli", _now())
    rem = s.conn.execute(
        "SELECT fire_at, status FROM reminders WHERE note_id=?", (r.note_id,)
    ).fetchone()
    assert rem["status"] == "pending"
    assert rem["fire_at"] == datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc).isoformat()
    assert r.intent == "reminder"


def test_ambiguous_reminder_time_stores_note_without_reminder(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    r = capture(s, FakeEmbedder(), "remind me to call mom sometime", "cli", _now())
    assert r.note_id is not None
    assert "time" in r.receipt.lower()
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE note_id=?", (r.note_id,)
    ).fetchone()[0] == 0


def test_dedupe_returns_deduped_result(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "x", "telegram", _now(), "c1", "m1")
    r2 = capture(s, FakeEmbedder(), "x", "telegram", _now(), "c1", "m1")
    assert r2.deduped is True
    assert r2.note_id is None


def test_capture_heals_existing_underived_duplicate(tmp_path):
    # The sync CLI path must self-heal too: a raw note written directly
    # (e.g. by a prior capture that crashed before deriving) gets derived
    # when the same (source, chat, msg) key is captured again.
    s = Storage.open(str(tmp_path / "t.db"))
    text = "remind me to call mom in 30min"
    nid = s.add_note(text, "telegram", _now(), "c1", "m1")
    assert s.get_note(nid)["derived_at"] is None

    r = capture(s, FakeEmbedder(), text, "telegram", _now(), "c1", "m1")

    assert r.deduped is True
    assert r.note_id is None
    row = s.get_note(nid)
    assert row["derived_at"] is not None
    results = search(s, FakeEmbedder(), "call mom", limit=5)
    assert any(x.note_id == nid for x in results)
    rem = s.conn.execute(
        "SELECT status FROM reminders WHERE note_id=?", (nid,)).fetchone()
    assert rem is not None and rem["status"] == "pending"


def test_repair_rederives_unfinished_notes_idempotently(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    # Simulate a crash: raw written, derivation never ran.
    nid = s.add_note("remind me in 30min to stretch", "cli", _now())
    assert s.underived_note_ids() == [nid]
    assert repair(s, FakeEmbedder()) == 1
    assert repair(s, FakeEmbedder()) == 0            # idempotent
    assert s.get_note(nid)["derived_at"] is not None
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE note_id=?", (nid,)
    ).fetchone()[0] == 1                              # exactly one, not two
