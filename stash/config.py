"""Configuration loading for stash. No secrets are ever logged."""

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
    return Config(
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
