from datetime import datetime, timedelta, timezone

from stash.capture import capture
from stash.embed import FakeEmbedder
from stash.reminders import run_due
from stash.storage import Storage


class FakeDelivery:
    def __init__(self, fail_times=0):
        self.sent = []
        self.fail_times = fail_times

    def send(self, chat_id, text):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient")
        self.sent.append((chat_id, text))


def _at(minute):
    return datetime(2026, 9, 24, 0, minute, tzinfo=timezone.utc).isoformat()


def _due_reminder(tmp_path):
    s = Storage.open(str(tmp_path / "t.db"))
    capture(s, FakeEmbedder(), "remind me in 30min to stretch", "cli", _at(0))
    return s


def test_delivers_due_reminder_once(tmp_path):
    s = _due_reminder(tmp_path)
    d = FakeDelivery()
    assert run_due(s, d, _at(31), lease_seconds=120, max_attempts=5) == 1
    assert len(d.sent) == 1
    # Second pass after it is marked sent delivers nothing.
    assert run_due(s, d, _at(32), lease_seconds=120, max_attempts=5) == 0
    assert len(d.sent) == 1


def test_not_yet_due_is_not_delivered(tmp_path):
    s = _due_reminder(tmp_path)
    d = FakeDelivery()
    assert run_due(s, d, _at(10), lease_seconds=120, max_attempts=5) == 0
    assert d.sent == []


def test_send_failure_retries_then_fails_after_max(tmp_path):
    s = _due_reminder(tmp_path)
    d = FakeDelivery(fail_times=99)
    for m in range(31, 45):  # keep ticking; lease expires so it is reclaimable
        run_due(s, d, _at(m), lease_seconds=1, max_attempts=3)
    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] >= 3


def test_no_transaction_held_open_across_delivery_send(tmp_path):
    # Spec 10: the storage transaction that claims a reminder must close
    # before Delivery.send runs, so a slow or failing send never holds a
    # SQLite lock across a network call.
    s = _due_reminder(tmp_path)

    class AssertingDelivery:
        def __init__(self, storage):
            self.storage = storage
            self.sent = []

        def send(self, chat_id, text):
            assert self.storage.conn.in_transaction is False
            self.sent.append((chat_id, text))

    d = AssertingDelivery(s)
    assert run_due(s, d, _at(31), lease_seconds=120, max_attempts=5) == 1
    assert len(d.sent) == 1


def test_lease_blocks_double_claim_within_lease(tmp_path):
    s = _due_reminder(tmp_path)
    # First claim leases it; a second immediate pass (still under lease,
    # simulating a concurrent tick) claims nothing.
    first = s.claim_due_reminders(_at(31), lease_seconds=120, max_attempts=5)
    second = s.claim_due_reminders(_at(31), lease_seconds=120, max_attempts=5)
    assert len(first) == 1
    assert second == []


def test_stale_tick_completion_does_not_clobber_a_newer_sent_row(tmp_path):
    # Models the gate's repro: tick 1 claims at 00:31. Its send() runs long
    # enough that the lease (120s, cutoff 00:32) expires, so a nested tick
    # at 00:34 reclaims the same reminder and delivers it successfully.
    # Tick 1's own send() then fails. Without lease-owned completion, tick
    # 1's failure handler flips the row tick 2 already marked 'sent' back
    # to 'pending', and a later tick resends it.
    s = _due_reminder(tmp_path)
    inner_delivery = FakeDelivery()

    class OutlastingDelivery:
        """Simulates a send that outlives its lease: a nested tick reclaims
        and delivers before this send finally fails."""

        def __init__(self, storage, inner):
            self.storage = storage
            self.inner = inner

        def send(self, chat_id, text):
            run_due(self.storage, self.inner, _at(34), lease_seconds=120, max_attempts=5)
            raise RuntimeError("outlasted its lease")

    run_due(s, OutlastingDelivery(s, inner_delivery), _at(31),
             lease_seconds=120, max_attempts=5)

    assert len(inner_delivery.sent) == 1
    row = s.conn.execute("SELECT status FROM reminders").fetchone()
    assert row["status"] == "sent"  # not resurrected to 'pending' by the stale tick

    later_delivery = FakeDelivery()
    assert run_due(s, later_delivery, _at(40), lease_seconds=120, max_attempts=5) == 0
    assert later_delivery.sent == []  # no repeated send


def test_stale_tick_skips_send_for_batch_row_reclaimed_mid_send(tmp_path):
    # Gate finding 1: run_due claims a whole batch up front, then sends each
    # claimed row in a loop, without re-checking that it still owns each
    # row's lease at send time. If the FIRST send in the batch runs long
    # enough for the lease to expire, a nested tick can reclaim and deliver
    # the ENTIRE batch (including rows this tick hasn't reached yet) before
    # this tick's loop resumes. The row that triggered the nested reclaim is
    # a documented, unavoidable at-least-once duplicate (its own send is
    # already in flight when the reclaim happens); every OTHER row still
    # queued in this tick's loop must not be sent again.
    s = Storage.open(str(tmp_path / "t.db"))
    e = FakeEmbedder()
    capture(s, e, "remind me in 30min to stretch", "cli", _at(0))
    capture(s, e, "remind me in 30min to call mom", "cli", _at(0))

    sent_log: list[tuple[str, str, str]] = []  # (tag, chat_id, text)

    class TaggedDelivery:
        def __init__(self, tag):
            self.tag = tag

        def send(self, chat_id, text):
            sent_log.append((self.tag, chat_id, text))

    class TickAOutlastsLease(TaggedDelivery):
        """Tick A's first send outlives its lease: before it returns, a
        nested tick B (past the lease) reclaims A's whole in-flight batch
        and delivers it. Tick A's loop then resumes for its remaining
        rows."""

        def __init__(self, storage, inner):
            super().__init__("A")
            self.storage = storage
            self.inner = inner
            self.calls = 0

        def send(self, chat_id, text):
            self.calls += 1
            if self.calls == 1:
                run_due(self.storage, self.inner, _at(34),
                         lease_seconds=120, max_attempts=5)
            super().send(chat_id, text)

    tick_b_delivery = TaggedDelivery("B")
    tick_a_delivery = TickAOutlastsLease(s, tick_b_delivery)
    run_due(s, tick_a_delivery, _at(31), lease_seconds=120, max_attempts=5)

    by_text: dict[str, list[str]] = {}
    for tag, _chat_id, text in sent_log:
        by_text.setdefault(text, []).append(tag)

    assert len(by_text) == 2  # both reminders were delivered at least once
    counts = sorted(len(tags) for tags in by_text.values())
    # The row whose send triggered the nested reclaim is sent twice (by
    # both A and B) -- the inherent residual duplicate. The other row in
    # A's batch must be caught by the ownership recheck: sent exactly
    # once, by B only.
    assert counts == [1, 2]
    once_sent = next(tags for tags in by_text.values() if len(tags) == 1)
    assert once_sent == ["B"]


def test_crash_before_release_does_not_bypass_attempt_cap(tmp_path):
    # Gate finding 2: claim_due_reminders increments attempts on every claim
    # but never filtered on max_attempts, so a reminder whose tick crashes
    # or hangs before it can call release_reminder (status stays 'pending',
    # never flips to 'failed') gets reclaimed and re-sent without limit. A
    # BaseException (not caught by run_due's `except Exception`) models a
    # tick that dies mid-send, before its own release/mark-sent logic runs
    # -- exactly the gap the finding describes.
    s = _due_reminder(tmp_path)

    class Crash(BaseException):
        pass

    class CrashingDelivery:
        def __init__(self):
            self.calls = 0

        def send(self, chat_id, text):
            self.calls += 1
            raise Crash("tick died before it could release the claim")

    d = CrashingDelivery()
    max_attempts = 3
    for m in range(31, 60):  # far more ticks than max_attempts; short lease
        try:                 # keeps it reclaimable every time despite the
            run_due(s, d, _at(m), lease_seconds=1, max_attempts=max_attempts)
        except Crash:
            pass  # the tick "crashed"; a fresh tick resumes next minute

    row = s.conn.execute("SELECT status, attempts FROM reminders").fetchone()
    assert d.calls <= max_attempts
    assert row["attempts"] <= max_attempts
    assert row["status"] == "failed"


def test_fail_exhausted_reminders_does_not_clobber_an_owned_final_attempt(tmp_path):
    # R2 re-review finding: fail_exhausted_reminders matched status='pending'
    # AND fire_at<=? AND attempts>=? with NO lease guard, unlike
    # claim_due_reminders (which only touches rows where claimed_at IS NULL
    # OR claimed_at<cutoff). So calling it at the start of a concurrent tick
    # could terminalize a reminder ANOTHER tick currently owns on its final
    # attempt, still well within its lease: flip it to 'failed' and clear
    # claimed_at out from under the owner. The owner's send then succeeds,
    # but its mark_reminder_sent CAS (WHERE id=? AND claimed_at=?) no longer
    # matches (claimed_at is now NULL), so it silently no-ops, leaving a
    # delivered reminder recorded as 'failed'.
    s = _due_reminder(tmp_path)
    max_attempts = 3
    lease_seconds = 120

    # Drive attempts to max_attempts-1 via ordinary claim/release cycles
    # that never trip the cap.
    for m in range(31, 33):
        claimed = s.claim_due_reminders(_at(m), lease_seconds, max_attempts)
        assert len(claimed) == 1
        s.release_reminder(claimed[0]["id"], max_attempts, claimed[0]["claimed_at"])

    # The final attempt: this claim brings attempts to max_attempts. The
    # owner now holds a live lease (120s) starting at owner_now.
    owner_now = _at(33)
    claimed = s.claim_due_reminders(owner_now, lease_seconds, max_attempts)
    assert len(claimed) == 1
    row = claimed[0]
    assert row["attempts"] == max_attempts
    owner_token = row["claimed_at"]
    assert owner_token == owner_now

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
