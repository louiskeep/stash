from stash.telegram_adapter import parse_update, is_authorized


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
