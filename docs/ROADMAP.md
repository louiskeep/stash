# stash: roadmap

Single source of truth for current and next-up work. Completed slices move to
`docs/RECENTLY-SHIPPED.md` (created on the first ship), dated, with gate
evidence. This file carries only current + next-up, never a backlog of done
items.

Design spec: `docs/superpowers/specs/2026-09-24-stash-design.md` (v2, Codex
GO-WITH-CHANGES incorporated).

Each slice runs the standard dev loop: FRAME → PLAN → DEVELOP → SELF-CHECK →
VERIFY → REVIEW → GATE → DOCUMENT. Acceptance tests are defined in the plan
before implementation (R2).

## Milestone 1: Core, usable via CLI (current)

The whole capture-and-recall loop proven against a real interface (the CLI), no
bot or web UI needed. Fake ingest/delivery for tests.

- [ ] **B0: Project setup & config.** Dependencies (sqlite-vec,
      sentence-transformers, web stack; hdbscan deferred to M3), config loader
      with validation (incl. session secret, sender allow-list, lease/attempt
      settings), `.env.example`, app skeleton and entry point.
- [ ] **B1: Storage & schema.** SQLite in WAL + busy_timeout, numbered
      migrations, all M1 tables from spec §5 (notes with `derived_at`, scoped
      dedupe key; reminders with lease/attempts/`sent_at`, unique per note; kv;
      fts5; vec), typed storage ops, short-transaction discipline.
- [ ] **B2: Capture pipeline + crash repair.** `capture()` writes raw first
      (`derived_at NULL`), derives, sets `derived_at`; startup re-derives
      unfinished notes idempotently; ambiguous-time receipt path.
- [ ] **B3: Embedding service.** sentence-transformers wrapper, batch embed,
      vector persistence, model-name recorded for reindex-on-change, fake
      embedder for non-quality tests.
- [ ] **B4: Recall + eval.** BM25 + vector + RRF hybrid search; eval harness
      reporting hit-rate@3 and MRR with the real encoder plus an FTS-only
      baseline, recording the hybrid baseline and failing the build on
      regression.
- [ ] **B5: Reminder scheduler.** Claim/lease, deliver outside any transaction,
      retry with attempt cap, reclaim stale leases, late-fire on restart,
      honest at-least-once guarantee.
- [ ] **B6: Ports + CLI + reindex.** `IngestSource` / `Delivery` split, in-memory
      fakes, `reindex` that rebuilds derived data and preserves reminders +
      offset, and a `stash` CLI to capture and search end-to-end.

Exit for Milestone 1: capture → derive → embed → hybrid recall → remind, plus
reindex and crash-repair, all pass their spec §10 acceptance tests through the
CLI and fakes, gated.

## Milestone 2: Telegram adapter (after a setup discussion)

Real ingest + delivery, brought ahead of the web UI to validate the port
boundary against a real transport.

- [ ] **T1: Telegram adapter.** Long-poll `IngestSource` with offset in `kv`,
      private-sender-id authorization, `Delivery` with retries, terse receipts,
      contract-tested against the M1 core.

## Milestone 3: Web UI + ML clustering

- [ ] **W1: Web UI.** Jinja + htmx pages (Search, Timeline, Topics, Note detail,
      Reminders), password/session guard with secure cookies + CSRF, localhost
      bind by default.
- [ ] **W2: Clustering.** HDBSCAN auto-Topics with TF-IDF/tag labels, outliers
      allowed, once a real corpus justifies it.
- [ ] **W3: Packaging.** Dockerfile, online-backup guidance, run docs.

## Later / deferred

- Slack adapter (second channel).
- Graveyard import (bulk-load existing notes).
- Hosted/paid version (schema left ready; nothing built).

---
cam
