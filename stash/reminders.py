"""Reminder delivery: claim under a lease, send outside any transaction,
at-least-once."""

from datetime import datetime, timezone


def render_reminder(row) -> str:
    return f"reminder: {row['note_id']}"  # note text join is a web/telegram concern


def run_due(storage, delivery, now: str, lease_seconds: int,
            max_attempts: int) -> int:
    # Terminalize any reminder that hit the attempt cap while stuck
    # 'pending' (its previous tick crashed/hung before releasing it), so
    # claim_due_reminders' attempts<max_attempts guard doesn't leave it
    # orphaned forever.
    storage.fail_exhausted_reminders(now, max_attempts)
    claimed = storage.claim_due_reminders(now, lease_seconds, max_attempts)
    delivered = 0
    for row in claimed:
        # The batch claim above and this row's send are not atomic: a
        # slow send earlier in this same batch can outlast our lease,
        # letting another tick reclaim (or even complete) THIS row before
        # we reach it. Re-check ownership immediately before sending, and
        # skip -- do not send, do not mark -- rows we no longer own. This
        # closes the batch window. It does not, and cannot, cover the
        # single-row case where one send itself outlives its own lease
        # before failing or succeeding; that residual duplicate is the
        # inherent, spec-documented at-least-once delivery guarantee.
        if storage.reminder_claim_token(row["id"]) != row["claimed_at"]:
            continue
        try:
            delivery.send(row["chat_id"], render_reminder(row))  # outside any txn
        except Exception:
            storage.release_reminder(row["id"], max_attempts, row["claimed_at"])
        else:
            storage.mark_reminder_sent(
                row["id"], datetime.now(timezone.utc).isoformat(), row["claimed_at"])
            delivered += 1
    return delivered
