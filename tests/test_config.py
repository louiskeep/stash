import pytest
from stash.config import load_config


def test_defaults_apply_when_env_empty():
    c = load_config({})
    assert c.db_path == "stash.db"
    assert c.embed_model == "all-MiniLM-L6-v2"
    assert c.web_bind == "127.0.0.1"
    assert c.web_port == 8000
    assert c.scheduler_tick_seconds == 30
    assert c.reminder_lease_seconds == 120
    assert c.reminder_max_attempts == 5
    assert c.busy_timeout_ms == 5000
    assert c.allowed_sender_ids == ()


def test_allowed_sender_ids_split_and_trimmed():
    c = load_config({"STASH_ALLOWED_SENDER_IDS": "111, 222 ,333"})
    assert c.allowed_sender_ids == ("111", "222", "333")


def test_port_must_be_int():
    with pytest.raises(ValueError):
        load_config({"STASH_WEB_PORT": "notanumber"})
