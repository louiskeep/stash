"""SQLite connection setup and migration runner for stash."""

import sqlite3

import sqlite_vec

from stash.migrations import MIGRATIONS


def connect(db_path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def _applied(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return {r["version"] for r in conn.execute(
        "SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection) -> None:
    from datetime import datetime, timezone

    done = _applied(conn)
    for version, sql in MIGRATIONS:
        if version in done:
            continue
        with conn:  # one transaction per migration
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
