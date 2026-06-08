from stash.parse import parse


def test_preserves_raw_text_verbatim():
    raw = "Sapiens #history great on the cognitive revolution"
    assert parse(raw).raw == raw


def test_extracts_hashtags_in_order():
    note = parse("Sapiens #history #books cognitive revolution")
    assert note.tags == ["history", "books"]


def test_no_hashtags_gives_empty_list():
    assert parse("just a plain thought").tags == []


def test_extracts_category_from_first_at_token():
    assert parse("call mom @family before friday").category == "family"


def test_category_is_none_when_absent():
    assert parse("no category here").category is None


def test_reminder_intent_with_minutes_no_space():
    note = parse("remind me to call mom in 30min")
    assert note.intent == "reminder"
    assert note.remind_in_seconds == 1800


def test_reminder_intent_with_spaced_hours():
    note = parse("remind me in 2 hours to stretch")
    assert note.intent == "reminder"
    assert note.remind_in_seconds == 7200


def test_plain_note_defaults_to_note_intent():
    note = parse("Sapiens #history great book")
    assert note.intent == "note"
    assert note.remind_in_seconds is None
