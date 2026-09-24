"""Rebuild derived data from raw notes. Never touches reminders or kv."""

from stash.capture import derive


def reindex(storage, embedder) -> None:
    conn = storage.conn
    with conn:
        conn.execute("DELETE FROM note_tags")
        conn.execute("DELETE FROM note_meta")
        conn.execute("DELETE FROM vec_notes")
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('delete-all')")
    # Re-derive each note. derive() re-inserts tags/meta/fts/vec and, because
    # reminders are unique per note with ON CONFLICT DO NOTHING, does not create
    # duplicate reminders or disturb existing reminder status.
    for row in conn.execute("SELECT id, raw, created_at FROM notes ORDER BY id"):
        derive(storage, embedder, row["id"], row["raw"], row["created_at"])
