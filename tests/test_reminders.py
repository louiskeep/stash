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
    first = s.claim_due_reminders(_at(31), lease_seconds=120)
    second = s.claim_due_reminders(_at(31), lease_seconds=120)
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
