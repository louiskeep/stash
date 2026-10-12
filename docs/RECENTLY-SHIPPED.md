# stash: recently shipped

Dated log of merged/merge-ready slices with gate evidence. The roadmap points
here instead of restating history.

## 2026-10-12: Milestone 3 / W1: Web UI

Branch `feat/m3-w1-web-ui`. A local, server-rendered web UI over the existing
core, mounted into the `stash serve` asyncio loop (spec 6.11) so one process runs
the web server, the Telegram poller, and the reminder scheduler over one SQLite
DB. Read-only against stored notes; the only write path is capturing a new note.

What shipped: a Starlette + Jinja2 app with a minimal htmx-compatible shim for
live search (progressive enhancement; the search form degrades to a plain GET
with JS off); pages for Search (+ live `/search` fragment + `/add`), Timeline
(keyset paginated), Topics (deterministic tag/category + drilldown), Note detail
(raw + derived + related-by-embedding + reminder), and Reminders (read-only);
single-password signed sessions and session-bound CSRF via itsdangerous
(timing-safe compares, separate salts, a pre-session login CSRF token); a
deny-by-default auth guard; HttpOnly/SameSite/Secure-on-https cookies; eight
read-only storage queries; and the uvicorn server composed into the serve loop
with one stop signal shutting down all three loops (web skipped, logged, if its
config is unset). Docs: a web-setup runbook, the README's stale Postgres line
corrected to SQLite, `.env.example` web comments.

Gate evidence:
- 9 tasks, acceptance tests defined per task before implementation (R2); plan on
  disk at `docs/superpowers/plans/2026-10-12-stash-m3-w1-web-ui.md`.
- Independent cold review at the gate (a reviewer that had not seen the code
  being written) against the slice's Review Focus checklist. 0 Critical. Two
  HIGH findings, both root-fixed: `add_note` ran SQLite on a worker thread via
  `asyncio.to_thread(capture, ...)` (switched to `capture_async`, keeping the DB
  on the loop thread per the item-7 invariant); and the web tests drove the app
  with the sync TestClient's portal thread while the connection was opened on the
  test thread (rewritten to httpx `AsyncClient` over `ASGITransport`, the
  harness the plan specified). LOW items (login-CSRF as a shared constant, no
  login throttling, `Secure` behind a TLS proxy) acknowledged as single-user
  tradeoffs; the proxy case is documented in the web-setup guide.
- Reviewer confirmed correct by reading: deny-by-default guard (no prefix-match
  leak), session-bound CSRF, autoescaping on every surface, FTS/vec hardening +
  empty-query safety, timing-safe compares, serve composition, SQL of the new
  storage methods, and no transaction held across an await.

Verification note: this slice was built in an environment without PyPI access,
so `sqlite-vec`, the real encoder, and `pytest` could not be installed to run the
suite here. The security and routing logic that does not require the vec
extension was verified in-session through Starlette/httpx in-process transports
(auth guard, login flow, wrong-password 401, cookie flags, CSRF on all POSTs,
autoescaping, add reaching `capture_async` with source "web", static serving,
serve stop-event shutdown on an ephemeral port, all templates rendering). The
sqlite-vec-backed result assertions (search results, timeline/topics/detail
content, the new storage reads) run in the pytest suite and should be executed in
an environment with the deps installed before merge.

Not in W1 (by design): HDBSCAN clustering and ML auto-Topics (W2); the
`[project.scripts]` entry point, Dockerfile, and backup tooling (W3); editing or
deleting a note's raw text, or reminder actions, from the web.

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
