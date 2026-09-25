import pytest

from stash.telegram_adapter import parse_update, is_authorized, TelegramIngest


def _update(uid, chat_type="private", sender="42", text="hi", chat="1000"):
    return {"update_id": uid, "message": {"message_id": uid,
            "chat": {"id": chat, "type": chat_type},
            "from": {"id": sender}, "text": text}}


def test_parse_update_extracts_fields():
    m = parse_update(_update(7, text="buy milk"))
    assert (m.update_id, m.chat_type, m.sender_id, m.text) == (7, "private", "42", "buy milk")


def test_parse_update_none_for_non_text():
    assert parse_update({"update_id": 8}) is None
    assert parse_update({"update_id": 9, "message": {"chat": {"id": "1", "type": "private"}}}) is None


def test_authorized_requires_private_and_allowlist():
    allowed = ("42",)
    assert is_authorized(parse_update(_update(1)), allowed) is True
    assert is_authorized(parse_update(_update(2, sender="99")), allowed) is False
    assert is_authorized(parse_update(_update(3, chat_type="group")), allowed) is False


@pytest.mark.asyncio
async def test_poll_sorts_updates_by_update_id():
    """Test that poll returns messages in ascending update_id order even if API response is shuffled."""
    class FakeClient:
        async def get_updates(self, offset):
            # Return updates out of order to test sorting
            return [
                _update(5),
                _update(1),
                _update(3),
                _update(2),
                _update(4),
            ]

    ingest = TelegramIngest(FakeClient())
    result = await ingest.poll(0)
    update_ids = [m.update_id for m in result]
    assert update_ids == [1, 2, 3, 4, 5]
