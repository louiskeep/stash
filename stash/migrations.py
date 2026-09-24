"""Numbered, append-only schema migrations. Never edit a shipped migration;
add a new one."""

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE notes (
        id            INTEGER PRIMARY KEY,
        raw           TEXT NOT NULL,
        source        TEXT NOT NULL,
        source_chat_id TEXT,
        source_msg_id TEXT,
        created_at    TEXT NOT NULL,
        derived_at    TEXT
    );
    -- dedupe only when a channel message id exists, scoped to the chat.
    CREATE UNIQUE INDEX ux_notes_source_msg
        ON notes(source, source_chat_id, source_msg_id)
        WHERE source_msg_id IS NOT NULL;

    CREATE TABLE note_tags (
        note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        tag     TEXT NOT NULL
    );
    CREATE INDEX ix_note_tags_tag ON note_tags(tag);

    CREATE TABLE note_meta (
        note_id    INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
        category   TEXT,
        intent     TEXT,
        cluster_id INTEGER
    );

    CREATE TABLE reminders (
        id         INTEGER PRIMARY KEY,
        note_id    INTEGER NOT NULL UNIQUE REFERENCES notes(id) ON DELETE CASCADE,
        fire_at    TEXT NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending',
        channel    TEXT,
        chat_id    TEXT,
        claimed_at TEXT,
        attempts   INTEGER NOT NULL DEFAULT 0,
        sent_at    TEXT
    );
    CREATE INDEX ix_reminders_due ON reminders(status, fire_at);

    CREATE TABLE clusters (
        id         INTEGER PRIMARY KEY,
        label      TEXT,
        size       INTEGER,
        updated_at TEXT
    );

    CREATE TABLE kv (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE VIRTUAL TABLE notes_fts USING fts5(
        raw,
        content='notes',
        content_rowid='id',
        tokenize='unicode61'
    );

    CREATE VIRTUAL TABLE vec_notes USING vec0(
        note_id INTEGER PRIMARY KEY,
        embedding FLOAT[384]
    );
    """),
]
