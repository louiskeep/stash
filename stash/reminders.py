"""Reminder delivery: claim under a lease, send outside any transaction,
at-least-once."""

from datetime import datetime, timezone


def render_reminder(row) -> str:
    return f"reminder: {row['note_id']}"  # note text join is a web/telegram concern


def run_due(storage, delivery, now: str, lease_seconds: int,
            max_attempts: int) -> int:
    claimed = storage.claim_due_reminders(now, lease_seconds)
    delivered = 0
    for row in claimed:
        try:
            delivery.send(row["chat_id"], render_reminder(row))  # outside any txn
        except Exception:
            storage.release_reminder(row["id"], max_attempts)
        else:
            storage.mark_reminder_sent(row["id"], datetime.now(timezone.utc).isoformat())
            delivered += 1
    return delivered
