"""Numbered, append-only schema migrations. Never edit a shipped migration;
add a new one."""

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS notes (
        id            INTEGER PRIMARY KEY,
        raw           TEXT NOT NULL,
        source        TEXT NOT NULL,
        source_chat_id TEXT,
        source_msg_id TEXT,
        created_at    TEXT NOT NULL,
        derived_at    TEXT
    );
    -- dedupe only when a channel message id exists, scoped to the chat.
    CREATE UNIQUE INDEX IF NOT EXISTS ux_notes_source_msg
        ON notes(source, source_chat_id, source_msg_id)
        WHERE source_msg_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS note_tags (
        note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        tag     TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_note_tags_tag ON note_tags(tag);

    CREATE TABLE IF NOT EXISTS note_meta (
        note_id    INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
        category   TEXT,
        intent     TEXT,
        cluster_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS reminders (
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
    CREATE INDEX IF NOT EXISTS ix_reminders_due ON reminders(status, fire_at);

    CREATE TABLE IF NOT EXISTS clusters (
        id         INTEGER PRIMARY KEY,
        label      TEXT,
        size       INTEGER,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS kv (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
        raw,
        content='notes',
        content_rowid='id',
        tokenize='unicode61'
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS vec_notes USING vec0(
        note_id INTEGER PRIMARY KEY,
        embedding FLOAT[384]
    );
    """),
    (2, """
    -- Board layout: the user's spatial arrangement of notes. Durable state,
    -- NOT derived -- a reindex never touches it. A deleted note cascades its
    -- layout row away; a deleted pile detaches its members (SET NULL) rather
    -- than deleting the notes.
    CREATE TABLE IF NOT EXISTS board_piles (
        id         INTEGER PRIMARY KEY,
        label      TEXT,
        collapsed  INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS board_layout (
        note_id    INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
        x          REAL,
        y          REAL,
        z          INTEGER NOT NULL DEFAULT 0,
        pile_id    INTEGER REFERENCES board_piles(id) ON DELETE SET NULL,
        updated_at TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_board_layout_pile ON board_layout(pile_id);
    """),
    (3, """
    -- Saved boards: named filter sets the user configures and recalls. Durable
    -- state, NOT derived -- a reindex never touches it. `config` is a JSON string
    -- of the filter set (category, tag, from, to, search, order).
    CREATE TABLE IF NOT EXISTS boards (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        config     TEXT NOT NULL,
        position   INTEGER,
        created_at TEXT
    );
    """),
    (4, """
    -- W8: the board is an ordered grid with merged notes (groups) rather than a
    -- free-drag canvas with piles. A merged card is a group of notes shown in
    -- order; `seq` is a note's place within its group (0 = top). The raw notes
    -- are never combined or rewritten -- a merge is a reversible grouping, so
    -- this is durable state a reindex must preserve, exactly like pile_id.
    -- Free-canvas x/y are no longer written; old rows keep their values harmlessly.
    --
    -- The former "piles" are retired: a loose stack cannot be faithfully mapped
    -- to an ordered merged card (no per-note order was stored), so we start the
    -- new model clean rather than inventing an arbitrary order. These two resets
    -- are idempotent and come first, so a crash-recovery re-run (which fails on
    -- the ALTER with "duplicate column name") still leaves a consistent state.
    UPDATE board_layout SET pile_id = NULL;
    DELETE FROM board_piles;
    ALTER TABLE board_layout ADD COLUMN seq INTEGER NOT NULL DEFAULT 0;
    """),
]
