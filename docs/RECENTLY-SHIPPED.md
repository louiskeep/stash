# stash: recently shipped

Dated log of merged/merge-ready slices with gate evidence. The roadmap points
here instead of restating history.

## 2026-09-25: Milestone 2: Telegram adapter

Branch `feat/m2-telegram` (commits `f02747b..a14671f`). A `stash serve` asyncio
daemon that long-polls Telegram, captures plain-text messages, answers `/find`,
`/recent`, `/help`, and delivers reminders. Includes the async redesign of the
reminder scheduler (one-at-a-time claim, attempt-at-send, retry backoff,
lease-owned CAS completion) that was deferred from M1.

What shipped: async ports; the reminder scheduler redesign; an httpx Bot API
client (token/text never logged); ingest/delivery adapters with private-chat +
allow-list auth; command dispatch; the `stash serve` daemon (single-instance
flock, fail-fast config validation, `.env` loading, per-update ack, best-effort
receipts); and a setup runbook.

Gate evidence:
- 8 tasks, each spec+quality reviewed; fix rounds on the scheduler and the
  daemon; a whole-branch review that drove a 6-item fix wave.
- dennis GO (0 Critical/High/Medium, 4 Low). Codex final gate GO after three
  rounds that caught and root-fixed: a reminder-authorization leak (delivery to
  a since-removed sender), a raw-first durability regression (a failed encode
  must not lose the note), token-in-logs hardening, a long-poll timeout bug, and
  a spec/code ack reconciliation.
- Full suite: 100 tests pass, including the real-encoder recall gate.

Carry-forward follow-ups (both gates below the merge bar): graceful shutdown can
exceed the documented `max(25s, tick)` during a Telegram outage (systemd
`Restart` mitigates); reminders created via the CLI (no chat) are simply never
claimed by the daemon.

Not in M2 (by design): web UI + clustering (M3), SMS (future hosted tier),
multi-user. A live smoke test against a real bot is a manual step (needs your
bot token + user id).

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
- Two-gate merge check: dennis GO (0 Critical/0 High); Codex gate ran three
  rounds. Round 1 (reindex crash-recovery, reminder lease ownership, CLI startup
  repair) and round 2 (ownership recheck before send, attempt-cap on claim,
  reindex on embed-model change) were remediated at the root with reproduce-first
  tests. Round 3 flagged a reminder-scheduler batch-claim/attempt-accounting bug
  that is only reachable under a concurrent `run_due` (a live scheduler loop),
  which M1 does not have. Per Cam's call, M1 ships now and that item is tracked
  as must-fix-before-M2 (see ROADMAP). The cheap config-validation fix
  (reject `max_attempts = 0`) landed here.
- Full suite: 58 tests pass, including the real-encoder eval.

Not in M1 (by design): live Telegram adapter, running poller/scheduler loop,
web UI, HDBSCAN clustering. Run command is `python -m stash` (a `uv run stash`
entry point is an M3 packaging follow-up).

Known limitation carried to M2: the reminder scheduler's concurrency (batch
claim + attempt accounting under a running loop) needs a redesign before the
live loop lands. It is safe in M1 because `run_due` is never invoked
concurrently here.

---
cam
