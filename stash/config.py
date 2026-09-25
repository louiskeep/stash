"""Configuration loading for stash. No secrets are ever logged."""

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Config:
    db_path: str
    embed_model: str
    web_bind: str
    web_port: int
    web_password: str | None
    web_session_secret: str | None
    telegram_bot_token: str | None
    allowed_sender_ids: tuple[str, ...]
    scheduler_tick_seconds: int
    reminder_lease_seconds: int
    reminder_max_attempts: int
    busy_timeout_ms: int


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def load_config(env: Mapping[str, str]) -> Config:
    ids_raw = env.get("STASH_ALLOWED_SENDER_IDS", "")
    ids = tuple(part.strip() for part in ids_raw.split(",") if part.strip())
    config = Config(
        db_path=env.get("STASH_DB_PATH", "stash.db"),
        embed_model=env.get("STASH_EMBED_MODEL", "all-MiniLM-L6-v2"),
        web_bind=env.get("STASH_WEB_BIND", "127.0.0.1"),
        web_port=_int(env, "STASH_WEB_PORT", 8000),
        web_password=env.get("STASH_WEB_PASSWORD") or None,
        web_session_secret=env.get("STASH_WEB_SESSION_SECRET") or None,
        telegram_bot_token=env.get("STASH_TELEGRAM_BOT_TOKEN") or None,
        allowed_sender_ids=ids,
        scheduler_tick_seconds=_int(env, "STASH_SCHEDULER_TICK_SECONDS", 30),
        reminder_lease_seconds=_int(env, "STASH_REMINDER_LEASE_SECONDS", 120),
        reminder_max_attempts=_int(env, "STASH_REMINDER_MAX_ATTEMPTS", 5),
        busy_timeout_ms=_int(env, "STASH_BUSY_TIMEOUT_MS", 5000),
    )

    # Validate that scheduler config values are positive
    if config.reminder_max_attempts < 1:
        raise ValueError(f"reminder_max_attempts must be >= 1, got {config.reminder_max_attempts}")
    if config.reminder_lease_seconds < 1:
        raise ValueError(f"reminder_lease_seconds must be >= 1, got {config.reminder_lease_seconds}")
    if config.scheduler_tick_seconds < 1:
        raise ValueError(f"scheduler_tick_seconds must be >= 1, got {config.scheduler_tick_seconds}")

    return config


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    A pre-existing environment variable always wins: a key already in
    os.environ is left untouched. Blank lines and lines starting with '#'
    are skipped; a value may be wrapped in matching single or double quotes,
    which are stripped. A line that isn't KEY=VALUE is ignored rather than
    raising, so a stray or malformed line doesn't block startup.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if not key or key in os.environ:
                continue
            os.environ[key] = value
