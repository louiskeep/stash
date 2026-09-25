from datetime import datetime, timezone

import pytest

from stash.serve import acquire_single_instance, handle_updates
from stash.embed import FakeEmbedder
from stash.ports import IncomingMessage, FakeAsyncDelivery
from stash.storage import Storage


class FakeIngest:
    def __init__(self, batches):
        self._batches = list(batches)  # list of lists of IncomingMessage

    async def poll(self, offset):
        return self._batches.pop(0) if self._batches else []


def _msg(uid, text, chat_type="private", sender="42"):
    return IncomingMessage(uid, "1000", chat_type, sender, str(uid), text)


def _now():
    return datetime(2026, 9, 25, tzinfo=timezone.utc).isoformat()


async def test_authorized_capture_and_offset_advance(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    d = FakeAsyncDelivery()
    ing = FakeIngest([[_msg(5, "buy milk")]])
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    assert s.kv_get("tg_offset") == "6"          # update_id + 1
    assert len(d.sent) == 1                       # receipt sent


async def test_group_message_dropped_but_offset_advances(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    d = FakeAsyncDelivery()
    ing = FakeIngest([[_msg(7, "/find secret", chat_type="group")]])
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    assert d.sent == []                           # no leak to the group
    assert s.kv_get("tg_offset") == "8"           # still advanced past it


async def test_dedupe_on_redelivered_update(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    d = FakeAsyncDelivery()
    # same message delivered in two polls (simulates a crash before ack)
    ing = FakeIngest([[_msg(5, "buy milk")], [_msg(5, "buy milk")]])
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_acquire_single_instance_raises_on_second_acquire(tmp_path):
    db_path = str(tmp_path / "t.db")
    first = acquire_single_instance(db_path)
    try:
        with pytest.raises(RuntimeError):
            acquire_single_instance(db_path)
    finally:
        first.close()
