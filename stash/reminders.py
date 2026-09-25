"""Reminder delivery: async, one at a time, attempt-at-send, backoff, at-least-once."""

from datetime import datetime, timezone

_MAX_MSG = 4096


def render_reminder(storage, row) -> str:
    note = storage.get_note(row["note_id"])
    body = note["raw"] if note else ""
    return ("reminder: " + body)[:_MAX_MSG]


async def run_due(storage, delivery, now: str, lease_seconds: int,
                  max_attempts: int) -> int:
    # Terminalize any reminder that hit the attempt cap while stuck
    # 'pending' (its previous tick crashed/hung before deferring it), so
    # claim_one_due_reminder's attempts<max_attempts guard doesn't leave it
    # orphaned forever. Lease-aware: a row still within its lease may be
    # owned by another tick mid-send on its final attempt, so it is left
    # alone here rather than terminalized out from under its owner.
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
            #
            # If this send actually outlived its own lease, a second,
            # overlapping run_due could have reclaimed and delivered this
            # same row before this except-block runs. defer_reminder's CAS
            # then no-ops (claimed_at no longer matches), so it will not
            # flip an already-sent row back to pending or failed here. The
            # daemon serializes scheduler ticks with a lock (see the serve
            # loop, Task 6), so run_due never overlaps with itself in
            # production, which is what keeps this from happening.
            storage.defer_reminder(row["id"], now, max_attempts, token)
        else:
            # Only count this as delivered if the CAS actually applied. If a
            # second, overlapping tick reclaimed and completed this same row
            # first (only possible with overlapping run_due calls, which the
            # daemon's serialize-lock prevents in production), this send
            # still happened -- a real, spec-documented at-least-once
            # duplicate -- but this tick's own mark is a no-op, so it must
            # not add to delivered or the count would overstate what this
            # call actually persisted.
            if storage.mark_reminder_sent(
                    row["id"], datetime.now(timezone.utc).isoformat(), token):
                delivered += 1
    return delivered
