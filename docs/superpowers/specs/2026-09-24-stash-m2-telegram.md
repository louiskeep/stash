# stash Milestone 2: Telegram adapter (design spec)

Status: draft (awaiting review)
Date: 2026-09-24
Author: cam
Risk: R2 (new subsystem, long-running daemon, network ingest, reminder concurrency)

Builds on the M1 core (shipped on main, f02747b). M1 proved capture and recall
through a CLI behind channel-agnostic ports. M2 makes stash the actual product:
you text a thought to a Telegram bot from your phone, it stashes it, you text a
query and get your notes back, and reminders ping you at the right time.

## 1. Intent

Realize the wedge: text a thought, get it back later. M2 wires a real Telegram
transport into the M1 ports and adds the long-running process that makes capture,
search, and reminder delivery work from a phone without touching the box.

## 2. Product principles (carried from M1, unchanged)

- No generative AI. The bot never converses; it is a command surface. The only
  learned model stays the frozen embedding encoder.
- The raw note is sacred (stored verbatim, never rewritten).
- Local processing and storage. Telegram is the one external transport: a note
  you send passes through Telegram's servers by definition. No other egress.
- Reminder delivery is at-least-once (never exactly-once).
- Single user per install. No inbound port: Telegram runs by outbound long-poll.
- No em-dashes in prose, comments, or bot copy.

## 3. Scope

### In scope (Milestone 2)

- A long-running daemon, `stash serve`: one process, single-threaded asyncio,
  running the Telegram poller and the reminder scheduler over the one SQLite DB.
- A Telegram adapter implementing the M1 `IngestSource` and `Delivery` ports:
  long-poll `getUpdates` (offset in `kv`), `sendMessage` for replies, authorized
  against an allowed-user-id list.
- An extensible command dispatch: plain text captures; `/find <query>` searches;
  `/recent` and `/help` are conveniences. New commands slot in without touching
  the transport (features will be added later).
- The reminder-scheduler concurrency redesign deferred from M1 (see §6.5), now
  that `run_due` runs live.
- A setup runbook (create the bot, configure token and allow-list, run the
  daemon).

### Out of scope

- Web UI and HDBSCAN clustering (Milestone 3).
- SMS / phone-number channel (a future hosted-tier adapter; the ports already
  allow it).
- Multi-user, accounts, or a hosted deployment.
- A rich command set beyond capture / find / recent / help (later features).

## 4. Architecture

```text
              Telegram servers (api.telegram.org)
                 ^  getUpdates (long-poll)   |  sendMessage
                 |  (outbound)               v
        ============ stash serve (one asyncio process) ============
          TelegramIngest.poll() --> [IncomingMessage]
                 |                                   ^
                 v                                   |
          command dispatch                     TelegramDelivery.send()
           |  plain text -> capture (M1)             ^
           |  /find       -> search (M1)  --replies--/
           |  /recent,/help                          ^
                                                      |
          reminder scheduler tick --> run_due() ------/
                 |
                 v
              SQLite (one file, WAL)   <-- shared by both tasks
```

Two cooperative asyncio tasks (the poller and the scheduler tick) share one
SQLite connection-owning `Storage`. Startup runs the M1 crash-repair and
embed-model reconcile before serving.

## 5. Components

### 5.1 Telegram API client
A thin async HTTP client over the Telegram Bot API (`getUpdates`,
`sendMessage`). One new dependency (`httpx`, async). Reads the bot token from
config. Never logs the token or message bodies. All calls are outbound to
`api.telegram.org`; the box needs no inbound port.

### 5.2 TelegramIngest (implements `IngestSource`)
`poll()` issues `getUpdates` with the stored offset (from `kv`) and a long-poll
timeout, returns `[IncomingMessage(chat_id, msg_id, text, sender_id)]`.
`ack(offset)` persists the new offset in `kv` only after the corresponding
captures commit, so a crash mid-poll re-reads rather than drops. Messages whose
`sender_id` is not in the configured allow-list are dropped (not stored, no
reply).

### 5.3 TelegramDelivery (implements `Delivery`)
`send(chat_id, text)` calls `sendMessage`, with a small bounded retry on
transient failures. Used for receipts, search replies, and reminder delivery.

### 5.4 Command dispatch (extensible)
A registry mapping a command name to a handler `handle(ctx, args) -> reply`.
Routing: a message beginning with `/` dispatches to the named command (unknown
command -> a short help reply); any other message is a capture. Handlers:
- **capture** (default, plain text): `capture()` the text as `source="telegram"`
  with the sender's chat id and message id; reply with the terse receipt
  (`stashed ✓ #tags`, or the ambiguous-time note from M1).
- **/find `<query>`**: `search()` and reply with the top K (default 3-5) result
  tiles: raw note, date, tags. Empty query or no hits -> a short "nothing found."
- **/recent**: the last N notes.
- **/help**: the available commands.
The registry is the extension point; adding a command is one handler plus one
registration, no transport changes.

### 5.5 App composition: `stash serve`
A new CLI subcommand starts the daemon: resolve config + storage + embedder, run
startup repair and embed-model reconcile, then run the poller and scheduler tick
as asyncio tasks until interrupted. Clean shutdown on SIGINT/SIGTERM. The
existing `add`/`search`/`tags`/`reindex`/`repair` subcommands stay as-is for
local use.

### 5.6 Reminder scheduler redesign (the M1 must-fix, now live)
M1 shipped a lease-based `run_due` whose batch-claim + attempt-at-claim
accounting is unsafe once `run_due` can overlap. M2 makes it correct for the
live loop:
- **Serialize ticks:** an async lock ensures at most one `run_due` runs at a
  time; a tick that fires while one is running is skipped, not overlapped.
- **One reminder at a time:** `run_due` claims and delivers a single due
  reminder per iteration (claim with `LIMIT 1`, ownership-checked send, mark),
  looping until none remain, so a slow send never strands or wrongly-fails other
  due reminders.
- **Count an attempt at send, not at claim:** increment `attempts` (durably,
  ownership-checked) immediately before the delivery attempt, so a reminder that
  is claimed but not yet sent never counts against its cap. The attempt cap and
  the `failed` terminal state keep their M1 meaning; the lease-aware guards from
  M1 remain.
This closes the Codex gate finding (a due reminder recorded `failed` without
being sent) at the root, in the milestone where the live loop makes it real.

## 6. Config and setup

Config fields already exist (bot token, allowed sender ids, tick interval, lease,
max attempts, busy timeout). M2 adds only a long-poll timeout (seconds) if
needed. Setup runbook (in the plan and a `docs` how-to):
1. Create a bot with @BotFather, copy the token.
2. Put the token in `.env` (`STASH_TELEGRAM_BOT_TOKEN`).
3. Get your numeric Telegram user id (message `@userinfobot`), add it to
   `STASH_ALLOWED_SENDER_IDS`.
4. `python -m stash serve` (run under `systemd`/`tmux` to keep it alive).

## 7. Security

- The bot token and message bodies are never logged.
- Only allow-listed Telegram user ids can capture, search, or receive anything;
  every other sender is silently dropped, so a stranger who finds the bot cannot
  inject notes or read yours.
- Long-poll is outbound only, so there is no inbound attack surface.
- Notes transit Telegram's servers (the honest boundary from §2); processing and
  storage remain local.

## 8. Testing and acceptance criteria

A fake Telegram transport (no network) implements the API client interface so
routing, capture, search, and delivery are tested without a bot. Acceptance:

- **Allow-list:** a message from a non-allowed sender id is dropped (no note
  stored, no reply); an allowed sender's message is captured.
- **Capture routing + dedupe:** a plain-text message is captured as
  `source="telegram"` with chat id and msg id; the same update delivered twice
  (Telegram re-delivery) yields one note.
- **Offset safety:** the `kv` offset advances only after captures commit; a
  simulated crash between poll and commit re-reads the update.
- **/find:** a `/find <query>` message replies with the intended note among the
  top results (using the real encoder in the retrieval test); an empty query or
  no-hit replies with the "nothing found" copy and does not raise.
- **/recent, /help, unknown command:** each returns its defined reply; unknown
  `/x` returns help, never a crash.
- **Reminder delivery (live-loop correctness):** the redesigned `run_due`
  delivers each due reminder once through the fake delivery; the M1
  reproduce-first cases still pass; and the M1 Codex finding is covered by a
  reproduce-first test proving no due reminder is recorded `failed` without a
  send under overlapping/slow-send conditions and a low cap.
- **Serialize:** an overlapping tick is skipped, not run concurrently.
- **Daemon:** `stash serve` starts both tasks, runs startup repair + embed-model
  reconcile, and shuts down cleanly on signal (a focused test of the composition,
  transport faked).

Test-strength is measured on changed units at VERIFY. A live smoke test against
a real bot and token is a manual gate step, not part of the automated suite.

## 9. Risk

R2. New subsystem, a long-running daemon, network ingest, and a concurrency
redesign, but personal scope, no customer data, low blast radius. Plan on disk,
acceptance tests defined before implementation, independent review at the gate
(dennis + Codex), and a live smoke before the milestone is called done. No R3
side effects (no deploy, no destructive external action).

## 10. Deferred / open

- More bot commands and features (explicitly planned for later).
- Web UI and clustering (M3).
- SMS / phone-number channel (future hosted tier; ports already support it).
- Hosted/paid version.

---
cam
