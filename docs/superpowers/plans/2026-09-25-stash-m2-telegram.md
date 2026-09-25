# stash Milestone 2 (Telegram adapter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram bot so the user texts thoughts in, texts `/find` to get notes back, and receives reminders, via a long-running `stash serve` asyncio daemon over the M1 core.

**Architecture:** One single-threaded asyncio process runs a Telegram long-poll poller and a reminder scheduler over the one SQLite DB. A Telegram client (httpx, async) fills the M1 ports; a command registry routes plain text to capture and `/find`/`/recent`/`/help` to handlers. The reminder scheduler is redesigned async: serialized ticks, one-reminder-at-a-time claim, attempt counted at send, and a retry backoff.

**Tech Stack:** Python >=3.10, asyncio, `httpx` (async Bot API), stdlib `sqlite3`, `sentence-transformers`, `pytest` + `pytest-asyncio`, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-24-stash-m2-telegram.md` (v2)

## Global Constraints

- No generative AI: only the frozen embedding encoder, inference only. The bot never converses.
- The raw note is immutable; reminders + kv are durable state.
- Reminder delivery is at-least-once, with one stated exception: after `max_attempts` failed deliveries a reminder becomes terminal `failed`.
- No SQLite transaction is held across an `await`. SQLite is touched only on the event-loop thread. The CPU-bound embedding encode is offloaded via `asyncio.to_thread`.
- The offset in `kv` advances to `update_id + 1` only after an update is fully handled, in `update_id` order (including rejected/unsupported/failed updates).
- Only allow-listed `sender_id`s in PRIVATE chats may capture, search, or receive anything. Group/channel updates are dropped. Reminders are delivered only to the private chat.
- The bot token and message text are never logged, including in HTTP error paths.
- Bot replies and stored-note-derived text sent to Telegram are truncated to Telegram's 4096-character message limit.
- No em-dashes in prose, comments, or bot copy.

## Review Focus

- **A note that begins with `/` but is not a registered command** is captured verbatim, never routed or dropped (Task 4 test).
- **A `/find` in a group chat from an allowed user** is dropped (no search, no reply), so notes never leak to a group (Task 5 / Task 6 test).
- **A crash after capture-commit but before offset ack** re-reads that update and dedupe makes it idempotent (Task 6 test).
- **A Telegram outage** (send always fails) does not drain a due reminder to its `max_attempts` inside one tick; retries are spaced across ticks (Task 2 test).
- **An async `send` that is returned but not awaited** is never counted as a completed delivery (Task 2 test asserts the awaited path).

---

### Task 0: Dependencies and async test setup

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_async_smoke.py`

**Interfaces:**
- Produces: `httpx` and `pytest-asyncio` available; `asyncio_mode = "auto"` so `async def test_*` run without a decorator.

- [ ] **Step 1: Add deps and asyncio mode**

In `pyproject.toml`, add to `[project].dependencies`: `"httpx>=0.27"`. Add to `[dependency-groups].dev`: `"pytest-asyncio>=0.23"`. Under `[tool.pytest.ini_options]` add `asyncio_mode = "auto"`. Keep existing entries (markers, testpaths, addopts, pythonpath).

Run: `uv sync`

- [ ] **Step 2: Write a failing async smoke test**

```python
# tests/test_async_smoke.py
import asyncio


async def test_asyncio_mode_runs_async_tests():
    await asyncio.sleep(0)
    assert True
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_async_smoke.py -v`
Expected: PASS (proves pytest-asyncio auto mode works). If it errors that the test was not collected/ran as a coroutine, fix `asyncio_mode`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock tests/test_async_smoke.py
git commit -m "chore: add httpx + pytest-asyncio and enable asyncio auto mode"
```

---

### Task 1: Async ports and fakes

**Files:**
- Modify: `stash/ports.py`
- Test: `tests/test_async_ports.py`

**Interfaces:**
- Consumes: existing `stash/ports.py` (`IncomingMessage`, `Delivery`, `MemoryDelivery`).
- Produces:
  - `IncomingMessage` gains fields `update_id: int` and `chat_type: str` (in addition to `chat_id`, `msg_id`, `text`, `sender_id`). Keep field order stable and give the two new fields no defaults (update the existing MemoryIngest construction in the M1 test if needed, but prefer adding them as the first fields so call sites are explicit).
  - `AsyncDelivery` Protocol: `async def send(self, chat_id: str, text: str) -> None`.
  - `FakeAsyncDelivery`: records `(chat_id, text)` in `.sent`; optional `fail_times` to raise on the first N sends; `send` is a real coroutine.
  - `FakeAsyncIngest`: constructed with a list of `IncomingMessage`; `async def poll()` returns and drains them (returns `[]` after); `ack(offset)` records `.offset`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_async_ports.py
import pytest
from stash.ports import IncomingMessage, FakeAsyncDelivery, FakeAsyncIngest


def _msg(uid=1, text="hi"):
    return IncomingMessage(update_id=uid, chat_id="c1", chat_type="private",
                           sender_id="s1", msg_id=str(uid), text=text)


async def test_fake_async_delivery_records_and_can_fail():
    d = FakeAsyncDelivery(fail_times=1)
    with pytest.raises(RuntimeError):
        await d.send("c1", "first")
    await d.send("c1", "second")
    assert d.sent == [("c1", "second")]


async def test_fake_async_ingest_polls_then_empties():
    ing = FakeAsyncIngest([_msg(1), _msg(2)])
    first = await ing.poll()
    assert [m.update_id for m in first] == [1, 2]
    assert await ing.poll() == []
    ing.ack("3")
    assert ing.offset == "3"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_async_ports.py -v`
Expected: FAIL (ImportError for the new names).

- [ ] **Step 3: Extend ports.py**

Add to `stash/ports.py` (keep the M1 sync `IncomingMessage`/`Delivery`/`MemoryIngest`/`MemoryDelivery` behavior; extend `IncomingMessage` with the two new fields):

```python
@dataclass
class IncomingMessage:
    update_id: int
    chat_id: str
    chat_type: str
    sender_id: str
    msg_id: str
    text: str


class AsyncDelivery(Protocol):
    async def send(self, chat_id: str, text: str) -> None: ...


class FakeAsyncDelivery:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail_times

    async def send(self, chat_id: str, text: str) -> None:
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("transient")
        self.sent.append((chat_id, text))


class FakeAsyncIngest:
    def __init__(self, messages: list[IncomingMessage] | None = None) -> None:
        self._queue = list(messages or [])
        self.offset: str | None = None

    async def poll(self, offset=None) -> list[IncomingMessage]:
        out, self._queue = self._queue, []
        return out

    def ack(self, offset: str) -> None:
        self.offset = offset
```

The async ingest contract is `poll(offset)` (the real `TelegramIngest.poll`
needs the offset for `getUpdates`); the fake accepts and ignores it. The Task 1
test calls `await ing.poll()` (offset defaults to None), which is fine.

If the M1 `MemoryIngest`/tests construct `IncomingMessage` positionally, update those call sites to the new field set (do not change their intent).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_async_ports.py tests/test_ports.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stash/ports.py tests/test_async_ports.py
git commit -m "feat: add async delivery/ingest ports and fakes; add update_id/chat_type"
```

---

### Task 2: Async reminder scheduler redesign

**Files:**
- Modify: `stash/storage.py` (new claim/attempt/defer ops)
- Modify: `stash/reminders.py` (async `run_due`, one-at-a-time, attempt-at-send, backoff)
- Modify: `tests/test_reminders.py` (migrate to async; add reproduce-first tests)

**Interfaces:**
- Consumes: `Storage`, `AsyncDelivery`, existing reminder columns (status, fire_at, claimed_at, attempts, sent_at) and `reminder_claim_token`.
- Produces:
  - Storage:
    - `claim_one_due_reminder(now, lease_seconds, max_attempts) -> sqlite3.Row | None`: claim the single oldest due, pending, `attempts < max_attempts`, unowned-or-lease-expired reminder by setting `claimed_at = now`; returns the row (with the new `claimed_at` token) or None. Does NOT increment attempts.
    - `record_attempt(rid, expected_claimed_at) -> bool`: `UPDATE reminders SET attempts = attempts + 1 WHERE id=? AND claimed_at=?`; returns True if it applied (ownership held). Committed.
    - `defer_reminder(rid, now, max_attempts, expected_claimed_at) -> None`: on a failed send, CAS on `claimed_at`: if the row's `attempts >= max_attempts` set `status='failed', claimed_at=NULL`; else set `claimed_at = now` (a fresh lease window that also serves as the retry backoff, so it is not reclaimable for `lease_seconds`). Keep `mark_reminder_sent(rid, when, expected_claimed_at)` from M1.
    - `fail_exhausted_reminders(now, max_attempts, lease_seconds)` stays (lease-aware) for reminders stuck pending at the cap.
  - `reminders.render_reminder(storage, row) -> str`: the note's raw text (join `notes` by `note_id`), truncated to 4096 chars; a short prefix like "reminder: " is fine.
  - `async def run_due(storage, delivery, now, lease_seconds, max_attempts) -> int`: drains due reminders one at a time; returns the count delivered.

- [ ] **Step 1: Write the failing tests (async)**

```python
# tests/test_reminders.py  (migrate existing tests to async + add these)
from datetime import datetime, timedelta, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.reminders import run_due
from stash.storage import Storage


class RecordingDelivery:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, chat_id, text):
        if self.fail:
            raise RuntimeError("outage")
        self.sent.append((chat_id, text))


def _at(minute):
    return datetime(2026, 9, 25, 0, minute, tzinfo=timezone.utc).isoformat()


def _due(tmp_path, n=1):
    s = Storage.open(str(tmp_path / "t.db"))
    for i in range(n):
        capture(s, FakeEmbedder(), f"remind me in 30min to task{i}", "cli", _at(0))
    return s


async def test_delivers_each_due_reminder_once(tmp_path):
    s = _due(tmp_path, n=2)
    d = RecordingDelivery()
    assert await run_due(s, d, _at(31), 120, 5) == 2
    assert len(d.sent) == 2
    assert await run_due(s, d, _at(32), 120, 5) == 0  # already sent


async def test_reminder_text_is_the_note_not_the_id(tmp_path):
    s = _due(tmp_path, n=1)
    d = RecordingDelivery()
    await run_due(s, d, _at(31), 120, 5)
    assert "task0" in d.sent[0][1]


async def test_outage_does_not_exhaust_cap_in_one_tick(tmp_path):
    # One tick with an always-failing delivery must attempt each reminder at
    # most once (the backoff defers it), not spin to the cap in a single tick.
    s = _due(tmp_path, n=1)
    d = RecordingDelivery(fail=True)
    await run_due(s, d, _at(31), 120, 5)
    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert row["attempts"] == 1          # exactly one attempt this tick
    assert row["status"] == "pending"    # deferred, not failed


async def test_backoff_then_retry_on_later_tick(tmp_path):
    s = _due(tmp_path, n=1)
    d = RecordingDelivery(fail=True)
    await run_due(s, d, _at(31), 120, 5)          # attempt 1, then deferred (leased)
    await run_due(s, d, _at(32), 120, 5)          # within backoff -> no new attempt
    assert s.conn.execute("SELECT attempts FROM reminders").fetchone()["attempts"] == 1
    d.fail = False
    await run_due(s, d, _at(34), 120, 5)          # 00:34 > 00:31 + 120s -> retry
    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert d.sent and row["status"] == "sent" and row["attempts"] == 2
```

Note to implementer: when you MIGRATE the M1 `test_send_failure_retries_then_fails_after_max`, use a SHORT lease (e.g. 1s) so each tick is past the backoff window and the reminder is re-attempted once per tick until it reaches the cap and becomes `failed`. That test must still prove the cap is enforced, now spaced by the backoff.

Also MIGRATE the existing M1 reminder tests in this file to `async def` + `await run_due(...)`, and replace the old `FakeDelivery.send` (sync) with an async `send`. Do NOT weaken their assertions (fire-once, not-yet-due, retry-then-fail-after-max, lease/ownership, no-clobber, the fail_exhausted-owned-final-attempt case). The at-least-once no-txn-across-send and stale-tick cases keep their meaning under the async form.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: FAIL (run_due not async / new storage ops missing).

- [ ] **Step 3: Implement storage ops**

Add to `stash/storage.py` (mirror the M1 lease/cutoff computation):

```python
    def claim_one_due_reminder(self, now, lease_seconds, max_attempts):
        from datetime import datetime, timedelta
        cutoff = (datetime.fromisoformat(now)
                  - timedelta(seconds=lease_seconds)).isoformat()
        with self.conn:
            return self.conn.execute(
                "UPDATE reminders SET claimed_at=?"
                " WHERE id = ("
                "   SELECT id FROM reminders"
                "   WHERE status='pending' AND fire_at<=? AND attempts<?"
                "     AND (claimed_at IS NULL OR claimed_at<?)"
                "   ORDER BY fire_at, id LIMIT 1)"
                " RETURNING *",
                (now, now, max_attempts, cutoff),
            ).fetchone()

    def record_attempt(self, rid, expected_claimed_at) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE reminders SET attempts=attempts+1"
                " WHERE id=? AND claimed_at=?",
                (rid, expected_claimed_at))
            return cur.rowcount == 1

    def defer_reminder(self, rid, now, max_attempts, expected_claimed_at) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET"
                " status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,"
                " claimed_at=CASE WHEN attempts>=? THEN NULL ELSE ? END"
                " WHERE id=? AND claimed_at=?",
                (max_attempts, max_attempts, now, rid, expected_claimed_at))
```

Keep `mark_reminder_sent`, `reminder_claim_token`, `fail_exhausted_reminders` from M1 (fail_exhausted stays lease-aware; it is still called each tick to terminalize genuinely-stuck at-cap rows).

- [ ] **Step 4: Implement the async scheduler**

Rewrite `stash/reminders.py`:

```python
"""Reminder delivery: async, one at a time, attempt-at-send, backoff, at-least-once."""

from datetime import datetime, timezone

_MAX_MSG = 4096


def render_reminder(storage, row) -> str:
    note = storage.get_note(row["note_id"])
    body = note["raw"] if note else ""
    return ("reminder: " + body)[:_MAX_MSG]


async def run_due(storage, delivery, now: str, lease_seconds: int,
                  max_attempts: int) -> int:
    storage.fail_exhausted_reminders(now, max_attempts, lease_seconds)
    delivered = 0
    while True:
        row = storage.claim_one_due_reminder(now, lease_seconds, max_attempts)
        if row is None:
            break
        token = row["claimed_at"]
        # Count the attempt durably BEFORE the send, ownership-checked, so a
        # crash-loop is bounded. (A crash between here and the send counts an
        # attempt that may not have delivered; this is the documented bounded
        # at-least-once tradeoff.)
        if not storage.record_attempt(row["id"], token):
            continue  # lost ownership; another tick has it
        try:
            await delivery.send(row["chat_id"], render_reminder(storage, row))
        except Exception:
            # Defer: leave leased for lease_seconds (retry backoff) or fail at cap.
            storage.defer_reminder(row["id"], now, max_attempts, token)
        else:
            storage.mark_reminder_sent(
                row["id"], datetime.now(timezone.utc).isoformat(), token)
            delivered += 1
    return delivered
```

Note: because a deferred (failed) reminder keeps `claimed_at=now`, it is not reclaimable until `now + lease_seconds`, so the `while` drain loop will not re-pick it this tick. That is what bounds a single-tick outage to one attempt per reminder.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_reminders.py -v`
Expected: PASS (migrated M1 tests + the new backoff/one-attempt tests). Then run the full suite `uv run pytest -q` and fix any caller of the old sync `run_due` (there are none outside tests until Task 6).

- [ ] **Step 6: Commit**

```bash
git add stash/storage.py stash/reminders.py tests/test_reminders.py
git commit -m "feat: async reminder scheduler (one-at-a-time, attempt-at-send, backoff)"
```

---

### Task 3: Async Telegram API client

**Files:**
- Create: `stash/telegram_client.py`
- Test: `tests/test_telegram_client.py`

**Interfaces:**
- Consumes: `httpx`.
- Produces `TelegramClient`:
  - `TelegramClient(token, base_url="https://api.telegram.org", client=None)` (an injected `httpx.AsyncClient` for tests).
  - `async def get_updates(offset: int | None, timeout: int = 25) -> list[dict]`: calls `getUpdates`, returns the `result` list of update dicts.
  - `async def send_message(chat_id: str, text: str) -> None`: calls `sendMessage`.
  - Never logs the token or `text`. Raises on non-ok responses with a message that does NOT include the token or the text body.

- [ ] **Step 1: Write the failing test (httpx MockTransport, no network)**

```python
# tests/test_telegram_client.py
import httpx
from stash.telegram_client import TelegramClient


def _mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.telegram.org")


async def test_get_updates_returns_result_list():
    def handler(request):
        assert "getUpdates" in request.url.path
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})
    tc = TelegramClient("TOKEN", client=_mock(handler))
    assert await tc.get_updates(offset=None) == [{"update_id": 5}]


async def test_send_message_posts_chat_and_text():
    seen = {}
    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})
    tc = TelegramClient("TOKEN", client=_mock(handler))
    await tc.send_message("c1", "hello")
    assert seen["chat_id"] == "c1" and seen["text"] == "hello"


async def test_error_does_not_leak_token_or_text():
    def handler(request):
        return httpx.Response(403, json={"ok": False, "description": "forbidden"})
    tc = TelegramClient("SECRET-TOKEN", client=_mock(handler))
    try:
        await tc.send_message("c1", "secret note body")
    except Exception as e:
        assert "SECRET-TOKEN" not in str(e)
        assert "secret note body" not in str(e)
    else:
        raise AssertionError("expected an error")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_telegram_client.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the client**

```python
# stash/telegram_client.py
"""Async Telegram Bot API client. Never logs the token or message text."""

import httpx


class TelegramClient:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org",
                 client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._own = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url)

    def _path(self, method: str) -> str:
        return f"/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict) -> dict:
        resp = await self._client.post(self._path(method), json=payload)
        if resp.status_code != 200:
            # Do not include the token (in the URL) or the payload text.
            raise RuntimeError(f"telegram {method} failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} not ok")
        return data

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload: dict = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = await self._call("getUpdates", payload)
        return data.get("result", [])

    async def send_message(self, chat_id: str, text: str) -> None:
        await self._call("sendMessage", {"chat_id": chat_id, "text": text})

    async def aclose(self) -> None:
        if self._own:
            await self._client.aclose()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_telegram_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stash/telegram_client.py tests/test_telegram_client.py
git commit -m "feat: async telegram bot api client (token/text never logged)"
```

---

### Task 4: Command dispatch registry and handlers

**Files:**
- Create: `stash/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `capture` (M1), `search` (M1), `Storage`, an `Embedder`.
- Produces:
  - `handle_message(storage, embedder, msg: IncomingMessage, now: str) -> str | None`: routes the message and returns the reply text to send (or None when nothing to reply, e.g. an empty message). Registered commands: `/find`, `/recent`, `/help`. Any non-command text is captured. A message whose first token starts with `/` but is not a registered command is captured verbatim.
  - Replies are truncated to 4096 chars.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands.py
from datetime import datetime, timezone
from stash.commands import handle_message
from stash.embed import FakeEmbedder
from stash.ports import IncomingMessage
from stash.storage import Storage


def _msg(text, uid=1):
    return IncomingMessage(update_id=uid, chat_id="c1", chat_type="private",
                           sender_id="s1", msg_id=str(uid), text=text)


def _now():
    return datetime(2026, 9, 25, tzinfo=timezone.utc).isoformat()


def test_plain_text_is_captured_and_acked(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    reply = handle_message(s, FakeEmbedder(), _msg("buy milk #home"), _now())
    assert "stashed" in reply.lower()
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_note_starting_with_unknown_slash_is_captured_verbatim(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    handle_message(s, FakeEmbedder(), _msg("/todo call the vendor"), _now())
    row = s.conn.execute("SELECT raw FROM notes").fetchone()
    assert row["raw"] == "/todo call the vendor"


def test_find_returns_matches(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    handle_message(s, FakeEmbedder(), _msg("the quick brown fox"), _now())
    reply = handle_message(s, FakeEmbedder(), _msg("/find brown fox"), _now())
    assert "brown fox" in reply


def test_find_empty_and_no_hits(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert "nothing" in handle_message(s, FakeEmbedder(), _msg("/find zzz"), _now()).lower()


def test_help_and_empty(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    assert "/find" in handle_message(s, FakeEmbedder(), _msg("/help"), _now())
    assert handle_message(s, FakeEmbedder(), _msg("   "), _now()) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_commands.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the dispatch**

```python
# stash/commands.py
"""Telegram command dispatch. Plain text captures; /commands route. No chat."""

from stash.capture import capture
from stash.recall import search

_MAX = 4096
_COMMANDS = ("/find", "/recent", "/help")


def _tiles(results) -> str:
    if not results:
        return "nothing found."
    lines = []
    for r in results:
        tags = (" " + " ".join(f"#{t}" for t in r.tags)) if r.tags else ""
        lines.append(f"[{r.created_at[:10]}] {r.raw}{tags}")
    return "\n\n".join(lines)


def handle_message(storage, embedder, msg, now: str) -> str | None:
    text = msg.text or ""
    if not text.strip():
        return None
    first = text.split(maxsplit=1)[0]
    if first in _COMMANDS:
        rest = text[len(first):].strip()
        if first == "/help":
            reply = "commands: /find <query>, /recent, /help. Any other text is stashed."
        elif first == "/find":
            reply = _tiles(search(storage, embedder, rest, limit=5))
        elif first == "/recent":
            rows = storage.conn.execute(
                "SELECT raw, created_at FROM notes ORDER BY id DESC LIMIT 5").fetchall()
            reply = "\n\n".join(f"[{r['created_at'][:10]}] {r['raw']}" for r in rows) or "no notes yet."
        return reply[:_MAX]
    # Not a registered command (including an unknown leading '/') -> capture.
    result = capture(storage, embedder, text, "telegram", now,
                     source_chat_id=msg.chat_id, source_msg_id=msg.msg_id)
    return result.receipt[:_MAX]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_commands.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stash/commands.py tests/test_commands.py
git commit -m "feat: telegram command dispatch (capture default, /find /recent /help)"
```

---

### Task 5: Telegram ingest + delivery adapters

**Files:**
- Create: `stash/telegram_adapter.py`
- Test: `tests/test_telegram_adapter.py`

**Interfaces:**
- Consumes: `TelegramClient`, `IncomingMessage`, config (allowed sender ids).
- Produces:
  - `parse_update(update: dict) -> IncomingMessage | None`: extract `update_id`, `message.chat.id`, `message.chat.type`, `message.from.id`, `message.text`; return None for non-message or non-text updates (but the caller still advances the offset past them, see Task 6).
  - `is_authorized(msg, allowed_ids) -> bool`: True only when `msg.chat_type == "private"` and `msg.sender_id in allowed_ids`.
  - `TelegramIngest(client, allowed_ids)`: `async def poll(offset)` -> `list[IncomingMessage]` (raw parsed, in update_id order; authorization is applied by the daemon so unauthorized updates still advance the offset).
  - `TelegramDelivery(client)`: `async def send(chat_id, text)` with a small bounded retry.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telegram_adapter.py
from stash.telegram_adapter import parse_update, is_authorized


def _update(uid, chat_type="private", sender="42", text="hi", chat="1000"):
    return {"update_id": uid, "message": {"message_id": uid,
            "chat": {"id": chat, "type": chat_type},
            "from": {"id": sender}, "text": text}}


def test_parse_update_extracts_fields():
    m = parse_update(_update(7, text="buy milk"))
    assert (m.update_id, m.chat_type, m.sender_id, m.text) == (7, "private", "42", "buy milk")


def test_parse_update_none_for_non_text():
    assert parse_update({"update_id": 8}) is None
    assert parse_update({"update_id": 9, "message": {"chat": {"id": "1", "type": "private"}}}) is None


def test_authorized_requires_private_and_allowlist():
    allowed = ("42",)
    assert is_authorized(parse_update(_update(1)), allowed) is True
    assert is_authorized(parse_update(_update(2, sender="99")), allowed) is False
    assert is_authorized(parse_update(_update(3, chat_type="group")), allowed) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_telegram_adapter.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the adapter**

```python
# stash/telegram_adapter.py
"""Bind the Telegram client to the stash async ports."""

import asyncio

from stash.ports import IncomingMessage


def parse_update(update: dict) -> IncomingMessage | None:
    msg = update.get("message")
    if not msg or "text" not in msg:
        return None
    chat = msg.get("chat", {})
    frm = msg.get("from", {})
    if "id" not in chat or "id" not in frm:
        return None
    return IncomingMessage(
        update_id=int(update["update_id"]),
        chat_id=str(chat["id"]),
        chat_type=str(chat.get("type", "")),
        sender_id=str(frm["id"]),
        msg_id=str(msg.get("message_id", "")),
        text=msg["text"],
    )


def is_authorized(msg: IncomingMessage, allowed_ids) -> bool:
    return msg.chat_type == "private" and msg.sender_id in tuple(allowed_ids)


class TelegramIngest:
    def __init__(self, client, allowed_ids) -> None:
        self._client = client
        self._allowed = tuple(allowed_ids)

    async def poll(self, offset):
        raw = await self._client.get_updates(offset)
        out = []
        for u in raw:
            m = parse_update(u)
            if m is not None:
                out.append(m)
            elif "update_id" in u:
                out.append(IncomingMessage(int(u["update_id"]), "", "", "", "", ""))
        return out


class TelegramDelivery:
    def __init__(self, client, attempts: int = 3) -> None:
        self._client = client
        self._attempts = attempts

    async def send(self, chat_id: str, text: str) -> None:
        last = None
        for _ in range(self._attempts):
            try:
                await self._client.send_message(chat_id, text)
                return
            except Exception as e:  # transient; retry a couple times
                last = e
                await asyncio.sleep(0)
        raise last
```

Note: `poll` returns a sentinel `IncomingMessage` (empty text, sender "") for non-text/parse-None updates so the daemon can advance the offset past them without acting; the daemon treats an empty-text or unauthorized message as "skip, still ack". Confirm this shape against Task 6's loop and adjust if a cleaner representation (e.g. returning `(update_id, message_or_None)` pairs) reads better; keep the offset-past-skipped behavior either way.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_telegram_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stash/telegram_adapter.py tests/test_telegram_adapter.py
git commit -m "feat: telegram ingest/delivery adapters with private+allowlist auth"
```

---

### Task 6: The `stash serve` daemon

**Files:**
- Create: `stash/serve.py`
- Modify: `stash/cli.py` (add the `serve` subcommand)
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: everything above, `Config`, `Storage`, `SentenceTransformerEmbedder`, `repair`, `reindex`, `handle_message`, `run_due`.
- Produces:
  - `async def handle_updates(storage, embedder, delivery, ingest, allowed_ids, now_fn) -> None`: one poll+dispatch pass. Polls from the stored offset (`kv` key `tg_offset`); for each update in order: if it is an authorized private text message, `handle_message` and `await delivery.send(reply)`; otherwise skip; then advance the `kv` offset to `update_id + 1`. The offset advances per-update after full handling.
  - `async def scheduler_tick(storage, delivery, lease, max_attempts, now_fn, lock) -> None`: acquire the async lock (skip if held), then `await run_due(...)`.
  - `acquire_single_instance(db_path) -> lock_handle`: an OS file lock next to the DB; raise/exit if another `serve` holds it.
  - `async def serve(config) -> None`: acquire the lock, open storage, build the real embedder, run startup `repair` + embed-model reconcile, build the Telegram client/adapters, then run the poller loop and the scheduler loop as tasks until SIGINT/SIGTERM; clean shutdown.

- [ ] **Step 1: Write the failing tests (fakes, no network)**

```python
# tests/test_serve.py
from datetime import datetime, timezone
from stash.serve import handle_updates
from stash.embed import FakeEmbedder
from stash.ports import IncomingMessage, FakeAsyncDelivery
from stash.storage import Storage


class FakeIngest:
    def __init__(self, batches):
        self._batches = list(batches)  # list of lists of IncomingMessage
    async def poll(self, offset):
        return self._batches.pop(0) if self._batches else []


def _msg(uid, text, chat_type="private", sender="42"):
    return IncomingMessage(uid, "1000", chat_type, sender, str(uid), text)


def _now():
    return datetime(2026, 9, 25, tzinfo=timezone.utc).isoformat()


async def test_authorized_capture_and_offset_advance(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    d = FakeAsyncDelivery()
    ing = FakeIngest([[_msg(5, "buy milk")]])
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    assert s.kv_get("tg_offset") == "6"          # update_id + 1
    assert len(d.sent) == 1                       # receipt sent


async def test_group_message_dropped_but_offset_advances(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    d = FakeAsyncDelivery()
    ing = FakeIngest([[_msg(7, "/find secret", chat_type="group")]])
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    assert d.sent == []                           # no leak to the group
    assert s.kv_get("tg_offset") == "8"           # still advanced past it


async def test_dedupe_on_redelivered_update(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    d = FakeAsyncDelivery()
    # same message delivered in two polls (simulates a crash before ack)
    ing = FakeIngest([[_msg(5, "buy milk")], [_msg(5, "buy milk")]])
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    await handle_updates(s, FakeEmbedder(), d, ing, ("42",), _now)
    assert s.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_serve.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `handle_updates` and the scheduler tick**

```python
# stash/serve.py (partial; the loop/daemon wiring is Step 5)
from stash.commands import handle_message
from stash.telegram_adapter import is_authorized
from stash.reminders import run_due


async def handle_updates(storage, embedder, delivery, ingest, allowed_ids, now_fn):
    offset_raw = storage.kv_get("tg_offset")
    offset = int(offset_raw) if offset_raw else None
    for msg in await ingest.poll(offset):
        if msg.text and is_authorized(msg, allowed_ids):
            reply = handle_message(storage, embedder, msg, now_fn())
            if reply is not None:
                await delivery.send(msg.chat_id, reply)
        # else: unauthorized / group / non-text -> skip, but still advance.
        storage.kv_set("tg_offset", str(msg.update_id + 1))


async def scheduler_tick(storage, delivery, lease, max_attempts, now_fn, lock):
    if lock.locked():
        return
    async with lock:
        await run_due(storage, delivery, now_fn(), lease, max_attempts)
```

Note: `handle_message` performs the capture (which offloads the encode). In the daemon (Step 5), wrap the encode via `asyncio.to_thread` if the synchronous encode stalls the loop; for the fake-embedder tests it is instant. The offset advance uses `msg.update_id + 1` per fully-handled update.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_serve.py -v`
Expected: PASS.

- [ ] **Step 5: Implement the daemon and single-instance guard**

Add to `stash/serve.py`:

```python
import asyncio
import signal
from datetime import datetime, timezone

from stash.capture import repair
from stash.reindex import reindex
from stash.storage import Storage
from stash.telegram_client import TelegramClient
from stash.telegram_adapter import TelegramIngest, TelegramDelivery


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def acquire_single_instance(db_path: str):
    import fcntl
    lock_path = db_path + ".serve.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise RuntimeError(
            "another `stash serve` is already running on this database") from exc
    return fh


async def serve(config) -> None:
    lock = acquire_single_instance(config.db_path)
    storage = Storage.open(config.db_path, config.busy_timeout_ms)
    from stash.embed import SentenceTransformerEmbedder
    embedder = SentenceTransformerEmbedder(config.embed_model)
    # startup self-heal + embed-model reconcile (same policy as the CLI)
    if storage.underived_note_ids():
        repair(storage, embedder)
    stored = storage.kv_get("embed_model")
    if stored is None:
        storage.kv_set("embed_model", embedder.name)
    elif stored != embedder.name:
        reindex(storage, embedder)
        storage.kv_set("embed_model", embedder.name)

    client = TelegramClient(config.telegram_bot_token)
    ingest = TelegramIngest(client, config.allowed_sender_ids)
    delivery = TelegramDelivery(client)
    sched_lock = asyncio.Lock()
    stop = asyncio.Event()

    def _request_stop(*_):
        stop.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    async def poll_loop():
        while not stop.is_set():
            try:
                await handle_updates(storage, embedder, delivery, ingest,
                                     config.allowed_sender_ids, _utc_now)
            except Exception:
                await asyncio.sleep(1)

    async def sched_loop():
        while not stop.is_set():
            await scheduler_tick(storage, delivery, config.reminder_lease_seconds,
                                 config.reminder_max_attempts, _utc_now, sched_lock)
            await asyncio.sleep(config.scheduler_tick_seconds)

    try:
        await asyncio.gather(poll_loop(), sched_loop())
    finally:
        await client.aclose()
        lock.close()
```

Also add a `serve` subcommand to `stash/cli.py` that loads config and runs `asyncio.run(serve(cfg))`.

- [ ] **Step 6: Run to verify pass + full suite**

Run: `uv run pytest -q`
Expected: all pass. Add a focused test for `acquire_single_instance` (second acquire on the same path raises) if practical.

- [ ] **Step 7: Commit**

```bash
git add stash/serve.py stash/cli.py tests/test_serve.py
git commit -m "feat: stash serve daemon (poller + scheduler, single-instance, per-update ack)"
```

---

### Task 7: Setup runbook and docs-current

**Files:**
- Create: `docs/guides/telegram-setup.md`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Write the setup runbook**

Create `docs/guides/telegram-setup.md` with the exact steps: create a bot via @BotFather and copy the token; set `STASH_TELEGRAM_BOT_TOKEN` in `.env`; get your numeric Telegram user id (message `@userinfobot`) and set `STASH_ALLOWED_SENDER_IDS`; run `python -m stash serve` (and a note on keeping it alive with systemd or tmux). State plainly that the bot only responds to allow-listed users in private chats, and that notes pass through Telegram's servers.

- [ ] **Step 2: Update the roadmap**

In `docs/ROADMAP.md`, move T1 from "current" toward shipped once merged (leave the shipped move for the DOCUMENT step at gate time), and check off the M2 must-fix scheduler items now that the redesign is implemented. No em-dashes.

- [ ] **Step 3: Commit**

```bash
git add docs/guides/telegram-setup.md docs/ROADMAP.md
git commit -m "docs: telegram setup runbook and roadmap update for M2"
```

---

## Definition of done (Milestone 2)

- All tasks committed; `uv run pytest -q` green.
- The five Review Focus items each have a passing test in their owning task.
- Spec §8 acceptance criteria are each covered by a named test.
- The scheduler redesign closes the M1 Codex finding (no due reminder recorded `failed` without a send; an outage does not exhaust the cap in one tick), proven by reproduce-first tests.
- Manual live smoke (a real bot token, a real message, a real reminder) is run as a gate step and its result recorded, before the milestone is called done.
- Docs-current: roadmap + shipped log updated at gate time per the dev-loop rule.

---
cam
