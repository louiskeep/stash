"""Typed storage operations over the stash SQLite schema.

Transactions are short and never span a network call. FTS and vec tables are
kept in sync here on every note write.
"""

import sqlite3
import struct

from stash.db import connect, migrate


def _pack_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _fts5_match_expr(query: str) -> str:
    """Turn free-form user input into a safe FTS5 MATCH expression.

    Every whitespace-delimited token is quoted as an FTS5 string literal, so
    punctuation and operator keywords (AND/OR/NOT/*, an unbalanced quote or
    paren, ...) are matched as literal text rather than parsed as syntax.
    """
    tokens = query.split()
    quoted = ['"' + tok.replace('"', '""') + '"' for tok in tokens]
    return " ".join(quoted)


class Storage:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, db_path: str, busy_timeout_ms: int = 5000) -> "Storage":
        conn = connect(db_path, busy_timeout_ms)
        migrate(conn)
        return cls(conn)

    def add_note(self, raw, source, created_at,
                 source_chat_id=None, source_msg_id=None) -> int | None:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO notes(raw, source, source_chat_id, source_msg_id,"
                    " created_at, derived_at) VALUES (?,?,?,?,?,NULL)",
                    (raw, source, source_chat_id, source_msg_id, created_at),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return None  # scoped dedupe key already present
            raise

    def get_note(self, note_id) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()

    def set_tags(self, note_id, tags) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
            self.conn.executemany(
                "INSERT INTO note_tags(note_id, tag) VALUES (?,?)",
                [(note_id, t) for t in tags],
            )

    def set_meta(self, note_id, category, intent, cluster_id=None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO note_meta(note_id, category, intent, cluster_id)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(note_id) DO UPDATE SET"
                " category=excluded.category, intent=excluded.intent,"
                " cluster_id=excluded.cluster_id",
                (note_id, category, intent, cluster_id),
            )

    def set_embedding(self, note_id, vector) -> None:
        row = self.get_note(note_id)
        with self.conn:
            # FTS external content: insert by rowid = note id. Guard the
            # delete on a prior-index check: fts5's implicit-rowid DELETE
            # reads the *current* backing row to work out which terms to
            # remove, so issuing it before anything was ever indexed for
            # this rowid corrupts the shadow index instead of no-op'ing.
            indexed = self.conn.execute(
                "SELECT 1 FROM notes_fts_docsize WHERE id=?", (note_id,)
            ).fetchone()
            if indexed:
                self.conn.execute("DELETE FROM notes_fts WHERE rowid=?", (note_id,))
            self.conn.execute(
                "INSERT INTO notes_fts(rowid, raw) VALUES (?,?)",
                (note_id, row["raw"]),
            )
            self.conn.execute("DELETE FROM vec_notes WHERE note_id=?", (note_id,))
            self.conn.execute(
                "INSERT INTO vec_notes(note_id, embedding) VALUES (?,?)",
                (note_id, _pack_f32(vector)),
            )

    def mark_derived(self, note_id, when) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET derived_at=? WHERE id=?", (when, note_id))

    def underived_note_ids(self) -> list[int]:
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM notes WHERE derived_at IS NULL ORDER BY id")]

    def kv_get(self, key) -> str | None:
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def kv_set(self, key, value) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO kv(key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def search_bm25(self, query, limit) -> list[int]:
        if not query.strip():
            return []
        match_expr = _fts5_match_expr(query)
        if not match_expr:
            return []
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?"
                " ORDER BY bm25(notes_fts) LIMIT ?",
                (match_expr, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["rowid"] for r in rows]

    def search_vec(self, vector, limit) -> list[int]:
        rows = self.conn.execute(
            "SELECT note_id FROM vec_notes"
            " WHERE embedding MATCH ? AND k=? ORDER BY distance",
            (_pack_f32(vector), limit),
        ).fetchall()
        return [r["note_id"] for r in rows]

    def claim_one_due_reminder(self, now, lease_seconds, max_attempts):
        # Claim exactly one reminder per call: pending, due, under the
        # attempt cap, and either never claimed or its lease has expired.
        # One-at-a-time (vs. M1's batch claim) closes the window where a
        # tick could hold several already-claimed-but-not-yet-sent rows
        # while a nested/concurrent tick reclaims and delivers them out
        # from under it -- the next row is only claimed once the current
        # one is fully resolved (sent or deferred). Does NOT increment
        # attempts; that happens at send time via record_attempt.
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
        # Count the attempt durably, ownership-checked, BEFORE the caller
        # awaits delivery.send. This is what bounds a crash-loop: even if
        # the process dies mid-send and never reaches defer_reminder, the
        # attempt is already committed, so a later tick's reclaim continues
        # counting toward max_attempts instead of restarting from zero.
        with self.conn:
            cur = self.conn.execute(
                "UPDATE reminders SET attempts=attempts+1"
                " WHERE id=? AND claimed_at=?",
                (rid, expected_claimed_at))
            return cur.rowcount == 1

    def defer_reminder(self, rid, now, max_attempts, expected_claimed_at) -> None:
        # On a failed send: CAS on claimed_at (a stale tick's defer must not
        # clobber a row another tick has since reclaimed or completed). If
        # the row is now at the attempt cap, terminalize it. Otherwise leave
        # it LEASED (claimed_at=now) rather than releasing it outright --
        # that fresh lease window doubles as the retry backoff, so the
        # current tick's own drain loop (and any tick before the lease
        # elapses) will not immediately re-claim and re-attempt it. This is
        # what stops a single-tick outage from burning through the whole
        # attempt cap in one pass.
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET"
                " status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,"
                " claimed_at=CASE WHEN attempts>=? THEN NULL ELSE ? END"
                " WHERE id=? AND claimed_at=?",
                (max_attempts, max_attempts, now, rid, expected_claimed_at))

    def reminder_claim_token(self, rid) -> str | None:
        # Current claimed_at for a reminder, or None if it has none (never
        # claimed, or already completed/reclaimed). run_due compares this
        # against the token a tick claimed a row with, immediately before
        # sending, to detect that another tick has since reclaimed or
        # completed the row.
        row = self.conn.execute(
            "SELECT claimed_at FROM reminders WHERE id=?", (rid,)).fetchone()
        return row["claimed_at"] if row else None

    def fail_exhausted_reminders(self, now, max_attempts, lease_seconds):
        # Terminalize reminders that hit the attempt cap while still
        # 'pending' (e.g. a tick that recorded the final attempt but
        # crashed/hung before calling defer_reminder), so they stop
        # lingering as reclaimable forever once claim_one_due_reminder
        # starts excluding attempts>=cap.
        #
        # Lease-aware, mirroring claim_one_due_reminder's own guard: a row whose
        # lease is still live is owned by some tick that may be mid-send on
        # its final attempt. Without this guard, calling this at the start
        # of a concurrent tick could terminalize that row out from under its
        # owner -- flipping it to 'failed' and clearing claimed_at -- so the
        # owner's later mark_reminder_sent CAS (WHERE claimed_at=<token>) no
        # longer matches and silently no-ops, leaving a delivered reminder
        # recorded as 'failed'. Only rows that are unowned (never claimed)
        # or whose lease has actually expired are fair game here.
        from datetime import datetime, timedelta
        cutoff = (datetime.fromisoformat(now)
                  - timedelta(seconds=lease_seconds)).isoformat()
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET status='failed', claimed_at=NULL"
                " WHERE status='pending' AND fire_at<=? AND attempts>=?"
                "   AND (claimed_at IS NULL OR claimed_at<?)",
                (now, max_attempts, cutoff),
            )

    def mark_reminder_sent(self, rid, when, expected_claimed_at):
        # Lease-owned compare-and-swap: only complete the row if this tick's
        # claim (expected_claimed_at) still holds. A tick whose claim was
        # superseded by a later reclaim updates 0 rows and is a no-op,
        # instead of clobbering a newer tick's completed 'sent' row.
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET status='sent', sent_at=?, claimed_at=NULL"
                " WHERE id=? AND claimed_at=?", (when, rid, expected_claimed_at))
