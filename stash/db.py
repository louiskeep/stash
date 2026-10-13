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
    for version, sql in sorted(MIGRATIONS):
        if version in done:
            continue
        # executescript() autocommits DDL; migrations use IF NOT EXISTS (and, for
        # an additive ALTER, order idempotent statements first) so a re-run after
        # a crash in the window below is safe. Run script, record version, then
        # commit so the version row is durable.
        try:
            conn.executescript(sql)
        except sqlite3.OperationalError as e:
            # Recovery path: if a prior run committed the DDL but crashed before
            # its version row did, re-adding a column raises "duplicate column
            # name". That means the migration already applied -- record it and
            # move on. Anything else is a real error.
            if "duplicate column name" not in str(e).lower():
                raise
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
