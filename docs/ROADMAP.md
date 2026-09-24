# stash: roadmap

Single source of truth for current and next-up work. Completed slices move to
`docs/RECENTLY-SHIPPED.md`, dated, with gate evidence. This file carries only
current + next-up, never a backlog of done items.

Design spec: `docs/superpowers/specs/2026-09-24-stash-design.md` (v2, Codex
GO-WITH-CHANGES incorporated).

Each slice runs the standard dev loop: FRAME → PLAN → DEVELOP → SELF-CHECK →
VERIFY → REVIEW → GATE → DOCUMENT. Acceptance tests are defined in the plan
before implementation (R2).

## Shipped

- **Milestone 1: Core, usable via CLI**: shipped 2026-09-24 on `feat/m1-core`.
  See `docs/RECENTLY-SHIPPED.md`. The capture-and-recall loop (config, SQLite
  schema, storage, local embeddings, crash-safe capture, RRF hybrid recall,
  real-encoder eval gate, at-least-once reminder scheduler, ports + reindex +
  the `stash` CLI) works end to end through the CLI. 47 tests pass.

## Milestone 2: Telegram adapter (current, after a setup discussion)

Real ingest + delivery, brought ahead of the web UI to validate the port
boundary against a real transport. Needs a bot token and sender-id setup from
Cam before build.

- [ ] **T1: Telegram adapter.** Long-poll `IngestSource` with offset in `kv`,
      private-sender-id authorization, `Delivery` with retries, terse receipts,
      contract-tested against the M1 core. Wire a running poller + scheduler loop
      (the M1 scheduler mechanics exist; M2 adds the live loop and a real
      `Delivery`).

## Milestone 3: Web UI + ML clustering

- [ ] **W1: Web UI.** Jinja + htmx pages (Search, Timeline, Topics, Note detail,
      Reminders), password/session guard with secure cookies + CSRF, localhost
      bind by default.
- [ ] **W2: Clustering.** HDBSCAN auto-Topics with TF-IDF/tag labels, outliers
      allowed, once a real corpus justifies it.
- [ ] **W3: Packaging.** `[project.scripts]` entry so `uv run stash` works
      (M1 runs as `python -m stash`), Dockerfile, online-backup guidance, run
      docs.

## Follow-ups carried from M1 review (next slice)

- reindex hardening: NULL `derived_at` at reindex start so an interrupted
  reindex is `repair`-recoverable.
- Add one keyword-overlap case to the recall eval fixture so RRF fusion (not
  vector-only) is exercised by the gate.
- Small cleanups: drop the double `stash.capture` import in `cli.py`; CLI tests
  for `reindex`/`repair`; `note_tags` unique(note_id, tag); `note_meta.cluster_id`
  FK to `clusters`.

## Later / deferred

- Slack adapter (second channel).
- Graveyard import (bulk-load existing notes).
- Hosted/paid version (schema left ready; nothing built).

---
cam
