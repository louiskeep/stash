# stash Milestone 2: Telegram adapter (design spec)

Status: draft v2 (revised after Codex design gate, proceeding to plan)
Date: 2026-09-25
Author: cam
Risk: R2 (new subsystem, long-running daemon, network ingest, reminder concurrency)

Builds on the M1 core (shipped on main, f02747b). Codex gated the v1 design
GO-WITH-CHANGES; this v2 folds in the five changes: an awaited async delivery
contract, retry backoff plus honest attempt/at-least-once semantics, a complete
per-update acknowledgment rule, private-chat enforcement, and explicit
event-loop boundaries (no transaction across await, offloaded encode,
single-instance guard, real reminder text).

## 1. Intent

Realize the wedge: text a thought, get it back later. M2 wires a real Telegram
transport into the M1 ports and adds the long-running process that makes capture,
search, and reminder delivery work from a phone without touching the box.

## 2. Product principles (carried from M1)

- No generative AI. The bot never converses; it is a command surface.
- The raw note is sacred (stored verbatim, never rewritten).
- Local processing and storage. Telegram is the one external transport.
- Reminder delivery is at-least-once, with one stated exception: after
  `max_attempts` failed deliveries a reminder becomes terminal (`failed`) and is
  not retried. See §6.
- Single user per install. No inbound port: Telegram is outbound long-poll.
- No em-dashes in prose, comments, or bot copy.

## 3. Scope

### In scope (Milestone 2)

- `stash serve`: one single-threaded asyncio process running the Telegram poller
  and the reminder scheduler over the one SQLite DB, with a single-instance guard.
- A Telegram adapter implementing the M1 ports: async long-poll `getUpdates`
  (offset in `kv`), async `sendMessage`, authorized to an allowed-user-id list
  and to private chats only.
- An extensible command dispatch: plain text captures; `/find <query>` searches;
  `/recent` and `/help` are conveniences; more commands slot in later.
- The reminder-scheduler concurrency redesign deferred from M1 (§6), now async
  and live.
- A setup runbook.

### Out of scope

- Web UI and HDBSCAN clustering (Milestone 3).
- SMS / phone-number channel (a future hosted-tier adapter).
- Multi-user, accounts, or hosted deployment.
- A rich command set beyond capture / find / recent / help (later features).

## 4. Architecture

```text
              Telegram servers (api.telegram.org)
                 ^  getUpdates (async long-poll)  |  sendMessage (async)
                 |  (outbound)                     v
        ======== stash serve (one asyncio event-loop thread) ========
          poller task:  TelegramIngest.poll() -> [IncomingMessage]
                 |  per-update, in order:
                 |    dispatch (capture / find / recent / help) -> await send reply
                 |    then ack offset = update_id + 1
                 v
          scheduler task:  every tick, under a lock, run_due():
                 |    drain due reminders one at a time, await send, mark
                 v
              SQLite (one file, WAL)  <- accessed only on the loop thread;
                                          no transaction spans an await.
          CPU-bound embedding encode is offloaded via asyncio.to_thread;
          the resulting vector is written on the loop thread.
```

## 5. Components

### 5.1 Telegram API client (async)
A thin async HTTP client over the Bot API using `httpx.AsyncClient`
(`getUpdates`, `sendMessage`). Bot token from config. It never logs the token or
message text, and it scrubs both from any request/response/error logging. All
calls are outbound to `api.telegram.org`; no inbound port.

### 5.2 Async ports (M2 extension of the M1 ports)
M1's `IngestSource`/`Delivery` are synchronous (fine for the CLI). M2 introduces
awaited variants for the daemon:
- `AsyncDelivery.send(chat_id, text) -> awaitable` (must be awaited; a bare
  coroutine is never treated as a completed send).
- The poller returns `IncomingMessage(update_id, chat_id, chat_type, sender_id,
  text)`; `update_id` and `chat_type` are new fields required by the ack rule
  (§5.4) and private-chat enforcement (§7).
The scheduler (`run_due`) becomes async and awaits `AsyncDelivery.send`. The M1
sync `run_due` and its tests are migrated to the async form (the CLI never
delivered, so nothing else depends on the sync signature).

### 5.3 TelegramIngest (implements the async ingest)
`poll()` issues async `getUpdates` with the stored offset and a long-poll
timeout, returns the batch of `IncomingMessage` in `update_id` order. Drops any
update that is not a private-chat text message from an allowed `sender_id`
(no note stored, no reply), but still advances the offset past it (§5.4).

### 5.4 Update acknowledgment (complete rule)
Updates are handled strictly in `update_id` order. After each update is fully
handled, the offset in `kv` advances to that `update_id + 1`. A capture is
fully handled once its note is committed; the receipt reply is best-effort,
and its failure does not block the offset, so a persistently failing send
can never stall later updates. A rejected sender, a group/non-text update, or
an unsupported update is simply skipped and the offset still advances. The
offset is never advanced past an update whose note commit did not complete,
so a crash re-reads from the last fully-handled update. Telegram may then
re-deliver; capture dedupe by `(telegram, chat_id, msg_id)` makes a
re-delivered capture idempotent, and a re-sent command reply is harmless.

### 5.5 Command dispatch (extensible)
A registry mapping a command name to a handler. Routing: if the message's first
token is a registered command (`/find`, `/recent`, `/help`), dispatch to it;
otherwise the whole message is captured verbatim. A note that legitimately begins
with `/` but is not a registered command is captured unchanged (never eaten by
the router). An empty or whitespace-only message is ignored (offset still
advances). Handlers:
- **capture** (default): `capture()` as `source="telegram"` with the private
  chat id and message id; reply with the terse receipt.
- **/find `<query>`**: `search()`; reply with the top K tiles (raw note,
  truncated to fit, date, tags), or "nothing found."
- **/recent**: the last N notes.
- **/help**: the available commands.

### 5.6 App composition: `stash serve`
A new CLI subcommand: acquire a single-instance guard (an OS file lock on the DB
path; refuse to start a second `serve` on the same DB), resolve config + storage
+ embedder, run startup repair and embed-model reconcile, then run the poller and
scheduler tick as asyncio tasks until SIGINT/SIGTERM, then shut down cleanly
(release the lock, close the client). The existing `add`/`search`/`tags`/
`reindex`/`repair` subcommands are unchanged.

Event-loop discipline: SQLite is touched only on the loop thread; no transaction
is held across an `await`; the CPU-bound embedding encode runs via
`asyncio.to_thread` and its vector is written on the loop thread; `getUpdates`
uses async I/O so a long-poll wait never blocks the scheduler.

## 6. Reminder scheduler redesign (the M1 must-fix, now async and live)

`run_due` becomes async and correct under the live loop:
- **Serialize:** an async lock; a tick that fires while `run_due` is running is
  skipped, never overlapped.
- **One at a time:** claim a single due reminder (`LIMIT 1`), ownership-checked,
  deliver it, mark it, then continue, until no due reminder remains.
- **Attempt counted at send, durably, then awaited send:** immediately before the
  send, increment `attempts` in a short committed, ownership-checked transaction;
  then `await` the delivery; on success mark `sent`; on failure apply backoff
  (below). A reminder claimed but not yet at the send point never counts against
  its cap.
- **Retry backoff / per-tick eligibility:** a failed delivery does NOT make the
  reminder immediately reclaimable. It is deferred at least one lease interval
  (retry no earlier than `now + lease_seconds`), so one Telegram outage cannot
  drain a reminder to its cap inside a single tick's drain loop; retries are
  spaced across ticks. After `max_attempts` failed deliveries the reminder is
  terminal (`failed`).

Honest semantics (stated, not eliminated): the attempt count is committed just
before the network send, so a crash in the window between that commit and the
send counts an attempt that may not have delivered. With a finite cap this means
a reminder can, in the worst case, reach `failed` having been counted
`max_attempts` times without a confirmed delivery. This is the fundamental
tradeoff of bounded-retry at-least-once over a non-transactional network; the
spec does not claim to eliminate it. The terminal cap is the stated exception to
at-least-once (§2).

## 7. Security

- Bot token and message text are never logged, including in HTTP error paths.
- Only allow-listed `sender_id`s in PRIVATE chats can capture, search, or receive
  anything. Group and channel updates are dropped even from an allowed user, so a
  `/find` in a group cannot leak notes, and reminders are delivered only to the
  private chat. Every other sender is silently dropped.
- Long-poll is outbound only, so there is no inbound attack surface.
- Notes transit Telegram's servers (the honest boundary from §2); processing and
  storage remain local.

## 8. Testing and acceptance criteria

A fake async Telegram transport (no network) implements the client interface so
routing, capture, search, delivery, and crash windows are tested without a bot.
Acceptance:

- **Allow-list + private-chat:** a message from a non-allowed sender is dropped;
  a message in a group (even from an allowed sender) is dropped; an allowed
  sender in a private chat is captured. Offset still advances past dropped
  updates.
- **Capture routing + dedupe:** plain text is captured as `source="telegram"`
  with chat id and msg id; the same `update_id`/message delivered twice yields
  one note.
- **Ack ordering + crash windows:** the offset advances to `update_id+1` only
  after full handling; a simulated crash after capture-commit but before ack
  re-reads that update (idempotent via dedupe); a crash mid-batch re-reads from
  the first unacked update; a failed command reply does not advance the offset
  past it.
- **/find (awaited):** `/find <query>` awaits the send and replies with the
  intended note among top results (real encoder in the retrieval test); empty
  query or no hit replies "nothing found" without raising. A stubbed async send
  that is not awaited fails the test (guards the async contract).
- **/recent, /help, unknown, empty, leading-slash note:** each returns its
  defined behavior; `/x` unknown is captured verbatim (or help, per final plan)
  and never crashes; an empty message is ignored; a note "/todo milk" with no
  such command is stored verbatim.
- **Reminder delivery (redesigned):** each due reminder is delivered once with
  its note text (truncated to Telegram's 4096-char limit); the M1 reproduce-first
  cases still pass; a reproduce-first test proves the retry backoff prevents a
  single-tick cap exhaustion during an outage; a reproduce-first test proves no
  due reminder is recorded `failed` without a send under overlapping/slow-send
  and a low cap.
- **Serialize:** an overlapping tick is skipped, not run concurrently.
- **No txn across await:** a test asserts no SQLite transaction is open across an
  awaited `send`.
- **Single instance:** a second `stash serve` on the same DB refuses to start.
- **Daemon:** `stash serve` starts both tasks, runs startup repair + embed-model
  reconcile, and shuts down cleanly on signal (transport faked).

Test-strength is measured on changed units at VERIFY. A live smoke test against a
real bot and token is a manual gate step, not part of the automated suite.

## 9. Risk

R2. New subsystem, long-running daemon, network ingest, and a concurrency
redesign, but personal scope, no customer data, low blast radius. Plan on disk,
acceptance tests defined before implementation, independent review at the gate
(dennis + Codex), and a live smoke before the milestone is called done. No R3
side effects.

## 10. Deferred / open

- More bot commands and features (planned for later).
- Web UI and clustering (M3).
- SMS / phone-number channel (future hosted tier).
- Hosted/paid version.

---
cam
