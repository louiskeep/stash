# stash: design spec

Status: draft v2 (revised after Codex review, awaiting sign-off)
Date: 2026-09-24
Author: cam
Risk: R2 (new subsystem, data model, network ingest path)

Codex reviewed v1 and returned GO-WITH-CHANGES. This v2 folds in the six
changes: honest reminder guarantee, reminders as durable state (not derived),
crash-repair on capture, honest privacy boundary, a recall gate that measures
recall with the real encoder, and deferring clustering until real notes justify
it. The build order now makes a usable slice (capture and recall through the
CLI) the first milestone.

## 1. Intent

stash is a light note-capture and recall tool for people who jot down a lot of
small things and want their scattered thoughts, ideas, and reminders in one
place they can actually find later. The target user is a manager, writer, or
anyone whose notes pile up faster than they can file them.

The wedge is **recall**: the half-remembered thought you can finally find again.
You text a thought to a bot (or type it into a local web page), and stash keeps
it, organizes it, and lets you search or browse it later. Reminders you write in
plain language ("remind me in 30min to call the vendor") fire back to you at the
right time.

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
   mutated. Derived data (tags, category, embedding, cluster) can be recomputed
   from the raw text at any time. Delivery state (whether a reminder was sent) is
   *not* derived; it is durable state and survives a rebuild.
3. **A command surface, not a chat surface.** The bot acknowledges with a terse
   receipt. It never tries to hold a conversation or make you talk to it.
4. **Processing and storage are local.** Embeddings, search, and clustering run
   on your machine with no external API calls. The one external service is the
   optional chat transport: a note you send through Telegram passes through
   Telegram's servers, by definition. stash adds no other network egress. This is
   an honest boundary, not "nothing ever leaves the machine."
5. **Small enough to finish and to hand to someone else.** One process, one
   database file, one command to run.

## 3. Scope and build order

v1 is the backend core, built as a testable domain core behind channel-agnostic
ports, delivered in three milestones. The **CLI is the first usable slice**, so
the whole capture-and-recall loop is proven against a real interface (not only a
fake) before any bot or web work.

- **Milestone 1 (core, usable via CLI):** durable capture with crash-repair,
  deterministic parse (already built in `stash/parse.py`), local embedding,
  hybrid recall (BM25 + vector fused with RRF), reminders with an honest
  at-least-once guarantee, a recall eval harness, and a `stash` CLI to capture
  and search. Deterministic "Topics" by tag and category (no ML clustering yet).
- **Milestone 2 (Telegram adapter):** a real ingest source and delivery
  implementation. Brought ahead of the web UI so the port boundary is validated
  against a real transport early, since the adapter is not guaranteed to be thin
  (offset persistence, sender authorization, retries, receipts).
- **Milestone 3 (web UI + ML clustering):** the browse/search web surface, and
  HDBSCAN clustering for auto-Topics once there is a real corpus to justify it.

### Non-goals for v1

- No generative features (summaries, Q&A, chat over notes).
- No multi-user accounts, sharing, or per-user isolation.
- No Slack (planned as a second adapter after Telegram is proven).
- No hosted/paid tier.
- No mobile app beyond using Telegram from a phone.

## 4. Architecture overview

```text
            capture                              recall
  Telegram  ------\                        /------ CLI (M1)  Web UI (M3)
  (long-poll M2)   \                      /
  CLI (M1) ---------> INGEST SOURCE ---> CORE ENGINE <--- QUERY
  Web form (M3) ----/                       |    |
                                            |    +--- RRF hybrid search
                                            |    +--- Topics: tags/category (M1),
                                            |         HDBSCAN clustering (M3)
                                            +-------- reminder scheduler
                                               |            |
                                               v            v
                                        SQLite (one file)  DELIVERY
                                   notes (raw, immutable)     |
                                   derived (rebuildable):     +--> Telegram (M2)
                                     fts5, vec, tags, meta,
                                     clusters
                                   durable state (kept on rebuild):
                                     reminders (+ delivery status),
                                     ingest offset
```

Everything runs in a single process. Two background tasks (the ingest poller and
the reminder scheduler) and the web server share one SQLite database in WAL mode.

## 5. Data model (SQLite)

Three data classes, and the schema keeps them separate:

- **Raw** (`notes.raw`): immutable, verbatim, the source of truth.
- **Derived** (fts, vec, tags, meta, clusters): a `reindex` can drop and rebuild
  all of it from `notes.raw`.
- **Durable state** (reminders and their delivery status, ingest offset): written
  by the system, never reconstructable from raw text, and preserved across a
  reindex.

Tables:

- `notes`
  - `id` INTEGER PK
  - `raw` TEXT NOT NULL: immutable, verbatim
  - `source` TEXT NOT NULL: `cli` | `telegram` | `web` | `import`
  - `source_chat_id` TEXT: originating sender/chat for reply routing (nullable)
  - `source_msg_id` TEXT: channel message id (nullable)
  - `created_at` TEXT NOT NULL: ISO-8601 UTC
  - `derived_at` TEXT: set when derivation completed; NULL means "needs derive"
    (crash-repair reads this on startup)
  - UNIQUE (`source`, `source_chat_id`, `source_msg_id`) when `source_msg_id` is
    not null (dedupe is scoped to the chat, so ids from different chats never
    collide)
- `note_tags` (derived): `note_id` FK, `tag` TEXT, one row per tag
- `note_meta` (derived): `note_id` PK FK, `category` TEXT, `intent` TEXT,
  `cluster_id` INTEGER NULL
- `notes_fts` (derived): FTS5 external-content table over `notes.raw`. Kept in
  sync by the storage layer on every note write; rebuilt with the FTS5
  `'rebuild'` command during `reindex`.
- `vec_notes` (derived): sqlite-vec vec0 virtual table, `note_id`,
  `embedding FLOAT[384]`
- `reminders` (durable state)
  - `id` PK, `note_id` FK, `fire_at` TEXT (ISO-8601 UTC),
    `status` TEXT (`pending` | `sent` | `cancelled`),
    `channel` TEXT, `chat_id` TEXT,
    `claimed_at` TEXT NULL (lease timestamp), `attempts` INTEGER DEFAULT 0,
    `sent_at` TEXT NULL
  - UNIQUE (`note_id`): at most one reminder per note, so re-deriving a note is
    idempotent and never spawns duplicate reminders
- `clusters` (derived, Milestone 3): `id` PK, `label` TEXT, `size` INTEGER,
  `updated_at` TEXT
- `kv` (durable state): small key/value store, holds the Telegram ingest
  `offset` and similar cursors
- `schema_migrations`: applied migration ids

`reindex` rebuilds every derived table and re-derives `note_meta.intent`, but it
does **not** touch `reminders` rows or their `status`/`sent_at`, and it does not
reset `kv`. Reminder rows are reconciled to notes by `note_id`, not recreated.

Forward-compat: a later hosted version adds `user_id` to `notes`, `reminders`,
and `clusters`. v1 does not build this; the tables are keyed so the column can be
added without a rewrite.

## 6. Components

Each is a small unit with one purpose and a defined interface, testable in
isolation.

### 6.1 Config
Loads settings from environment / a `.env` file: database path, embedding model
name, web bind address and port, web password, web session secret, Telegram bot
token, allowed Telegram **sender ids**, scheduler tick interval, reminder lease
timeout, max delivery attempts. Validates required values at startup. Secrets are
never logged.

### 6.2 Storage
Owns the SQLite connection, migrations, and all reads/writes. Opens the database
in **WAL mode with a busy_timeout**, keeps every transaction short, and never
holds a transaction open across a network call. Exposes typed operations
(`add_note`, `mark_derived`, `get_note`, `search_*`, reminder claim/complete,
`kv` get/set, cluster writes). Loads the sqlite-vec extension and keeps
`notes_fts` in sync. All schema changes go through numbered migrations.

### 6.3 Capture pipeline
`capture(raw, source, source_chat_id?, source_msg_id?) -> Note`:
1. Write the raw note first with `derived_at = NULL`, in its own short
   transaction. Dedupe by the scoped unique key. Capture is durable before any
   derivation can fail.
2. Derive: run `parse()`; write tags and meta; compute and store the embedding;
   if intent is a reminder with a resolvable time, upsert the single reminder for
   that note at `created_at + remind_in_seconds`. Set `derived_at`.
3. If parse sees reminder intent but cannot resolve a time, store the note and
   return an "ambiguous time" receipt rather than silently creating or skipping a
   reminder.

Crash repair: on startup the core re-derives any note with `derived_at IS NULL`.
Because reminder creation is keyed unique per note, re-derivation is idempotent.

### 6.4 Ports (ingest source and delivery)
Two narrow interfaces, split by responsibility:
- `IngestSource`: `poll() -> [IncomingMessage]`, `ack(offset)` / offset
  persistence, and per-message sender identity for authorization.
- `Delivery`: `send(chat_id, text) -> DeliveryResult`.

The core depends only on these. A fake in-memory pair drives tests; the CLI is an
ingest path with no delivery; the Telegram adapter (6.9) implements both. The
ingest offset advances (is persisted) only **after** the corresponding note
capture commits, so a crash mid-poll re-reads rather than drops.

### 6.5 Embedding service
Wraps `sentence-transformers` with the configured model (default
`all-MiniLM-L6-v2`, 384-dim, CPU). `embed(text)` and `embed_batch(texts)`. The
model name is recorded so changing it triggers a `reindex`. Inference only;
never fine-tuned. A small deterministic fake embedder exists for tests that are
not measuring retrieval quality.

### 6.6 Recall (hybrid search + RRF)
`search(query, k) -> [Result]`:
- Parse the query for tag/category filters.
- BM25 top-N from `notes_fts`.
- Vector top-N from `vec_notes` using the query embedding.
- Fuse the two ranked lists with RRF into top-k.
- Each result carries the raw note, source, date, tags, and its component ranks
  so the ranking is explainable.

### 6.7 Topics
Milestone 1: deterministic grouping by `#tag` and `@category` (exact, free, no
ML). Milestone 3: `recluster()` runs HDBSCAN over note embeddings, assigns
`cluster_id` where density supports it (outliers stay unclustered, which is
correct behavior), and labels each cluster from its top TF-IDF keywords or most
common tags. Clustering never blocks capture and is fully recomputable. It is
deferred because it adds real dependencies and behaves poorly on a small, uneven
personal corpus; it earns its place only once there are enough notes.

### 6.8 Reminder scheduler
A background loop ticks on the configured interval. Each tick, in a short
transaction, **claims** due `pending` reminders by setting `claimed_at` (a
lease), then releases the transaction and sends each one through `Delivery`
outside any transaction. On success it marks `status = sent`, `sent_at`. On
failure it increments `attempts` and clears the claim for retry, up to the
configured max; a stale claim (older than the lease timeout) is reclaimable so a
crashed tick does not strand a reminder.

Guarantee (honest): **at-least-once delivery, duplicates possible.** A crash
after a successful `send` but before the status write can re-send on restart.
This is stated plainly rather than promising "exactly once," which SQLite plus an
external send cannot provide. The lease prevents two concurrent ticks from
sending the same reminder. Reminders due while the app was down fire on next
start, marked late.

### 6.9 Telegram adapter (Milestone 2)
Implements `IngestSource` and `Delivery` with long-polling (`getUpdates`, offset
persisted in `kv`). Outbound only: no inbound port, no public URL. Authorizes on
**private sender id**, not a group chat id (a group id would admit every member).
Retries transient send failures. Replies with a terse receipt
(`stashed ✓ #tags`, or an "ambiguous time" note per 6.3). Validated against the
core's port contract before the web UI is built.

### 6.10 Web UI (Milestone 3)
Server-rendered (Jinja) with light htmx for live search; no build step. Pages:
Search, Timeline, Topics, Note detail (raw + derived + related-by-embedding),
Reminders. Auth: a single password grants a signed session cookie using the
configured persistent session secret; cookies are HttpOnly, SameSite, and Secure;
state-changing routes carry CSRF protection. Binds to localhost by default.

### 6.11 App composition
One entry point (`uv run stash`) starts the web server, the ingest poller, and
the reminder scheduler over one database. Clean startup (including crash repair)
and shutdown.

## 7. ML inventory

The only learned model is the embedding encoder. Everything else is classical.

- Embedding: `all-MiniLM-L6-v2` (384-dim, ~90MB, CPU, frozen, inference only).
  Swappable via config; `bge-small-en-v1.5` / `gte-small` are drop-in
  alternatives.
- Keyword search: BM25 via SQLite FTS5 (no ML).
- Vector search: nearest neighbor via sqlite-vec (distance math).
- Fusion: Reciprocal Rank Fusion (arithmetic).
- Clustering (Milestone 3): HDBSCAN over embeddings; labels from TF-IDF / tags
  (deterministic). Deferred until a real corpus exists.
- Reminder parsing: regex (already built).

No LLM, no text generation, no training. The only network egress in the product
is the optional chat transport.

## 8. Security and config

- Single user, but the box is internet-reachable so the phone can submit:
  - Telegram ingest authorizes on a private sender id allow-list, so strangers
    (and other members of any group) cannot inject notes. Ingest is outbound
    long-poll, so there is no inbound attack surface by default.
  - The web UI binds to localhost by default. Exposing it beyond localhost
    (`0.0.0.0`) requires TLS, the persistent session secret, Secure/HttpOnly/
    SameSite cookies, and CSRF protection; the bind address alone does not make
    it "LAN only," so deployment guidance states this.
- Secrets (bot token, web password, session secret) come from env / `.env`,
  never committed, never logged.

## 9. Packaging and run

- `uv sync && uv run stash`. State is one SQLite file (WAL) plus the cached
  model.
- Backup: a consistent SQLite online backup (`VACUUM INTO` or the backup API)
  while the process is running, not a raw file copy of a live WAL database.
- Optional Dockerfile for a one-container install.
- First run downloads the embedding model once, then works offline.

## 10. Testing and acceptance criteria

Test-driven throughout. Transports and the embedding model sit behind interfaces
so most tests run without network or heavy model loads. Retrieval-quality tests
use the **real encoder**; a deterministic fake embedder is used only where the
test is not measuring retrieval.

Acceptance criteria for the v1 core (Milestone 1):

- **Capture durability + crash repair**: a raw note is persisted with
  `derived_at NULL` before derivation; a forced derivation failure leaves the raw
  note intact; on restart the note is re-derived, and no duplicate reminder is
  created.
- **Verbatim preservation**: `get_note(id).raw` equals the exact submitted text
  for a corpus with punctuation, unicode, and markup.
- **Scoped dedupe**: the same `(source, chat_id, msg_id)` captured twice yields
  one note; the same `msg_id` from a different chat yields two.
- **Parse integration**: tags, category, and reminder intent match `parse()` for
  a fixture set (parser unit tests already pass).
- **Recall gate**: on a labeled fixture set, report **hit-rate@3 and MRR** using
  the real encoder, alongside an **FTS-only baseline** for comparison; the
  harness records the hybrid baseline and fails the build on regression below it.
  The intended note is the single relevant result per query.
- **RRF correctness**: fusion of two known ranked lists produces the known fused
  order (unit test with hand-computed expected ranks).
- **Reminders**: "remind me in 30min" creates exactly one `pending` reminder at
  the right `fire_at`; the scheduler delivers it when due and marks it `sent`;
  the lease prevents two concurrent ticks from sending the same reminder; a
  reminder due while down fires on next start (marked late); a send failure
  retries up to the configured max. The at-least-once guarantee (possible
  duplicate only after a crash between send and status write) is asserted as the
  documented behavior, not "exactly once."
- **Ambiguous reminder receipt**: a note with reminder intent but no resolvable
  time is stored and returns an "ambiguous time" receipt; no reminder row is
  created.
- **Reindex preserves state**: `reindex` rebuilds tags, meta, FTS, and vectors to
  identical search results, and leaves every `reminders` row and its
  `status`/`sent_at` and the `kv` offset untouched.
- **SQLite hardening**: the database opens in WAL with a busy_timeout; a test
  asserts no transaction is held open across a `Delivery.send` call.
- **Offset safety**: the ingest offset advances only after capture commits; a
  simulated crash between poll and commit re-reads the message rather than
  dropping it.

Milestone-3 clustering has its own acceptance criteria defined when it is
planned (stability on unchanged input, non-empty labels, outliers allowed).

Test-strength (per `testing.md`) is measured on changed units at VERIFY so review
consumes assertion quality, not test counts.

## 11. Risk

R2. New subsystem with a data model and a network ingest path, but personal
scope, no customer data, low blast radius. Plan on disk, acceptance tests defined
before implementation, and an independent review at the gate are required. No R3
side effects in v1 (no deploy, no destructive external action).

## 12. Deferred / open

- Telegram bot creation, token handling, and sender-id discovery UX (Milestone 2
  setup discussion).
- Web UI visual design and exact page interactions (Milestone 3).
- Slack adapter (second channel).
- Graveyard/import path for bulk-loading existing notes on day one.
- Hosted/paid version (schema left ready; nothing built).

---
cam
