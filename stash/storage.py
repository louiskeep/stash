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

    def claim_due_reminders(self, now, lease_seconds):
        # Exclusive claim in one short transaction. A reminder is claimable if
        # pending, due, and either never claimed or its lease has expired.
        from datetime import datetime, timedelta
        cutoff = (datetime.fromisoformat(now)
                  - timedelta(seconds=lease_seconds)).isoformat()
        with self.conn:
            rows = self.conn.execute(
                "UPDATE reminders SET claimed_at=?, attempts=attempts+1"
                " WHERE id IN ("
                "   SELECT id FROM reminders"
                "   WHERE status='pending' AND fire_at<=?"
                "     AND (claimed_at IS NULL OR claimed_at<?))"
                " RETURNING *",
                (now, now, cutoff),
            ).fetchall()
        return rows

    def mark_reminder_sent(self, rid, when, expected_claimed_at):
        # Lease-owned compare-and-swap: only complete the row if this tick's
        # claim (expected_claimed_at) still holds. A tick whose claim was
        # superseded by a later reclaim updates 0 rows and is a no-op,
        # instead of clobbering a newer tick's completed 'sent' row.
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET status='sent', sent_at=?, claimed_at=NULL"
                " WHERE id=? AND claimed_at=?", (when, rid, expected_claimed_at))

    def release_reminder(self, rid, max_attempts, expected_claimed_at):
        # Same lease-owned compare-and-swap as mark_reminder_sent: a stale
        # tick's release must not resurrect a row another tick already
        # completed.
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET claimed_at=NULL,"
                " status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END"
                " WHERE id=? AND claimed_at=?", (max_attempts, rid, expected_claimed_at))
