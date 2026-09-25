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


async def test_plain_text_is_captured_and_acked(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    reply = await handle_message(s, FakeEmbedder(), _msg("buy milk #home"), _now())
    assert "stashed" in reply.lower()
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


async def test_note_starting_with_unknown_slash_is_captured_verbatim(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    await handle_message(s, FakeEmbedder(), _msg("/todo call the vendor"), _now())
    row = s.conn.execute("SELECT raw FROM notes").fetchone()
    assert row["raw"] == "/todo call the vendor"


async def test_find_returns_matches(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    await handle_message(s, FakeEmbedder(), _msg("the quick brown fox"), _now())
    reply = await handle_message(s, FakeEmbedder(), _msg("/find brown fox"), _now())
    assert "brown fox" in reply


async def test_find_empty_and_no_hits(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    reply = await handle_message(s, FakeEmbedder(), _msg("/find zzz"), _now())
    assert "nothing" in reply.lower()


async def test_help_and_empty(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    help_reply = await handle_message(s, FakeEmbedder(), _msg("/help"), _now())
    assert "/find" in help_reply
    empty_reply = await handle_message(s, FakeEmbedder(), _msg("   "), _now())
    assert empty_reply is None


async def test_find_with_leading_whitespace(tmp_path):
    """Test that leading whitespace in /find doesn't corrupt the query."""
    s = Storage.open(str(tmp_path / "t.db"))
    await handle_message(s, FakeEmbedder(), _msg("the quick brown fox"), _now())
    reply = await handle_message(s, FakeEmbedder(), _msg("   /find brown"), _now())
    assert "brown" in reply


async def test_plain_text_with_leading_whitespace_captured_verbatim(tmp_path):
    """Test that plain text with leading whitespace is captured unchanged."""
    s = Storage.open(str(tmp_path / "t.db"))
    original_text = "   buy milk with leading spaces"
    await handle_message(s, FakeEmbedder(), _msg(original_text), _now())
    row = s.conn.execute("SELECT raw FROM notes").fetchone()
    assert row["raw"] == original_text
