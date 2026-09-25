from datetime import datetime, timezone

import pytest

from stash.config import Config
from stash.serve import acquire_single_instance, handle_updates, serve, _validate_config
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


async def test_failed_reply_send_still_stores_note_and_advances_offset(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))

    class AlwaysFailingDelivery:
        async def send(self, chat_id, text):
            raise RuntimeError("telegram is down")

    d = AlwaysFailingDelivery()
    ing = FakeIngest([[_msg(5, "buy milk")]])
    # Must not raise out of handle_updates: the failed receipt is best-effort.
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    assert s.kv_get("tg_offset") == "6"  # update_id + 1, offset still advances


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


def _cfg(tmp_path, **overrides):
    kwargs = dict(
        db_path=str(tmp_path / "t.db"),
        embed_model="fake",
        web_bind="127.0.0.1",
        web_port=8000,
        web_password=None,
        web_session_secret=None,
        telegram_bot_token="test-token",
        allowed_sender_ids=("42",),
        scheduler_tick_seconds=30,
        reminder_lease_seconds=120,
        reminder_max_attempts=5,
        busy_timeout_ms=5000,
    )
    kwargs.update(overrides)
    return Config(**kwargs)


def test_validate_config_raises_when_token_missing(tmp_path):
    with pytest.raises(RuntimeError, match="STASH_TELEGRAM_BOT_TOKEN"):
        _validate_config(_cfg(tmp_path, telegram_bot_token=None))


def test_validate_config_raises_when_allowlist_empty(tmp_path):
    with pytest.raises(RuntimeError, match="STASH_ALLOWED_SENDER_IDS"):
        _validate_config(_cfg(tmp_path, allowed_sender_ids=()))


async def test_serve_raises_before_touching_db_when_misconfigured(tmp_path):
    # Validation runs before acquire_single_instance/Storage.open, so a
    # misconfigured daemon fails fast without creating a lock file or a DB.
    db_path = tmp_path / "t.db"
    with pytest.raises(RuntimeError, match="STASH_TELEGRAM_BOT_TOKEN"):
        await serve(_cfg(tmp_path, telegram_bot_token=None))
    assert not db_path.exists()
    assert not (tmp_path / "t.db.serve.lock").exists()
