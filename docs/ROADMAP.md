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
- **Milestone 2: Telegram adapter**: shipped 2026-09-25 on `feat/m2-telegram`.
  See `docs/RECENTLY-SHIPPED.md`. A `stash serve` asyncio daemon: Telegram
  long-poll capture, `/find` search, `/recent`, `/help`, and reminder delivery,
  with the reminder scheduler redesigned async (one-at-a-time claim, attempt-at-
  send, backoff). 100 tests pass. dennis GO + Codex GO.

## Milestone 3: Web UI + ML clustering (current)

- [ ] **W1: Web UI.** Jinja + htmx pages (Search, Timeline, Topics, Note detail,
      Reminders), password/session guard with secure cookies + CSRF, localhost
      bind by default.
- [ ] **W2: Clustering.** HDBSCAN auto-Topics with TF-IDF/tag labels, outliers
      allowed, once a real corpus justifies it.
- [ ] **W3: Packaging.** `[project.scripts]` entry so `uv run stash` works
      (M1 runs as `python -m stash`), Dockerfile, online-backup guidance, run
      docs.

## Follow-ups carried from review (next slice)

From M2 (Codex gate, below the merge bar):
- Bound graceful shutdown so it respects the documented `max(25s, tick)`: check
  the stop event between reminders in `run_due`'s drain and cap the in-flight
  delivery timeout, and align the runbook's systemd stop timeout.
- Reminders created via the CLI (no delivery chat) are never claimed by the
  daemon; consider surfacing that rather than leaving them silently pending.

From M1:
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
