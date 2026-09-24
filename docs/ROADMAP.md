# stash: roadmap

Single source of truth for current and next-up work. Completed slices move to
`docs/RECENTLY-SHIPPED.md` (created on the first ship), dated, with gate
evidence. This file carries only current + next-up, never a backlog of done
items.

Design spec: `docs/superpowers/specs/2026-09-24-stash-design.md`

Each slice runs the standard dev loop: FRAME → PLAN → DEVELOP → SELF-CHECK →
VERIFY → REVIEW → GATE → DOCUMENT. Acceptance tests are defined in the plan
before implementation (R2).

## Milestone 1: Backend core (current)

Build the domain core and channel-agnostic ports. Fully testable with a fake
channel and a deterministic fake embedder; no live Telegram, no web UI yet.

- [ ] **B0: Project setup & config.** Dependencies (sqlite-vec,
      sentence-transformers, hdbscan, web stack), config loader with validation,
      `.env.example`, app skeleton and entry point.
- [ ] **B1: Storage & schema.** SQLite connection, numbered migrations, all
      tables from spec §5, FTS5 + sqlite-vec wired, typed storage operations.
- [ ] **B2: Capture pipeline.** `capture()` with durable raw-write-first,
      dedupe, parse integration (reuses `stash/parse.py`), and `reindex`.
- [ ] **B3: Embedding service.** sentence-transformers wrapper, batch embed,
      vector persistence, model-name recorded for reindex-on-change.
- [ ] **B4: Recall + eval.** BM25 + vector + RRF hybrid search; `precision@k`
      eval harness with a labeled fixture set and a recorded baseline that fails
      the build on regression.
- [ ] **B5: Clustering / topics.** `recluster()` (HDBSCAN default, k-means
      fallback), deterministic labels, cluster assignment.
- [ ] **B6: Reminder scheduler.** Background tick loop, fire-once semantics,
      restart-safe, late-fire on next start, delivery through the port.
- [ ] **B7: Ports & CLI.** Abstract `Channel` interface, in-memory fake for
      tests, and a `cli` capture/search path for manual end-to-end use without a
      bot.

Exit for Milestone 1: capture → derive → embed → hybrid recall → cluster →
remind all pass their acceptance tests through the fake channel and CLI, gated.

## Milestone 2: Telegram + Web UI (after a setup discussion)

Not started; scope confirmed after we discuss bot setup and UI. Thin adapters
over the Milestone 1 core.

- [ ] **F1: Telegram adapter.** Long-poll `Channel` impl, allow-list, terse
      receipts, reminder delivery.
- [ ] **F2: Web UI.** Jinja + htmx pages (Search, Timeline, Topics, Note
      detail, Reminders), password/session guard.
- [ ] **F3: Packaging.** Dockerfile, run docs, backup guidance.

## Later / deferred

- Slack adapter (second channel).
- Graveyard import (bulk-load existing notes).
- Hosted/paid version (schema left ready; nothing built).

---
cam
