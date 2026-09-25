from datetime import datetime, timezone
from stash.commands import handle_message
from stash.embed import FakeEmbedder
from stash.ports import IncomingMessage
from stash.storage import Storage


def _msg(text, uid=1):
    return IncomingMessage(update_id=uid, chat_id="c1", chat_type="private",
                           sender_id="s1", msg_id=str(uid), text=text)


def _now():
    return datetime(2026, 9, 25, tzinfo=timezone.utc).isoformat()


def test_plain_text_is_captured_and_acked(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    reply = handle_message(s, FakeEmbedder(), _msg("buy milk #home"), _now())
    assert "stashed" in reply.lower()
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_note_starting_with_unknown_slash_is_captured_verbatim(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    handle_message(s, FakeEmbedder(), _msg("/todo call the vendor"), _now())
    row = s.conn.execute("SELECT raw FROM notes").fetchone()
    assert row["raw"] == "/todo call the vendor"


def test_find_returns_matches(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    handle_message(s, FakeEmbedder(), _msg("the quick brown fox"), _now())
    reply = handle_message(s, FakeEmbedder(), _msg("/find brown fox"), _now())
    assert "brown fox" in reply


def test_find_empty_and_no_hits(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert "nothing" in handle_message(s, FakeEmbedder(), _msg("/find zzz"), _now()).lower()


def test_help_and_empty(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert "/find" in handle_message(s, FakeEmbedder(), _msg("/help"), _now())
    assert handle_message(s, FakeEmbedder(), _msg("   "), _now()) is None
