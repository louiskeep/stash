# stash: recently shipped

Dated log of merged/merge-ready slices with gate evidence. The roadmap points
here instead of restating history.

## 2026-09-24: Milestone 1: core, usable via CLI

Branch `feat/m1-core` (commits `f55cea2..7965bb5`), merge-ready pending Cam's
merge call. Built with subagent-driven development: a fresh implementer and an
independent spec+quality reviewer per task, then a whole-branch review.

What shipped:
- Config loader with validation (`stash/config.py`).
- SQLite schema + numbered migrations, WAL + busy_timeout, sqlite-vec + FTS5
  (`stash/db.py`, `stash/migrations.py`).
- Storage layer: scoped dedupe, FTS/vec sync, kv, BM25 + vector search
  (`stash/storage.py`).
- Local embeddings: `all-MiniLM-L6-v2` + a deterministic fake for tests
  (`stash/embed.py`).
- Crash-safe capture pipeline with `repair()` (`stash/capture.py`).
- RRF hybrid recall (`stash/recall.py`).
- Real-encoder hit-rate@3 / MRR eval with a recorded, fail-closed regression
  gate (`stash/eval.py`, `stash/eval_baseline.json`).
- Lease-based at-least-once reminder scheduler (`stash/reminders.py`).
- Channel ports (IngestSource / Delivery), reindex that preserves durable state,
  and the `stash` CLI (`stash/ports.py`, `stash/reindex.py`, `stash/cli.py`).

Gate evidence:
- 8 tasks, each spec+quality reviewed by a fresh reviewer; fix loops on Tasks 1,
  2, 4 (migration idempotency, reindex regression test + narrowed dedupe catch,
  UTC `derived_at`).
- Whole-branch final review (most capable model): ready to merge with fixes, no
  Critical. Four should-fix items resolved before merge: FTS query crash on
  punctuation, missing spec §10 txn-across-send test, `STASH_EMBED_MODEL`
  ignored by the CLI, and these docs.
- Full suite: 47 tests pass, including the real-encoder eval.

Not in M1 (by design): live Telegram adapter, running poller/scheduler loop,
web UI, HDBSCAN clustering. Run command is `python -m stash` (a `uv run stash`
entry point is an M3 packaging follow-up).

---
cam
