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


def test_reminder_max_attempts_must_be_positive():
    with pytest.raises(ValueError, match="reminder_max_attempts must be >= 1"):
        load_config({"STASH_REMINDER_MAX_ATTEMPTS": "0"})


def test_reminder_lease_seconds_must_be_positive():
    with pytest.raises(ValueError, match="reminder_lease_seconds must be >= 1"):
        load_config({"STASH_REMINDER_LEASE_SECONDS": "0"})


def test_scheduler_tick_seconds_must_be_positive():
    with pytest.raises(ValueError, match="scheduler_tick_seconds must be >= 1"):
        load_config({"STASH_SCHEDULER_TICK_SECONDS": "0"})


def test_defaults_pass_validation():
    # Ensure that a default config (with all default values) still succeeds
    c = load_config({})
    assert c.reminder_max_attempts == 5
    assert c.reminder_lease_seconds == 120
    assert c.scheduler_tick_seconds == 30
