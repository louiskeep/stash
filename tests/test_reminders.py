from datetime import datetime, timezone

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
    assert len(d.sent) == 2  # no repeated send on the follow-up tick


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


async def test_not_yet_due_is_not_delivered(tmp_path):
    s = _due(tmp_path)
    d = RecordingDelivery()
    assert await run_due(s, d, _at(10), lease_seconds=120, max_attempts=5) == 0
    assert d.sent == []


async def test_send_failure_retries_then_fails_after_max(tmp_path):
    # Short lease: each tick is past the backoff window, so the reminder is
    # re-attempted once per tick until it reaches the cap and is failed --
    # this still proves the cap is enforced, now spaced by the backoff.
    s = _due(tmp_path)
    d = RecordingDelivery(fail=True)
    for m in range(31, 45):  # keep ticking; lease expires so it is reclaimable
        await run_due(s, d, _at(m), lease_seconds=1, max_attempts=3)
    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] >= 3


async def test_no_transaction_held_open_across_delivery_send(tmp_path):
    # Spec 10: the storage transaction that claims a reminder (and the one
    # that records its attempt) must close before Delivery.send runs, so a
    # slow or failing send never holds a SQLite lock across a network call.
    s = _due(tmp_path)

    class AssertingDelivery:
        def __init__(self, storage):
            self.storage = storage
            self.sent = []

        async def send(self, chat_id, text):
            assert self.storage.conn.in_transaction is False
            self.sent.append((chat_id, text))

    d = AssertingDelivery(s)
    assert await run_due(s, d, _at(31), lease_seconds=120, max_attempts=5) == 1
    assert len(d.sent) == 1


def test_mark_reminder_sent_reports_whether_its_cas_applied(tmp_path):
    # run_due only counts a delivery when this returns True, so a stale
    # tick's no-op mark must not be mistaken for a real completion.
    s = _due(tmp_path)
    row = s.claim_one_due_reminder(_at(31), lease_seconds=120, max_attempts=5)
    assert row is not None

    stale_token = "not-the-real-claimed-at"
    assert s.mark_reminder_sent(row["id"], _at(32), stale_token) is False
    still_pending = s.conn.execute(
        "SELECT status FROM reminders WHERE id=?", (row["id"],)).fetchone()
    assert still_pending["status"] == "pending"  # the no-op left it untouched

    assert s.mark_reminder_sent(row["id"], _at(32), row["claimed_at"]) is True
    sent = s.conn.execute(
        "SELECT status FROM reminders WHERE id=?", (row["id"],)).fetchone()
    assert sent["status"] == "sent"


def test_lease_blocks_double_claim_within_lease(tmp_path):
    s = _due(tmp_path)
    # First claim leases it; a second immediate claim (still under lease,
    # simulating a concurrent tick) claims nothing.
    first = s.claim_one_due_reminder(_at(31), lease_seconds=120, max_attempts=5)
    second = s.claim_one_due_reminder(_at(31), lease_seconds=120, max_attempts=5)
    assert first is not None
    assert second is None


async def test_stale_tick_completion_does_not_clobber_a_newer_sent_row(tmp_path):
    # Models the gate's repro: tick 1 claims at 00:31. Its send() runs long
    # enough that the lease (120s, cutoff 00:32) expires, so a nested tick
    # at 00:34 reclaims the same reminder and delivers it successfully.
    # Tick 1's own send() then fails. Without lease-owned completion, tick
    # 1's failure handler flips the row tick 2 already marked 'sent' back
    # to 'pending', and a later tick resends it.
    s = _due(tmp_path)
    inner_delivery = RecordingDelivery()

    class OutlastingDelivery:
        """Simulates a send that outlives its lease: a nested tick reclaims
        and delivers before this send finally fails."""

        def __init__(self, storage, inner):
            self.storage = storage
            self.inner = inner

        async def send(self, chat_id, text):
            await run_due(self.storage, self.inner, _at(34),
                          lease_seconds=120, max_attempts=5)
            raise RuntimeError("outlasted its lease")

    await run_due(s, OutlastingDelivery(s, inner_delivery), _at(31),
                  lease_seconds=120, max_attempts=5)

    assert len(inner_delivery.sent) == 1
    row = s.conn.execute("SELECT status FROM reminders").fetchone()
    assert row["status"] == "sent"  # not resurrected to 'pending' by the stale tick

    later_delivery = RecordingDelivery()
    assert await run_due(s, later_delivery, _at(40), lease_seconds=120, max_attempts=5) == 0
    assert later_delivery.sent == []  # no repeated send


async def test_stale_tick_full_drain_during_send_does_not_double_send_other_reminder(tmp_path):
    # Gate finding 1 (M1): claim_due_reminders claimed a whole BATCH up
    # front, then sent each row in a loop; a slow first send could outlast
    # the lease and let a nested tick reclaim and deliver rows already in
    # this tick's batch but not yet reached. M2's one-at-a-time claim closes
    # this window by construction: this tick never claims a second row
    # until the first is fully resolved (sent or deferred). So if a nested
    # full drain -- kicked off from inside the first row's send -- delivers
    # a second reminder before this tick gets to it, this tick's own claim
    # for that second reminder simply finds nothing left pending; there is
    # no ownership recheck to fall back on because there is no pre-claimed
    # batch to recheck.
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    capture(s, e, "remind me in 30min to stretch", "cli", _at(0))
    capture(s, e, "remind me in 30min to call mom", "cli", _at(0))

    sent_log: list[tuple[str, str, str]] = []  # (tag, chat_id, text)

    class TaggedDelivery:
        def __init__(self, tag):
            self.tag = tag

        async def send(self, chat_id, text):
            sent_log.append((self.tag, chat_id, text))

    class TickAOutlastsLease(TaggedDelivery):
        """Tick A's first send outlives its lease: before it returns, a
        nested tick B (past the lease) reclaims and delivers whatever is
        currently due, including the reminder A hasn't reached yet. Tick
        A's own send then finally completes."""

        def __init__(self, storage, inner):
            super().__init__("A")
            self.storage = storage
            self.inner = inner
            self.calls = 0

        async def send(self, chat_id, text):
            self.calls += 1
            if self.calls == 1:
                await run_due(self.storage, self.inner, _at(34),
                              lease_seconds=120, max_attempts=5)
            await super().send(chat_id, text)

    tick_b_delivery = TaggedDelivery("B")
    tick_a_delivery = TickAOutlastsLease(s, tick_b_delivery)
    await run_due(s, tick_a_delivery, _at(31), lease_seconds=120, max_attempts=5)

    by_text: dict[str, list[str]] = {}
    for tag, _chat_id, text in sent_log:
        by_text.setdefault(text, []).append(tag)

    assert len(by_text) == 2  # both reminders were delivered at least once
    counts = sorted(len(tags) for tags in by_text.values())
    # The reminder whose in-flight send triggered the nested drain is sent
    # twice (by both A and B) -- the inherent, spec-documented at-least-once
    # duplicate. The other reminder, which A had not yet claimed when B ran,
    # is sent exactly once: A's later claim for it finds nothing pending.
    assert counts == [1, 2]
    once_sent = next(tags for tags in by_text.values() if len(tags) == 1)
    assert once_sent == ["B"]


async def test_crash_before_defer_does_not_bypass_attempt_cap(tmp_path):
    # Gate finding 2 (M1): the old claim incremented attempts on every claim
    # but never filtered on max_attempts, so a reminder whose tick crashed
    # or hung before it could release/defer the claim (status stays
    # 'pending', never flips to 'failed') got reclaimed and re-sent without
    # limit. M2 counts the attempt (record_attempt, committed) BEFORE the
    # awaited send, so this still holds: a BaseException (not caught by
    # run_due's `except Exception`) models a tick that dies mid-send, before
    # its own defer logic runs, and the cap still bounds it because the
    # attempt was already durable.
    s = _due(tmp_path)

    class Crash(BaseException):
        pass

    class CrashingDelivery:
        def __init__(self):
            self.calls = 0

        async def send(self, chat_id, text):
            self.calls += 1
            raise Crash("tick died before it could defer the claim")

    d = CrashingDelivery()
    max_attempts = 3
    for m in range(31, 60):  # far more ticks than max_attempts; short lease
        try:                 # keeps it reclaimable every time despite the
            await run_due(s, d, _at(m), lease_seconds=1, max_attempts=max_attempts)
        except Crash:
            pass  # the tick "crashed"; a fresh tick resumes next minute

    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert d.calls <= max_attempts
    assert row["attempts"] <= max_attempts
    assert row["status"] == "failed"


def test_fail_exhausted_reminders_does_not_clobber_an_owned_final_attempt(tmp_path):
    # R2 re-review finding: fail_exhausted_reminders matched status='pending'
    # AND fire_at<=? AND attempts>=? with NO lease guard, unlike the claim
    # (which only touches rows where claimed_at IS NULL OR claimed_at<cutoff).
    # So calling it at the start of a concurrent tick could terminalize a
    # reminder ANOTHER tick currently owns on its final attempt, still well
    # within its lease: flip it to 'failed' and clear claimed_at out from
    # under the owner. The owner's send then succeeds, but its
    # mark_reminder_sent CAS (WHERE id=? AND claimed_at=?) no longer matches
    # (claimed_at is now NULL), so it silently no-ops, leaving a delivered
    # reminder recorded as 'failed'.
    s = _due(tmp_path)
    max_attempts = 3
    lease_seconds = 1  # short, so each 1-minute tick is past the backoff

    # Drive attempts to max_attempts-1 via ordinary claim/record/defer
    # cycles that never trip the cap (each models a failed send that gets
    # backed off, not exhausted).
    for m in range(31, 33):
        row = s.claim_one_due_reminder(_at(m), lease_seconds, max_attempts)
        assert row is not None
        assert s.record_attempt(row["id"], row["claimed_at"])
        s.defer_reminder(row["id"], _at(m), max_attempts, row["claimed_at"])

    # The final attempt: this claim brings attempts to max_attempts. The
    # owner now holds a live lease starting at owner_now.
    owner_now = _at(33)
    row = s.claim_one_due_reminder(owner_now, lease_seconds, max_attempts)
    assert row is not None
    owner_token = row["claimed_at"]
    assert owner_token == owner_now
    assert s.record_attempt(row["id"], owner_token)
    attempts_row = s.conn.execute(
        "SELECT attempts FROM reminders WHERE id=?", (row["id"],)).fetchone()
    assert attempts_row["attempts"] == max_attempts

    # A concurrent tick starts at essentially the same moment, well inside
    # the owner's lease, and runs the exhausted-reminder sweep.
    s.fail_exhausted_reminders(owner_now, max_attempts, lease_seconds)

    still_owned = s.conn.execute(
        "SELECT status, claimed_at FROM reminders WHERE id=?", (row["id"],)
    ).fetchone()
    assert still_owned["status"] == "pending"  # not clobbered out from under the owner
    assert still_owned["claimed_at"] == owner_token

    # The owner's send succeeds; its CAS mark must still hold.
    s.mark_reminder_sent(row["id"], _at(34), owner_token)
    final = s.conn.execute(
        "SELECT status FROM reminders WHERE id=?", (row["id"],)
    ).fetchone()
    assert final["status"] == "sent"
