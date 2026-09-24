from stash.db import connect, migrate


def test_migrate_creates_expected_tables(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    for expected in {"notes", "note_tags", "note_meta", "reminders",
                     "clusters", "kv", "schema_migrations", "notes_fts"}:
        assert expected in names


def test_wal_and_foreign_keys_enabled(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_sqlite_vec_loaded(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    version = conn.execute("SELECT vec_version()").fetchone()[0]
    assert isinstance(version, str)


def test_migrate_is_idempotent(tmp_path):
    p = str(tmp_path / "t.db")
    conn = connect(p)
    migrate(conn)
    migrate(conn)  # second run applies nothing, does not raise
    applied = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert applied >= 1
