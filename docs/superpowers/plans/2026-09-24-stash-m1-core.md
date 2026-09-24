# stash Milestone 1 (CLI-usable core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build stash's backend core so a person can capture notes and recall them through a CLI, with local embeddings, hybrid search, and reminders, no bot or web UI yet.

**Architecture:** One SQLite file (WAL) holds raw notes plus derived search data and durable reminder state. A capture pipeline writes the raw note first then derives tags/embedding/reminder; recall fuses BM25 and vector search with RRF; a scheduler delivers reminders at-least-once through a `Delivery` port. Ingest and delivery are behind narrow ports so the CLI (Milestone 1) and Telegram (Milestone 2) are interchangeable adapters.

**Tech Stack:** Python >=3.10, stdlib `sqlite3` (FTS5 + loadable extensions), `sqlite-vec` (vector search), `sentence-transformers` (`all-MiniLM-L6-v2`), `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-24-stash-design.md` (v2)

## Global Constraints

- Python floor: `requires-python = ">=3.10"` (from spec / existing `pyproject.toml`).
- No generative AI: no LLM, no text generation, no external API calls except the chat transport (not in Milestone 1). The only model is the frozen embedding encoder, inference only.
- The raw note is immutable: `notes.raw` is written once and never updated. Only derived tables and durable reminder state change.
- Data classes stay separate: raw / derived / durable-state. `reindex` rebuilds derived data only; it never alters `reminders` rows, their `status`/`sent_at`, or the `kv` ingest offset.
- Reminder delivery is at-least-once (duplicates possible only after a crash between send and status write). Never promise exactly-once. Never hold a SQLite transaction open across a `Delivery.send` call.
- SQLite opens in WAL mode with a `busy_timeout`; transactions are short.
- Prose and receipts contain no em-dashes (project rule); use period, comma, or colon.
- Embedding dimension is 384 (`all-MiniLM-L6-v2`). The vec table is `FLOAT[384]`.

## Review Focus

- **Verbatim unicode / emoji / very long text:** a note containing emoji, combining characters, and thousands of characters is stored byte-for-byte and still embeds without error (encoder truncates input internally, never the stored raw). Test in Task 4.
- **Plain note (no tag, category, or reminder):** derivation completes with empty tags, null category, `intent = "note"`, and no reminder row. Test in Task 4.
- **Cold start / empty corpus:** searching an empty database, or with an empty query string, returns an empty result list without raising. Test in Task 5.
- **CLI notes have null `msg_id`:** two distinct CLI captures with `source_msg_id = None` are two notes, never collapsed by the dedupe key. Test in Task 2.
- **Concurrent scheduler ticks do not double-claim:** two `run_due` passes over the same due reminder deliver it once (the lease claim is exclusive); a crash after send but before mark re-sends on the next pass (at-least-once, asserted as documented behavior). Test in Task 7.

---

### Task 0: Project setup and config

**Files:**
- Modify: `pyproject.toml` (add runtime dependencies)
- Create: `stash/config.py`
- Create: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` dataclass and `load_config(env: Mapping[str, str]) -> Config` with fields: `db_path: str`, `embed_model: str`, `web_bind: str`, `web_port: int`, `web_password: str | None`, `web_session_secret: str | None`, `telegram_bot_token: str | None`, `allowed_sender_ids: tuple[str, ...]`, `scheduler_tick_seconds: int`, `reminder_lease_seconds: int`, `reminder_max_attempts: int`, `busy_timeout_ms: int`.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, set:

```toml
[project]
name = "stash"
version = "0.0.0"
description = "stash - a second brain that isn't a chatbot (recall-only, no generative LLM)"
requires-python = ">=3.10"
dependencies = [
    "sqlite-vec>=0.1.6",
    "sentence-transformers>=3.0",
]

[dependency-groups]
dev = ["pytest>=8"]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-p no:cacheprovider"
```

Run: `uv sync` (downloads sqlite-vec and sentence-transformers; first sync is slow).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_config.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.config'`

- [ ] **Step 4: Implement config**

```python
# stash/config.py
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
```

- [ ] **Step 5: Create `.env.example`**

```bash
# stash configuration (copy to .env; never commit .env)
STASH_DB_PATH=stash.db
STASH_EMBED_MODEL=all-MiniLM-L6-v2
STASH_WEB_BIND=127.0.0.1
STASH_WEB_PORT=8000
STASH_WEB_PASSWORD=
STASH_WEB_SESSION_SECRET=
STASH_TELEGRAM_BOT_TOKEN=
STASH_ALLOWED_SENDER_IDS=
STASH_SCHEDULER_TICK_SECONDS=30
STASH_REMINDER_LEASE_SECONDS=120
STASH_REMINDER_MAX_ATTEMPTS=5
STASH_BUSY_TIMEOUT_MS=5000
```

Confirm `.env` is gitignored (the repo `.gitignore` already ignores it; add `.env` if missing).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock stash/config.py tests/test_config.py .env.example .gitignore
git commit -m "feat: add runtime deps and config loader"
```

---

### Task 1: Database connection, migrations, and schema

**Files:**
- Create: `stash/db.py`
- Create: `stash/migrations.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `connect(db_path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection` (WAL, foreign keys on, sqlite-vec loaded, `row_factory = sqlite3.Row`).
  - `migrate(conn: sqlite3.Connection) -> None` (applies all pending numbered migrations).
  - `MIGRATIONS: list[tuple[int, str]]` in `migrations.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.db'`

- [ ] **Step 3: Write the migrations**

```python
# stash/migrations.py
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
```

- [ ] **Step 4: Write the connection + migrate runner**

```python
# stash/db.py
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add stash/db.py stash/migrations.py tests/test_db.py
git commit -m "feat: add sqlite connection, migrations, and schema"
```

---

### Task 2: Storage layer (notes, tags, meta, FTS/vec sync, kv)

**Files:**
- Create: `stash/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `stash.db.connect`, `stash.db.migrate`.
- Produces a `Storage` class wrapping a connection:
  - `Storage(conn)` and classmethod `Storage.open(db_path, busy_timeout_ms) -> Storage` (connects + migrates).
  - `add_note(raw, source, created_at, source_chat_id=None, source_msg_id=None) -> int | None` (returns new note id, or `None` if the scoped dedupe key already exists).
  - `get_note(note_id) -> sqlite3.Row | None`.
  - `set_tags(note_id, tags: list[str]) -> None` (replaces).
  - `set_meta(note_id, category, intent, cluster_id=None) -> None` (upsert).
  - `set_embedding(note_id, vector: list[float]) -> None` (replace in `vec_notes` + `notes_fts`).
  - `mark_derived(note_id, when: str) -> None`.
  - `underived_note_ids() -> list[int]` (note ids with `derived_at IS NULL`, ascending).
  - `kv_get(key) -> str | None`, `kv_set(key, value) -> None`.
  - `search_bm25(query, limit) -> list[int]` (note ids best-first).
  - `search_vec(vector, limit) -> list[int]` (note ids nearest-first).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage.py
import struct

from stash.storage import Storage


def _vec(*xs):
    return list(xs) + [0.0] * (384 - len(xs))


def test_add_and_get_note_roundtrip(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("hello #world", "cli", "2026-09-24T00:00:00+00:00")
    row = s.get_note(nid)
    assert row["raw"] == "hello #world"
    assert row["source"] == "cli"
    assert row["derived_at"] is None


def test_scoped_dedupe_only_when_msg_id_present(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    a = s.add_note("x", "telegram", "t0", source_chat_id="c1", source_msg_id="m1")
    dup = s.add_note("x", "telegram", "t1", source_chat_id="c1", source_msg_id="m1")
    other_chat = s.add_note("x", "telegram", "t2", source_chat_id="c2", source_msg_id="m1")
    assert a is not None
    assert dup is None                # same chat + msg id -> deduped
    assert other_chat is not None     # same msg id, different chat -> distinct


def test_cli_notes_with_null_msg_id_are_distinct(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    a = s.add_note("note one", "cli", "t0")
    b = s.add_note("note two", "cli", "t1")
    assert a is not None and b is not None and a != b


def test_underived_note_ids_and_mark(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("plain", "cli", "t0")
    assert s.underived_note_ids() == [nid]
    s.mark_derived(nid, "2026-09-24T00:00:01+00:00")
    assert s.underived_note_ids() == []


def test_bm25_and_vec_search_find_the_note(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    nid = s.add_note("the quick brown fox", "cli", "t0")
    s.set_embedding(nid, _vec(1.0, 0.0))
    assert nid in s.search_bm25("brown fox", 5)
    assert s.search_vec(_vec(1.0, 0.0), 5)[0] == nid


def test_kv_roundtrip(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert s.kv_get("offset") is None
    s.kv_set("offset", "42")
    assert s.kv_get("offset") == "42"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.storage'`

- [ ] **Step 3: Implement storage**

```python
# stash/storage.py
"""Typed storage operations over the stash SQLite schema.

Transactions are short and never span a network call. FTS and vec tables are
kept in sync here on every note write.
"""

import sqlite3
import struct

from stash.db import connect, migrate


def _pack_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class Storage:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, db_path: str, busy_timeout_ms: int = 5000) -> "Storage":
        conn = connect(db_path, busy_timeout_ms)
        migrate(conn)
        return cls(conn)

    def add_note(self, raw, source, created_at,
                 source_chat_id=None, source_msg_id=None) -> int | None:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO notes(raw, source, source_chat_id, source_msg_id,"
                    " created_at, derived_at) VALUES (?,?,?,?,?,NULL)",
                    (raw, source, source_chat_id, source_msg_id, created_at),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # scoped dedupe key already present

    def get_note(self, note_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()

    def set_tags(self, note_id, tags) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
            self.conn.executemany(
                "INSERT INTO note_tags(note_id, tag) VALUES (?,?)",
                [(note_id, t) for t in tags],
            )

    def set_meta(self, note_id, category, intent, cluster_id=None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO note_meta(note_id, category, intent, cluster_id)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(note_id) DO UPDATE SET"
                " category=excluded.category, intent=excluded.intent,"
                " cluster_id=excluded.cluster_id",
                (note_id, category, intent, cluster_id),
            )

    def set_embedding(self, note_id, vector) -> None:
        row = self.get_note(note_id)
        with self.conn:
            # FTS external content: insert by rowid = note id.
            self.conn.execute("DELETE FROM notes_fts WHERE rowid=?", (note_id,))
            self.conn.execute(
                "INSERT INTO notes_fts(rowid, raw) VALUES (?,?)",
                (note_id, row["raw"]),
            )
            self.conn.execute("DELETE FROM vec_notes WHERE note_id=?", (note_id,))
            self.conn.execute(
                "INSERT INTO vec_notes(note_id, embedding) VALUES (?,?)",
                (note_id, _pack_f32(vector)),
            )

    def mark_derived(self, note_id, when) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET derived_at=? WHERE id=?", (when, note_id))

    def underived_note_ids(self) -> list[int]:
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM notes WHERE derived_at IS NULL ORDER BY id")]

    def kv_get(self, key) -> str | None:
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def kv_set(self, key, value) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO kv(key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def search_bm25(self, query, limit) -> list[int]:
        if not query.strip():
            return []
        rows = self.conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?"
            " ORDER BY bm25(notes_fts) LIMIT ?",
            (query, limit),
        ).fetchall()
        return [r["rowid"] for r in rows]

    def search_vec(self, vector, limit) -> list[int]:
        rows = self.conn.execute(
            "SELECT note_id FROM vec_notes"
            " WHERE embedding MATCH ? AND k=? ORDER BY distance",
            (_pack_f32(vector), limit),
        ).fetchall()
        return [r["note_id"] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (6 tests). If `search_bm25` raises on a bare term, confirm the FTS query is passed as a single string; MATCH accepts plain words.

- [ ] **Step 5: Commit**

```bash
git add stash/storage.py tests/test_storage.py
git commit -m "feat: add storage layer with fts/vec sync and scoped dedupe"
```

---

### Task 3: Embedding service and fake embedder

**Files:**
- Create: `stash/embed.py`
- Test: `tests/test_embed.py`

**Interfaces:**
- Consumes: nothing (model loaded lazily).
- Produces:
  - `Embedder` Protocol: `embed(text: str) -> list[float]`, `embed_batch(texts: list[str]) -> list[list[float]]`, property `dim: int`, property `name: str`.
  - `SentenceTransformerEmbedder(model_name="all-MiniLM-L6-v2")` implementing it.
  - `FakeEmbedder(dim=384)` for tests not measuring retrieval quality: deterministic hash-based vectors.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embed.py
from stash.embed import FakeEmbedder


def test_fake_embedder_is_deterministic_and_right_dim():
    e = FakeEmbedder(dim=384)
    v1 = e.embed("hello")
    v2 = e.embed("hello")
    assert v1 == v2
    assert len(v1) == 384
    assert e.embed("hello") != e.embed("world")


def test_fake_embed_batch_matches_single():
    e = FakeEmbedder(dim=384)
    assert e.embed_batch(["a", "b"]) == [e.embed("a"), e.embed("b")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_embed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.embed'`

- [ ] **Step 3: Implement embedders**

```python
# stash/embed.py
"""Local embedding. The only learned model in stash; inference only."""

import hashlib
from typing import Protocol


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...
    @property
    def name(self) -> str: ...
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic, dependency-free embedder for plumbing tests.

    Not for retrieval-quality tests; those use SentenceTransformerEmbedder.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"fake-{self._dim}"

    def embed(self, text: str) -> list[float]:
        # Deterministic finite floats in [0, 1); never NaN/inf, so the vector
        # index accepts them. Distinct text -> distinct vector.
        out: list[float] = []
        counter = 0
        while len(out) < self._dim:
            h = hashlib.sha256(f"{counter}:{text}".encode()).digest()  # 32 bytes
            out.extend(b / 255.0 for b in h)
            counter += 1
        return out[:self._dim]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._name = model_name
        self._model = None  # lazy: avoid loading the model at import time

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._name)
        return self._model

    @property
    def dim(self) -> int:
        return int(self._ensure().get_sentence_embedding_dimension())

    @property
    def name(self) -> str:
        return self._name

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._ensure().encode(text)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in row] for row in self._ensure().encode(texts)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_embed.py -v`
Expected: PASS (2 tests). The real model is not loaded here.

- [ ] **Step 5: Commit**

```bash
git add stash/embed.py tests/test_embed.py
git commit -m "feat: add embedder protocol, fake embedder, and st embedder"
```

---

### Task 4: Capture pipeline and crash repair

**Files:**
- Create: `stash/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `Storage`, `Embedder`, `stash.parse.parse`.
- Produces:
  - `CaptureResult` dataclass: `note_id: int | None`, `intent: str`, `receipt: str`, `deduped: bool`.
  - `capture(storage, embedder, raw, source, created_at, source_chat_id=None, source_msg_id=None) -> CaptureResult`.
  - `repair(storage, embedder) -> int` (re-derives every undERIVED note; returns count). Method name: `repair`.
  - internal `derive(storage, embedder, note_id, raw, created_at) -> str` (returns intent), used by both capture and repair.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_capture.py
from datetime import datetime, timezone

from stash.capture import capture, repair
from stash.embed import FakeEmbedder
from stash.storage import Storage


def _now():
    return datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()


def test_plain_note_derives_with_empty_meta(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    r = capture(s, FakeEmbedder(), "just a plain thought", "cli", _now())
    assert r.note_id is not None
    row = s.get_note(r.note_id)
    assert row["derived_at"] is not None
    meta = s.conn.execute(
        "SELECT * FROM note_meta WHERE note_id=?", (r.note_id,)).fetchone()
    assert meta["intent"] == "note"
    assert meta["category"] is None
    tags = s.conn.execute(
        "SELECT tag FROM note_tags WHERE note_id=?", (r.note_id,)).fetchall()
    assert tags == []
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE note_id=?", (r.note_id,)
    ).fetchone()[0] == 0


def test_verbatim_unicode_and_long_text_preserved_and_embedded(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    raw = "emoji 🧠 combine é " + ("x" * 5000) + " #big"
    r = capture(s, FakeEmbedder(), raw, "cli", _now())
    assert s.get_note(r.note_id)["raw"] == raw          # byte-for-byte
    assert s.search_vec(FakeEmbedder().embed(raw), 5)   # embedded, no error


def test_reminder_note_creates_one_reminder(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    r = capture(s, FakeEmbedder(), "remind me to call mom in 30min", "cli", _now())
    rem = s.conn.execute(
        "SELECT fire_at, status FROM reminders WHERE note_id=?", (r.note_id,)
    ).fetchone()
    assert rem["status"] == "pending"
    assert rem["fire_at"] == datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc).isoformat()
    assert r.intent == "reminder"


def test_ambiguous_reminder_time_stores_note_without_reminder(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    r = capture(s, FakeEmbedder(), "remind me to call mom sometime", "cli", _now())
    assert r.note_id is not None
    assert "time" in r.receipt.lower()
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE note_id=?", (r.note_id,)
    ).fetchone()[0] == 0


def test_dedupe_returns_deduped_result(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "x", "telegram", _now(), "c1", "m1")
    r2 = capture(s, FakeEmbedder(), "x", "telegram", _now(), "c1", "m1")
    assert r2.deduped is True
    assert r2.note_id is None


def test_repair_rederives_unfinished_notes_idempotently(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    # Simulate a crash: raw written, derivation never ran.
    nid = s.add_note("remind me in 30min to stretch", "cli", _now())
    assert s.underived_note_ids() == [nid]
    assert repair(s, FakeEmbedder()) == 1
    assert repair(s, FakeEmbedder()) == 0            # idempotent
    assert s.get_note(nid)["derived_at"] is not None
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE note_id=?", (nid,)
    ).fetchone()[0] == 1                              # exactly one, not two
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_capture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.capture'`

- [ ] **Step 3: Implement capture and repair**

```python
# stash/capture.py
"""Capture pipeline: durable raw write first, then derivation. Crash-safe."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from stash.parse import parse


@dataclass
class CaptureResult:
    note_id: int | None
    intent: str
    receipt: str
    deduped: bool


def _fire_at(created_at: str, seconds: int) -> str:
    base = datetime.fromisoformat(created_at)
    return (base + timedelta(seconds=seconds)).isoformat()


def derive(storage, embedder, note_id: int, raw: str, created_at: str) -> str:
    parsed = parse(raw)
    storage.set_tags(note_id, parsed.tags)
    storage.set_meta(note_id, parsed.category, parsed.intent)
    storage.set_embedding(note_id, embedder.embed(raw))
    if parsed.intent == "reminder" and parsed.remind_in_seconds is not None:
        fire_at = _fire_at(created_at, parsed.remind_in_seconds)
        # Carry the delivery target (channel + chat) from the note, so a later
        # channel adapter knows where to send. unique(note_id) + DO NOTHING makes
        # this idempotent across re-derivation.
        with storage.conn:
            storage.conn.execute(
                "INSERT INTO reminders(note_id, fire_at, status, channel, chat_id)"
                " SELECT id, ?, 'pending', source, source_chat_id"
                " FROM notes WHERE id=?"
                " ON CONFLICT(note_id) DO NOTHING",
                (fire_at, note_id),
            )
    storage.mark_derived(note_id, datetime.now().astimezone().isoformat())
    return parsed.intent


def _receipt(parsed_intent: str, raw: str, resolvable: bool) -> str:
    if parsed_intent == "reminder" and not resolvable:
        return "saved as a note. I could not read a time, so no reminder was set."
    if parsed_intent == "reminder":
        return "stashed and reminder set."
    return "stashed."


def capture(storage, embedder, raw, source, created_at,
            source_chat_id=None, source_msg_id=None) -> CaptureResult:
    note_id = storage.add_note(
        raw, source, created_at, source_chat_id, source_msg_id)
    if note_id is None:
        return CaptureResult(None, "note", "already stashed.", deduped=True)
    parsed = parse(raw)
    resolvable = parsed.intent == "reminder" and parsed.remind_in_seconds is not None
    intent = derive(storage, embedder, note_id, raw, created_at)
    return CaptureResult(
        note_id, intent, _receipt(parsed.intent, raw, resolvable), deduped=False)


def repair(storage, embedder) -> int:
    count = 0
    for note_id in storage.underived_note_ids():
        row = storage.get_note(note_id)
        derive(storage, embedder, note_id, row["raw"], row["created_at"])
        count += 1
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capture.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add stash/capture.py tests/test_capture.py
git commit -m "feat: add capture pipeline with crash repair and reminder derivation"
```

---

### Task 5: Recall (RRF hybrid search)

**Files:**
- Create: `stash/recall.py`
- Test: `tests/test_recall.py`

**Interfaces:**
- Consumes: `Storage`, `Embedder`.
- Produces:
  - `rrf_fuse(rankings: list[list[int]], k: int = 60) -> list[int]` (pure).
  - `Result` dataclass: `note_id: int`, `raw: str`, `source: str`, `created_at: str`, `tags: list[str]`.
  - `search(storage, embedder, query: str, limit: int = 10, pool: int = 50) -> list[Result]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recall.py
from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.recall import rrf_fuse, search
from stash.storage import Storage


def test_rrf_fuse_hand_computed_order():
    # item 2 appears high in both lists -> should win.
    fused = rrf_fuse([[1, 2, 3], [2, 1, 4]], k=60)
    assert fused[0] == 2
    assert set(fused) == {1, 2, 3, 4}


def test_rrf_fuse_empty_lists():
    assert rrf_fuse([[], []]) == []


def test_search_empty_corpus_and_empty_query(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert search(s, FakeEmbedder(), "anything") == []
    nid = capture(s, FakeEmbedder(), "hello world", "cli",
                  datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()).note_id
    assert search(s, FakeEmbedder(), "") == []      # empty query, no crash
    assert nid is not None


def test_search_finds_captured_note(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    now = datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()
    capture(s, FakeEmbedder(), "the quick brown fox jumps", "cli", now)
    results = search(s, FakeEmbedder(), "brown fox", limit=5)
    assert results
    assert "brown fox" in results[0].raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recall.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.recall'`

- [ ] **Step 3: Implement recall**

```python
# stash/recall.py
"""Hybrid recall: BM25 + vector, fused with Reciprocal Rank Fusion."""

from dataclasses import dataclass


@dataclass
class Result:
    note_id: int
    raw: str
    source: str
    created_at: str
    tags: list[str]


def rrf_fuse(rankings: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


def _tags(storage, note_id: int) -> list[str]:
    return [r["tag"] for r in storage.conn.execute(
        "SELECT tag FROM note_tags WHERE note_id=?", (note_id,))]


def search(storage, embedder, query, limit: int = 10, pool: int = 50):
    if not query.strip():
        return []
    bm25 = storage.search_bm25(query, pool)
    vec = storage.search_vec(embedder.embed(query), pool)
    fused = rrf_fuse([bm25, vec])[:limit]
    results = []
    for nid in fused:
        row = storage.get_note(nid)
        if row is None:
            continue
        results.append(Result(
            note_id=nid, raw=row["raw"], source=row["source"],
            created_at=row["created_at"], tags=_tags(storage, nid)))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_recall.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add stash/recall.py tests/test_recall.py
git commit -m "feat: add rrf hybrid recall"
```

---

### Task 6: Recall eval harness (hit-rate@3 and MRR with the real encoder)

**Files:**
- Create: `stash/eval.py`
- Create: `tests/fixtures/recall_eval.json`
- Create: `tests/test_eval.py`
- Create: `stash/eval_baseline.json` (recorded baseline, committed)

**Interfaces:**
- Consumes: `Storage`, `Embedder`, `search`, `search_bm25`.
- Produces:
  - `EvalCase` dataclass: `query: str`, `note: str` (the one intended note text), `distractors: list[str]`.
  - `run_eval(cases, embedder) -> dict` with keys `hybrid_hit_rate_at_3`, `hybrid_mrr`, `fts_only_hit_rate_at_3`, `fts_only_mrr`, `n`.
  - `check_regression(result, baseline_path, tolerance=0.0) -> None` (raises `AssertionError` on regression below the recorded hybrid baseline).

- [ ] **Step 1: Create a small labeled fixture**

```json
// tests/fixtures/recall_eval.json
[
  {"query": "that book about human history and cognition",
   "note": "Sapiens by Harari, great on the cognitive revolution #books",
   "distractors": ["grocery list milk eggs bread",
                   "standup notes ship the scheduler #work",
                   "dentist appointment next tuesday"]},
  {"query": "the espresso machine I wanted",
   "note": "look into the Gaggia Classic Pro espresso machine #shopping",
   "distractors": ["call the plumber about the leak",
                   "idea: a note app that texts you back",
                   "renew car registration"]},
  {"query": "app idea about notes",
   "note": "idea: a note app that texts you back and finds things later",
   "distractors": ["Sapiens by Harari #books",
                   "buy running shoes",
                   "meeting with the bank friday"]}
]
```

- [ ] **Step 2: Write the failing test (uses the REAL encoder)**

```python
# tests/test_eval.py
import json
from pathlib import Path

import pytest

from stash.embed import SentenceTransformerEmbedder
from stash.eval import EvalCase, run_eval, check_regression

FIX = Path(__file__).parent / "fixtures" / "recall_eval.json"
BASELINE = Path(__file__).parent.parent / "stash" / "eval_baseline.json"


def _cases():
    return [EvalCase(**c) for c in json.loads(FIX.read_text())]


@pytest.mark.eval
def test_hybrid_beats_or_meets_recorded_baseline():
    result = run_eval(_cases(), SentenceTransformerEmbedder())
    assert result["n"] == 3
    assert 0.0 <= result["hybrid_hit_rate_at_3"] <= 1.0
    # Hybrid should not do worse than FTS-only on this set.
    assert result["hybrid_hit_rate_at_3"] >= result["fts_only_hit_rate_at_3"]
    check_regression(result, BASELINE)
```

Add the marker to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["eval: retrieval-quality tests that load the real embedding model"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.eval'`

- [ ] **Step 4: Implement the eval harness**

```python
# stash/eval.py
"""Recall evaluation: hit-rate@3 and MRR, hybrid vs FTS-only, real encoder."""

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from stash.recall import rrf_fuse
from stash.storage import Storage


@dataclass
class EvalCase:
    query: str
    note: str
    distractors: list[str] = field(default_factory=list)


def _rank_of(target_id: int, ranking: list[int]) -> int | None:
    for i, nid in enumerate(ranking):
        if nid == target_id:
            return i + 1  # 1-based
    return None


def _metrics(ranks: list[int | None]) -> tuple[float, float]:
    n = len(ranks)
    hits3 = sum(1 for r in ranks if r is not None and r <= 3) / n
    mrr = sum((1.0 / r) for r in ranks if r is not None) / n
    return hits3, mrr


def run_eval(cases: list[EvalCase], embedder) -> dict:
    hybrid_ranks: list[int | None] = []
    fts_ranks: list[int | None] = []
    for case in cases:
        with tempfile.TemporaryDirectory() as d:
            s = Storage.open(str(Path(d) / "eval.db"))
            now = datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()
            target_id = s.add_note(case.note, "import", now)
            s.set_embedding(target_id, embedder.embed(case.note))
            for text in case.distractors:
                did = s.add_note(text, "import", now)
                s.set_embedding(did, embedder.embed(text))
            bm25 = s.search_bm25(case.query, 50)
            vec = s.search_vec(embedder.embed(case.query), 50)
            hybrid_ranks.append(_rank_of(target_id, rrf_fuse([bm25, vec])))
            fts_ranks.append(_rank_of(target_id, bm25))
    h3, hmrr = _metrics(hybrid_ranks)
    f3, fmrr = _metrics(fts_ranks)
    return {
        "n": len(cases),
        "hybrid_hit_rate_at_3": h3,
        "hybrid_mrr": hmrr,
        "fts_only_hit_rate_at_3": f3,
        "fts_only_mrr": fmrr,
    }


def check_regression(result: dict, baseline_path, tolerance: float = 0.0) -> None:
    p = Path(baseline_path)
    if not p.exists():
        raise AssertionError(
            f"no recorded baseline at {p}; record one with record_baseline()")
    base = json.loads(p.read_text())
    for key in ("hybrid_hit_rate_at_3", "hybrid_mrr"):
        if result[key] + tolerance < base[key]:
            raise AssertionError(
                f"recall regression on {key}: {result[key]:.3f} < "
                f"baseline {base[key]:.3f}")


def record_baseline(result: dict, baseline_path) -> None:
    Path(baseline_path).write_text(json.dumps(result, indent=2, sort_keys=True))
```

- [ ] **Step 5: Record the baseline from the real encoder, then run the gate**

Run once to generate the committed baseline:

```bash
uv run python -c "
import json
from pathlib import Path
from stash.embed import SentenceTransformerEmbedder
from stash.eval import EvalCase, run_eval, record_baseline
cases = [EvalCase(**c) for c in json.loads(Path('tests/fixtures/recall_eval.json').read_text())]
res = run_eval(cases, SentenceTransformerEmbedder())
print(res)
record_baseline(res, 'stash/eval_baseline.json')
"
```

Inspect the printed metrics: `hybrid_hit_rate_at_3` should be strong (this tiny set is easy; expect near 1.0). If hybrid underperforms FTS-only, stop and investigate before recording. Then:

Run: `uv run pytest tests/test_eval.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Commit**

```bash
git add stash/eval.py stash/eval_baseline.json tests/fixtures/recall_eval.json tests/test_eval.py pyproject.toml
git commit -m "feat: add recall eval harness with recorded baseline gate"
```

---

### Task 7: Reminder scheduler (claim/lease, at-least-once)

**Files:**
- Create: `stash/reminders.py`
- Test: `tests/test_reminders.py`
- Modify: `stash/storage.py` (add reminder claim/complete/release ops)

**Interfaces:**
- Consumes: `Storage`, a `Delivery` (defined in Task 8, but Task 7 tests use a local fake so it does not import Task 8).
- Produces:
  - Storage additions: `claim_due_reminders(now, lease_seconds) -> list[sqlite3.Row]`, `mark_reminder_sent(rid, when)`, `release_reminder(rid, max_attempts)` (clears claim; sets `status='failed'` if attempts reached max).
  - `render_reminder(row) -> str`.
  - `run_due(storage, delivery, now: str, lease_seconds: int, max_attempts: int) -> int` (returns number delivered).

> Status set note: this adds a terminal `'failed'` status beyond the spec's
> `pending|sent|cancelled`, for reminders that exhaust `max_attempts`. This is an
> additive extension, not a weakening of any acceptance criterion.

- [ ] **Step 1: Add storage ops (write first, then their test in Step 2)**

Append to `stash/storage.py`:

```python
    def claim_due_reminders(self, now, lease_seconds):
        # Exclusive claim in one short transaction. A reminder is claimable if
        # pending, due, and either never claimed or its lease has expired.
        from datetime import datetime, timedelta
        cutoff = (datetime.fromisoformat(now)
                  - timedelta(seconds=lease_seconds)).isoformat()
        with self.conn:
            rows = self.conn.execute(
                "UPDATE reminders SET claimed_at=?, attempts=attempts+1"
                " WHERE id IN ("
                "   SELECT id FROM reminders"
                "   WHERE status='pending' AND fire_at<=?"
                "     AND (claimed_at IS NULL OR claimed_at<?))"
                " RETURNING *",
                (now, now, cutoff),
            ).fetchall()
        return rows

    def mark_reminder_sent(self, rid, when):
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET status='sent', sent_at=?, claimed_at=NULL"
                " WHERE id=?", (when, rid))

    def release_reminder(self, rid, max_attempts):
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET claimed_at=NULL,"
                " status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END"
                " WHERE id=?", (max_attempts, rid))
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_reminders.py
from datetime import datetime, timedelta, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.reminders import run_due
from stash.storage import Storage


class FakeDelivery:
    def __init__(self, fail_times=0):
        self.sent = []
        self.fail_times = fail_times

    def send(self, chat_id, text):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient")
        self.sent.append((chat_id, text))


def _at(minute):
    return datetime(2026, 9, 24, 0, minute, tzinfo=timezone.utc).isoformat()


def _due_reminder(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "remind me in 30min to stretch", "cli", _at(0))
    return s


def test_delivers_due_reminder_once(tmp_path):
    s = _due_reminder(tmp_path)
    d = FakeDelivery()
    assert run_due(s, d, _at(31), lease_seconds=120, max_attempts=5) == 1
    assert len(d.sent) == 1
    # Second pass after it is marked sent delivers nothing.
    assert run_due(s, d, _at(32), lease_seconds=120, max_attempts=5) == 0
    assert len(d.sent) == 1


def test_not_yet_due_is_not_delivered(tmp_path):
    s = _due_reminder(tmp_path)
    d = FakeDelivery()
    assert run_due(s, d, _at(10), lease_seconds=120, max_attempts=5) == 0
    assert d.sent == []


def test_send_failure_retries_then_fails_after_max(tmp_path):
    s = _due_reminder(tmp_path)
    d = FakeDelivery(fail_times=99)
    for m in range(31, 45):  # keep ticking; lease expires so it is reclaimable
        run_due(s, d, _at(m), lease_seconds=1, max_attempts=3)
    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] >= 3


def test_lease_blocks_double_claim_within_lease(tmp_path):
    s = _due_reminder(tmp_path)
    # First claim leases it; a second immediate pass (still under lease,
    # simulating a concurrent tick) claims nothing.
    first = s.claim_due_reminders(_at(31), lease_seconds=120)
    second = s.claim_due_reminders(_at(31), lease_seconds=120)
    assert len(first) == 1
    assert second == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stash.reminders'`

- [ ] **Step 4: Implement the scheduler**

```python
# stash/reminders.py
"""Reminder delivery: claim under a lease, send outside any transaction,
at-least-once."""

from datetime import datetime


def render_reminder(row) -> str:
    return f"reminder: {row['note_id']}"  # note text join is a web/telegram concern


def run_due(storage, delivery, now: str, lease_seconds: int,
            max_attempts: int) -> int:
    claimed = storage.claim_due_reminders(now, lease_seconds)
    delivered = 0
    for row in claimed:
        try:
            delivery.send(row["chat_id"], render_reminder(row))  # outside any txn
        except Exception:
            storage.release_reminder(row["id"], max_attempts)
        else:
            storage.mark_reminder_sent(row["id"], datetime.now().astimezone().isoformat())
            delivered += 1
    return delivered
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add stash/reminders.py stash/storage.py tests/test_reminders.py
git commit -m "feat: add reminder scheduler with lease-based at-least-once delivery"
```

---

### Task 8: Ports, reindex, and the CLI

**Files:**
- Create: `stash/ports.py`
- Create: `stash/reindex.py`
- Create: `stash/cli.py`
- Test: `tests/test_ports.py`
- Test: `tests/test_reindex.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `ports.py`: `IngestSource` Protocol (`poll() -> list[IncomingMessage]`, `ack(offset: str) -> None`), `Delivery` Protocol (`send(chat_id, text) -> None`), `IncomingMessage` dataclass (`chat_id: str`, `msg_id: str`, `text: str`, `sender_id: str`), and `MemoryIngest` / `MemoryDelivery` fakes.
  - `reindex.py`: `reindex(storage, embedder) -> None` (rebuilds derived data, preserves reminders + kv).
  - `cli.py`: `main(argv, storage=None, embedder=None) -> int` with subcommands `add <text>`, `search <query>`, `reindex`, `repair`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ports.py
from stash.ports import IncomingMessage, MemoryIngest, MemoryDelivery


def test_memory_ingest_polls_then_empties():
    ing = MemoryIngest([IncomingMessage("c1", "m1", "hi", "s1")])
    assert [m.text for m in ing.poll()] == ["hi"]
    assert ing.poll() == []


def test_memory_delivery_records():
    d = MemoryDelivery()
    d.send("c1", "hello")
    assert d.sent == [("c1", "hello")]
```

```python
# tests/test_reindex.py
from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.recall import search
from stash.reindex import reindex
from stash.storage import Storage


def _now():
    return datetime(2026, 9, 24, tzinfo=timezone.utc).isoformat()


def test_reindex_preserves_search_and_reminders(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "the quick brown fox", "cli", _now())
    capture(s, FakeEmbedder(), "remind me in 30min to stretch", "cli", _now())
    s.kv_set("offset", "77")
    before = [r.note_id for r in search(s, FakeEmbedder(), "brown fox")]

    reindex(s, FakeEmbedder())

    after = [r.note_id for r in search(s, FakeEmbedder(), "brown fox")]
    assert after == before                       # derived rebuilt identically
    assert s.kv_get("offset") == "77"            # kv preserved
    assert s.conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 1
```

```python
# tests/test_cli.py
from stash.cli import main
from stash.embed import FakeEmbedder
from stash.storage import Storage


def test_cli_add_then_search(tmp_path, capsys):
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    assert main(["add", "the quick brown fox #animals"], storage=s, embedder=e) == 0
    assert main(["search", "brown fox"], storage=s, embedder=e) == 0
    out = capsys.readouterr().out
    assert "brown fox" in out


def test_cli_tags_lists_counts(tmp_path, capsys):
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    main(["add", "note one #work"], storage=s, embedder=e)
    main(["add", "note two #work #urgent"], storage=s, embedder=e)
    capsys.readouterr()  # clear
    assert main(["tags"], storage=s, embedder=e) == 0
    out = capsys.readouterr().out
    assert "#work (2)" in out
    assert "#urgent (1)" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ports.py tests/test_reindex.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError` for the three new modules.

- [ ] **Step 3: Implement ports**

```python
# stash/ports.py
"""Channel ports: ingest and delivery, split by responsibility."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class IncomingMessage:
    chat_id: str
    msg_id: str
    text: str
    sender_id: str


class IngestSource(Protocol):
    def poll(self) -> list[IncomingMessage]: ...
    def ack(self, offset: str) -> None: ...


class Delivery(Protocol):
    def send(self, chat_id: str, text: str) -> None: ...


class MemoryIngest:
    def __init__(self, messages: list[IncomingMessage] | None = None) -> None:
        self._queue = list(messages or [])
        self.offset: str | None = None

    def poll(self) -> list[IncomingMessage]:
        out, self._queue = self._queue, []
        return out

    def ack(self, offset: str) -> None:
        self.offset = offset


class MemoryDelivery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))
```

- [ ] **Step 4: Implement reindex**

```python
# stash/reindex.py
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
```

> FTS note: `INSERT INTO notes_fts(notes_fts) VALUES('delete-all')` clears an
> external-content FTS5 index; `derive()` then re-populates it per note. If the
> installed SQLite rejects `'delete-all'`, use `('rebuild')` after re-inserting
> rows instead. Verify in Step 6 and keep whichever the runtime accepts.

- [ ] **Step 5: Implement the CLI**

```python
# stash/cli.py
"""stash command line: capture and recall without a bot or web UI."""

import argparse
import sys
from datetime import datetime, timezone

from stash.capture import capture
from stash.embed import SentenceTransformerEmbedder
from stash.recall import search
from stash.reindex import reindex
from stash.capture import repair
from stash.config import load_config
from stash.storage import Storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv=None, storage=None, embedder=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="stash")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add"); p_add.add_argument("text")
    p_search = sub.add_parser("search"); p_search.add_argument("query")
    sub.add_parser("reindex")
    sub.add_parser("repair")
    sub.add_parser("tags")  # deterministic Topics: tag -> count
    args = parser.parse_args(argv)

    if storage is None:
        cfg = load_config(__import__("os").environ)
        storage = Storage.open(cfg.db_path, cfg.busy_timeout_ms)
    if embedder is None:
        embedder = SentenceTransformerEmbedder()

    if args.cmd == "add":
        r = capture(storage, embedder, args.text, "cli", _now())
        print(r.receipt)
        return 0
    if args.cmd == "search":
        for res in search(storage, embedder, args.query):
            tags = (" " + " ".join(f"#{t}" for t in res.tags)) if res.tags else ""
            print(f"[{res.created_at}] {res.raw}{tags}")
        return 0
    if args.cmd == "reindex":
        reindex(storage, embedder); print("reindexed."); return 0
    if args.cmd == "repair":
        n = repair(storage, embedder); print(f"repaired {n} notes."); return 0
    if args.cmd == "tags":
        for r in storage.conn.execute(
            "SELECT tag, COUNT(*) AS n FROM note_tags"
            " GROUP BY tag ORDER BY n DESC, tag"):
            print(f"#{r['tag']} ({r['n']})")
        return 0
    return 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ports.py tests/test_reindex.py tests/test_cli.py -v`
Expected: PASS. If `reindex` errors on `'delete-all'`, apply the FTS note above and re-run.

- [ ] **Step 7: Full suite + manual smoke**

Run: `uv run pytest -v` (all tests, including the real-encoder eval)
Expected: all PASS.

Manual smoke against the real model:

```bash
uv run python -m stash.cli add "idea: a note app that texts you back #ideas"
uv run python -m stash.cli add "Sapiens is great on the cognitive revolution #books"
uv run python -m stash.cli search "that history book"
```

Expected: the search prints the Sapiens note first.

Add a module entry point so `python -m stash.cli` works: create `stash/__main__.py`:

```python
from stash.cli import main
import sys
sys.exit(main())
```

- [ ] **Step 8: Commit**

```bash
git add stash/ports.py stash/reindex.py stash/cli.py stash/__main__.py \
        tests/test_ports.py tests/test_reindex.py tests/test_cli.py
git commit -m "feat: add ports, reindex, and the stash cli"
```

---

## Scope boundary (what M1 does NOT include)

- No running scheduler loop and no real `Delivery`. Task 7 builds and unit-tests
  the `run_due` mechanics against a fake delivery; the background tick loop and a
  real delivery target (Telegram) arrive in Milestone 2. Reminders are created
  and stored in M1, but nothing delivers them yet, which is expected.
- No `IngestSource` poller loop wired to a running process; the CLI is the only
  ingest path in M1.

## Definition of done (Milestone 1)

- All tasks committed; `uv run pytest -v` green including the real-encoder eval.
- The five Review Focus items each have a passing test in their owning task.
- Spec §10 acceptance criteria are each covered by a named test.
- `stash add` / `stash search` work end-to-end against the real model.
- Docs-current: mark M1 tasks done on `docs/ROADMAP.md`, move them to
  `docs/RECENTLY-SHIPPED.md` with the gate evidence, per the dev-loop docs rule.

---
cam
