# stash: design spec

Status: draft (awaiting review)
Date: 2026-09-24
Author: cam
Risk: R2 (new subsystem, data model, network ingest path)

## 1. Intent

stash is a light note-capture and recall tool for people who jot down a lot of
small things and want their scattered thoughts, ideas, and reminders in one
place they can actually find later. The target user is a manager, writer, or
anyone whose notes pile up faster than they can file them.

The wedge is **recall**: the half-remembered thought you can finally find again.
You text a thought to a bot (or type it into a local web page), and stash keeps
it, organizes it on its own, and lets you search or browse it later. Reminders
you write in plain language ("remind me in 30min to call the vendor") fire back
to you at the right time.

Each person installs and runs their own copy on their own machine. There is one
user per install. A future hosted, paid version is possible, but nothing in v1
builds for it beyond leaving the schema room to grow.

## 2. Product principles (the stance)

These are load-bearing. They constrain every later decision.

1. **No generative AI in the product.** No LLM writes, rewrites, summarizes, or
   answers. The only learned model is a frozen sentence-embedding encoder used
   for search and clustering. It converts text to vectors; it never produces
   text.
2. **The raw note is sacred.** The text you sent is stored verbatim and never
   mutated. Everything else (tags, category, embedding, cluster) is *derived*
   and can be recomputed from the raw text at any time.
3. **A command surface, not a chat surface.** The bot acknowledges with a terse
   receipt. It never tries to hold a conversation or make you talk to it.
4. **Local and private by default.** After the one-time model download, nothing
   leaves the machine. No external API calls in the default product.
5. **Small enough to finish and to hand to someone else.** One process, one
   database file, one command to run.

## 3. Scope

### v1 (this spec): the backend core plus a Telegram vertical slice

v1 is one capture channel proven end to end, built as a testable domain core
behind channel-agnostic ports:

- Durable capture of a raw note.
- Deterministic parse (tags, `@category`, reminder intent): already built in
  `stash/parse.py`.
- Local embedding of each note.
- Hybrid recall: keyword (BM25) + vector (nearest neighbor) fused with
  Reciprocal Rank Fusion (RRF).
- Auto-sort into topics via unsupervised clustering, plus deterministic grouping
  by tag and category.
- Reminders that fire back through the originating channel.
- A `precision@k` evaluation harness to keep recall honest.

The **backend build is done first** and covers everything above behind an
abstract channel interface. The live Telegram adapter and the web UI are thin
layers added after a separate setup discussion. Tests fake the channel
transport, so the core is fully verifiable without a live bot.

### Non-goals for v1

- No generative features (summaries, Q&A, chat over notes).
- No multi-user accounts, sharing, or per-user isolation.
- No Slack (planned as a second adapter after Telegram is proven).
- No hosted/paid tier.
- No mobile app beyond using Telegram from a phone.

## 4. Architecture overview

```text
            capture                         recall
  Telegram ---------\                  /------ Web UI (later)
  (long-poll)        \                /
  Web form (later) ---> INGEST PORT --+--> CORE ENGINE <--- QUERY PORT
                                          |    |    |
                                          |    |    +-- RRF hybrid search
                                          |    +------- clustering (topics)
                                          +------------ reminder scheduler
                                               |
                                               v
                                        SQLite (one file)
                                   notes (raw, immutable)
                                   + derived: fts5, vec, tags,
                                     meta, clusters, reminders
                                               ^
                                               |
                                        DELIVERY PORT --> Telegram send (later)
```

Everything runs in a single process. Two background tasks (the channel poller
and the reminder scheduler) and the web server share one SQLite database.

## 5. Data model (SQLite)

Raw is separated from derived so principle 2 is enforced by the schema. A
`reindex` operation can drop and rebuild every derived table from `notes.raw`.

- `notes`
  - `id` INTEGER PK
  - `raw` TEXT NOT NULL: immutable, verbatim
  - `source` TEXT NOT NULL: `telegram` | `web` | `import` | `cli`
  - `source_msg_id` TEXT: channel message id for dedupe (nullable)
  - `source_chat_id` TEXT: originating chat/user for reply routing (nullable)
  - `created_at` TEXT NOT NULL: ISO-8601 UTC
  - UNIQUE (`source`, `source_msg_id`) where `source_msg_id` is not null
- `note_tags`
  - `note_id` INTEGER FK, `tag` TEXT: one row per tag, derived
- `note_meta`
  - `note_id` INTEGER PK FK, `category` TEXT, `intent` TEXT, `cluster_id` INTEGER
- `notes_fts`: FTS5 virtual table (external content over `notes.raw`), BM25
- `vec_notes`: sqlite-vec vec0 virtual table: `note_id`, `embedding FLOAT[384]`
- `reminders`
  - `id` PK, `note_id` FK, `fire_at` TEXT (ISO-8601 UTC), `status` TEXT
    (`pending` | `sent` | `cancelled`), `channel` TEXT, `chat_id` TEXT
- `clusters`
  - `id` PK, `label` TEXT, `size` INTEGER, `updated_at` TEXT
- `schema_migrations`: applied migration ids

Forward-compat: a later hosted version would add `user_id` to `notes`,
`reminders`, and `clusters`. v1 does not build this, but the tables are named and
keyed so the column can be added without a rewrite.

## 6. Components

Each is a small unit with one purpose and a defined interface, testable in
isolation.

### 6.1 Config
Loads settings from environment / a `.env` file: database path, embedding model
name, web bind address and port, web password, Telegram bot token, allowed
Telegram chat ids, scheduler tick interval. Validates required values at
startup. Secrets are never logged.

### 6.2 Storage
Owns the SQLite connection, migrations, and all reads/writes. Exposes typed
operations (`add_note`, `get_note`, `search_*`, `upsert_meta`, reminder CRUD,
cluster writes). Enables FTS5 and loads the sqlite-vec extension. All schema
changes go through numbered migrations.

### 6.3 Capture pipeline
`capture(raw, source, source_msg_id?, source_chat_id?) -> Note`:
1. Write the raw note first (capture is durable before any derivation can fail).
   Dedupe by `(source, source_msg_id)`.
2. Derive: run `parse()`; write tags and meta; compute and store the embedding;
   if intent is a reminder, insert a `pending` reminder at
   `created_at + remind_in_seconds`.
Derivation failure never loses the raw note; a `reindex` can recover derived
state later.

### 6.4 Ingest and delivery ports (channel abstraction)
An abstract `Channel` interface: `poll() -> [IncomingMessage]` and
`send(chat_id, text)`. The core depends only on this interface. A fake in-memory
channel drives tests and a `cli` capture path drives manual use. The real
Telegram adapter (6.9) is one implementation, added later.

### 6.5 Embedding service
Wraps `sentence-transformers` with the configured model (default
`all-MiniLM-L6-v2`, 384-dim, CPU). `embed(text) -> vector` and
`embed_batch(texts)`. Model name is recorded so a model change can trigger a
`reindex`. Inference only; never fine-tuned.

### 6.6 Recall (hybrid search + RRF)
`search(query, k) -> [Result]`:
- Parse the query for tag/category filters.
- BM25 top-N from `notes_fts`.
- Vector top-N from `vec_notes` using the query embedding.
- Fuse the two ranked lists with RRF into top-k.
- Each result carries the raw note, source, date, tags, and its component
  ranks so the ranking is explainable.

### 6.7 Clustering (topics)
`recluster()` runs unsupervised clustering (HDBSCAN by default, k-means
fallback) over all note embeddings, assigns `cluster_id` per note, and labels
each cluster deterministically from its top TF-IDF keywords or most common tags.
Runs on demand or after N new notes. Never blocks capture. Fully recomputable.

### 6.8 Reminder scheduler
A background loop ticks on the configured interval, selects `pending` reminders
with `fire_at <= now`, sends them via the delivery port, and marks them `sent`.
State lives in SQLite, so it survives restarts. Reminders whose time passed while
the app was down fire on next start, marked late. Idempotent: a crash between
send and status update at worst re-sends, never drops.

### 6.9 Telegram adapter (built after the setup discussion)
Implements `Channel` with long-polling (`getUpdates`, tracked offset). Outbound
only: no inbound port, no public URL, no TLS. Accepts messages only from
allow-listed chat ids. Replies with a terse receipt (`stashed ✓ #tags`) and
delivers reminders through `send`.

### 6.10 Web UI (built after the setup discussion)
Server-rendered (Jinja) with light htmx for live search; no build step. Pages:
Search, Timeline, Topics, Note detail (raw + derived + related-by-embedding),
Reminders. A single password grants a signed session cookie.

### 6.11 App composition
One entry point (`uv run stash`) starts the web server, the channel poller, and
the reminder scheduler over one database. Clean startup and shutdown.

## 7. ML inventory

The only learned model is the embedding encoder. Everything else is classical.

- Embedding: `all-MiniLM-L6-v2` (384-dim, ~90MB, CPU, frozen, inference only).
  Swappable via config; `bge-small-en-v1.5` / `gte-small` are drop-in
  alternatives.
- Keyword search: BM25 via SQLite FTS5 (no ML).
- Vector search: nearest neighbor via sqlite-vec (distance math).
- Fusion: Reciprocal Rank Fusion (arithmetic).
- Clustering: HDBSCAN (default) or k-means over embeddings; labels from TF-IDF /
  tags (deterministic).
- Reminder parsing: regex (already built).

No LLM, no text generation, no training, no network calls in the default
product.

## 8. Security and config

- Single user, but the box is internet-reachable so the phone can submit, so:
  - The web UI sits behind a password (signed session cookie) and binds to
    localhost by default; `0.0.0.0` is opt-in for LAN use.
  - Telegram ingest is allow-listed by chat id so strangers cannot inject notes.
  - Ingest needs no inbound port (long-poll is outbound), so there is no public
    attack surface by default.
- Secrets (bot token, web password) come from env / `.env`, never committed,
  never logged.

## 9. Packaging and run

- `uv sync && uv run stash`. State is one SQLite file plus the cached model.
  Backup is a file copy.
- Optional Dockerfile for a one-container install.
- First run downloads the embedding model once, then works offline.

## 10. Testing and acceptance criteria

Test-driven throughout. The Telegram transport and the embedding model are
behind interfaces so tests run without network or heavy model loads (a small
deterministic fake embedder is used where the real model is not under test).

Acceptance criteria for the v1 backend core:

- **Capture durability**: a raw note is persisted before derivation; a forced
  derivation failure leaves the raw note intact and re-runnable via `reindex`.
- **Verbatim preservation**: `get_note(id).raw` equals the exact submitted text
  for a corpus including punctuation, unicode, and markup.
- **Dedupe**: the same `(source, source_msg_id)` captured twice yields one note.
- **Parse integration**: tags, category, and reminder intent match `parse()` for
  a fixture set (parser unit tests already pass).
- **Hybrid recall**: on a labeled fixture set, RRF search returns the intended
  note in the top 3 at a **precision@3 baseline recorded in the eval harness**;
  the harness fails the build on regression below the recorded baseline.
- **RRF correctness**: fusion of two known ranked lists produces the known fused
  order (unit test with hand-computed expected ranks).
- **Reminders**: a note with "remind me in 30min" creates a `pending` reminder
  at the right `fire_at`; the scheduler fires it once when due, marks it `sent`,
  and does not double-fire; a reminder due while down fires on next start.
- **Clustering**: `recluster()` assigns every note a cluster, labels are
  non-empty, and re-running is stable on unchanged input.
- **Reindex**: dropping and rebuilding all derived tables reproduces identical
  tags, meta, and search results.

Test-strength (per `testing.md`) is measured on changed units at VERIFY so
review consumes assertion quality, not test counts.

## 11. Risk

R2. New subsystem with a data model and a network ingest path, but personal
scope, no customer data, low blast radius. Plan on disk, acceptance tests defined
before implementation, and an independent review at the gate are required. No R3
side effects in v1 (no deploy, no destructive external action).

## 12. Deferred / open (for the later frontend + Telegram discussion)

- Telegram bot creation, token handling, and allow-list UX.
- Web UI visual design and exact page interactions.
- Slack adapter (second channel).
- Graveyard/import path for bulk-loading existing notes on day one.
- Hosted/paid version (schema is left ready; nothing built).

---
cam
